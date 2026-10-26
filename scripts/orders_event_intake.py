"""EventBridge S3 Object Created -> one idempotent Airflow orders DagRun.

Run ``consume`` as a separate SQS long-poll worker, or ``replay`` with an
explicit feed/key/version. No event field supplies an API URL, bucket, DAG ID
or token. This module does not import Airflow or the backend graph.
"""

import argparse
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlparse

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.object_orders_feed import CONFIG, batch_id_for_object, object_spec
from scripts.orders_loader import MAX_BYTES

log = logging.getLogger("orders_event_intake")
DAG_ID = "versioned_orders_to_agent"
MAX_EVENT_BYTES = 64 * 1024
ACCOUNT = re.compile(r"^[0-9]{12}$")
REASONS = {"PutObject", "POST Object", "CopyObject", "CompleteMultipartUpload"}


class IntakeError(ValueError):
    """Event/identity rejected before an Airflow DAG can be scheduled."""


def _object_pairs_unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise IntakeError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def parse_event_body(body: str) -> dict:
    if not isinstance(body, str) or len(body.encode("utf-8")) > MAX_EVENT_BYTES:
        raise IntakeError("event body is missing or exceeds 64 KiB")
    try:
        event = json.loads(body, object_pairs_hook=_object_pairs_unique)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise IntakeError("event body is not valid JSON") from exc
    if not isinstance(event, dict):
        raise IntakeError("event must be a single EventBridge object")
    return event


def configured_origin() -> tuple[str, str]:
    account = os.environ.get("P7_EXPECTED_ACCOUNT_ID", "")
    region = os.environ.get("P7_EXPECTED_REGION", "")
    if not ACCOUNT.fullmatch(account) or not re.fullmatch(r"[a-z]{2}(?:-[a-z0-9]+)+-[0-9]+", region):
        raise IntakeError("P7_EXPECTED_ACCOUNT_ID and P7_EXPECTED_REGION must be configured")
    return account, region


def identity_for_object(feed_id: str, key: str, version_id: str) -> dict:
    try:
        spec = object_spec(feed_id, key, version_id)
    except ValueError as exc:
        raise IntakeError(str(exc)) from exc
    batch_id = batch_id_for_object(feed_id, spec["bucket"], key, version_id)
    return {"feed_id": feed_id, "bucket": spec["bucket"], "object_key": key,
            "version_id": version_id, "batch_id": batch_id}


def identity_from_event(event: dict, account: str, region: str) -> dict:
    if not ACCOUNT.fullmatch(account) or not region:
        raise IntakeError("expected account and region are required")
    if (event.get("version") != "0" or event.get("source") != "aws.s3" or
            event.get("detail-type") != "Object Created" or
            event.get("account") != account or event.get("region") != region):
        raise IntakeError("event envelope is not an allowed S3 Object Created event")
    detail = event.get("detail")
    if not isinstance(detail, dict):
        raise IntakeError("event detail is missing")
    event_version = detail.get("event-version")
    match = re.fullmatch(r"([0-9]+)\.([0-9]+)", event_version) if isinstance(event_version, str) else None
    if not match or int(match.group(1)) != 1:
        raise IntakeError("unsupported S3 EventBridge schema version")
    if detail.get("version") != "0" or detail.get("reason") not in REASONS:
        raise IntakeError("unsupported object creation reason")
    bucket_info, object_info = detail.get("bucket"), detail.get("object")
    if not isinstance(bucket_info, dict) or not isinstance(object_info, dict):
        raise IntakeError("event bucket or object is missing")
    bucket, key, version_id = (bucket_info.get("name"), object_info.get("key"),
                               object_info.get("version-id"))
    if (not isinstance(bucket, str) or not isinstance(key, str) or
            not isinstance(version_id, str)):
        raise IntakeError("event lacks an exact object version")
    if event.get("resources") != [f"arn:aws:s3:::{bucket}"]:
        raise IntakeError("event bucket ARN does not match bucket name")
    size = object_info.get("size")
    if size is not None and (type(size) is not int or size < 0 or size > MAX_BYTES):
        raise IntakeError("event object size is invalid or exceeds the batch cap")
    registry = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    allowed = []
    for feed_id in (registry.get("feeds") or {}):
        try:
            identity = identity_for_object(feed_id, key, version_id)
        except IntakeError:
            continue
        if identity["bucket"] == bucket:
            allowed.append(identity)
    if len(allowed) != 1:
        raise IntakeError("event object is outside one allowlisted feed and bucket")
    return allowed[0]


def dag_run_id(identity: dict) -> str:
    # Shares P6's exact object tuple; the EventBridge UUID is deliberately
    # excluded so duplicate notifications and operator replay converge.
    return "s3v_" + identity["batch_id"][4:]


def dag_conf(identity: dict) -> dict:
    return {"feed_id": identity["feed_id"], "object_key": identity["object_key"],
            "version_id": identity["version_id"]}


def _verified_existing(client: httpx.Client, run_id: str, conf: dict) -> dict:
    path = f"/api/v2/dags/{DAG_ID}/dagRuns/{run_id}"
    for attempt in range(3):
        response = client.get(path)
        if response.status_code == 404 and attempt < 2:
            time.sleep(0.25)
            continue
        response.raise_for_status()
        existing = response.json()
        if existing.get("dag_run_id") != run_id or existing.get("conf") != conf:
            raise IntakeError("Airflow DagRun ID exists with different object identity")
        return existing
    raise IntakeError("Airflow reported a duplicate DagRun that is not readable")


def dispatch_identity(client: httpx.Client, identity: dict) -> dict:
    # Replay callers also pass through object_spec; never trust arbitrary
    # hand-built conf or a client-supplied DagRun ID.
    checked = identity_for_object(identity["feed_id"], identity["object_key"], identity["version_id"])
    if checked != identity:
        raise IntakeError("object identity differs from configured feed")
    run_id, conf = dag_run_id(checked), dag_conf(checked)
    response = client.post(f"/api/v2/dags/{DAG_ID}/dagRuns",
                           json={"dag_run_id": run_id, "logical_date": None, "conf": conf})
    if response.status_code == 409:
        _verified_existing(client, run_id, conf)
        return {"dag_run_id": run_id, "result": "already_exists", "batch_id": checked["batch_id"]}
    response.raise_for_status()
    # Read back even after a successful POST; only an exact saved conf may
    # cause an SQS acknowledgment.
    _verified_existing(client, run_id, conf)
    return {"dag_run_id": run_id, "result": "created", "batch_id": checked["batch_id"]}


def process_sqs_message(sqs, queue_url: str, message: dict,
                        client: httpx.Client, account: str, region: str) -> dict:
    receipt = message.get("ReceiptHandle")
    if not isinstance(receipt, str) or not receipt:
        raise IntakeError("SQS message lacks a receipt handle")
    event = parse_event_body(message.get("Body"))
    identity = identity_from_event(event, account, region)
    result = dispatch_identity(client, identity)
    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
    return result


def _https_url(value: str, name: str) -> str:
    parsed = urlparse(value)
    if (parsed.scheme != "https" or not parsed.netloc or parsed.username or
            parsed.password or parsed.query or parsed.fragment):
        raise IntakeError(f"{name} must be an HTTPS URL without credentials or query")
    return value.rstrip("/")


def airflow_client() -> httpx.Client:
    url = _https_url(os.environ.get("P7_AIRFLOW_API_URL", ""), "P7_AIRFLOW_API_URL")
    token = os.environ.get("P7_AIRFLOW_BEARER_TOKEN", "")
    if not token:
        raise IntakeError("P7_AIRFLOW_BEARER_TOKEN is required")
    return httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=15)


def consume_forever(client: httpx.Client, sqs=None) -> None:
    queue_url = _https_url(os.environ.get("P7_SQS_QUEUE_URL", ""), "P7_SQS_QUEUE_URL")
    account, region = configured_origin()
    if sqs is None:
        import boto3  # optional bridge dependency, not imported by the backend
        sqs = boto3.client("sqs", region_name=region)
    while True:
        response = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1,
                                       WaitTimeSeconds=20, VisibilityTimeout=120)
        for message in response.get("Messages", []):
            try:
                result = process_sqs_message(sqs, queue_url, message, client, account, region)
                log.info("accepted object DagRun %s (%s)", result["dag_run_id"], result["result"])
            except Exception:
                # Leave unacknowledged so queue redrive sends the message to
                # its configured DLQ. Never auto-publish a rejected object.
                log.exception("object event rejected or Airflow dispatch failed; message remains queued")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("consume", help="long-poll the configured SQS queue")
    replay = sub.add_parser("replay", help="dispatch one explicit object version")
    replay.add_argument("--feed", required=True)
    replay.add_argument("--key", required=True)
    replay.add_argument("--version", required=True)
    replay_event = sub.add_parser("replay-event", help="redispatch one saved EventBridge JSON file")
    replay_event.add_argument("event_file", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with airflow_client() as client:
        if args.action == "consume":
            consume_forever(client)
        elif args.action == "replay":
            result = dispatch_identity(client, identity_for_object(args.feed, args.key, args.version))
            print(result)
        else:
            account, region = configured_origin()
            event = parse_event_body(args.event_file.read_text(encoding="utf-8"))
            result = dispatch_identity(client, identity_from_event(event, account, region))
            print(result)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (IntakeError, httpx.HTTPError) as exc:
        print(f"orders event intake failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
