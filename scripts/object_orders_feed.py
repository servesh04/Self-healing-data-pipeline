"""Version-pinned S3-compatible orders input for the independent P4 loader.

Only an allowlisted feed, key prefix and explicit non-null object version are
accepted. The exact fetched bytes are staged and retried from Postgres; the
object store is never reread to publish after human approval.
"""

from pathlib import Path
import hashlib
import os
import re
from urllib.parse import urlparse

import httpx
import yaml

from scripts.orders_loader import MAX_BYTES, _id, publish, stage

CONFIG = Path(__file__).resolve().parents[1] / "config" / "object_feeds.yaml"
BRIDGE_FEEDS = CONFIG.with_name("external_feeds.yaml")
BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
CHUNK = 64 * 1024


def object_spec(feed_id: str, key: str, version_id: str, config_path: Path = CONFIG) -> dict:
    _id(feed_id)
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    feeds = config.get("feeds") or {}
    item = feeds.get(feed_id)
    if not isinstance(item, dict):
        raise ValueError("unknown object orders feed")
    bridge = yaml.safe_load(BRIDGE_FEEDS.read_text(encoding="utf-8")) or {}
    if feed_id not in (bridge.get("feeds") or {}):
        raise ValueError("object feed is not registered with the agent bridge")
    prefix = item.get("key_prefix")
    if (not isinstance(prefix, str) or not prefix.endswith("/") or
            not isinstance(key, str) or not key.startswith(prefix) or
            not key.endswith(".csv") or len(key.encode("utf-8")) > 1024 or
            any(c == "\\" or ord(c) < 32 or ord(c) == 127 for c in key) or
            any(part in {"", ".", ".."} for part in key.split("/"))):
        raise ValueError("object key is outside the allowlisted CSV prefix")
    if (not isinstance(version_id, str) or not version_id or version_id.lower() == "null" or
            len(version_id) > 1024 or any(ord(c) < 33 or ord(c) > 126 for c in version_id)):
        raise ValueError("explicit immutable object version ID is required")
    bucket_env = item.get("bucket_env")
    if bucket_env != "P6_ORDERS_BUCKET":
        raise ValueError("object bucket configuration is unsupported")
    bucket = os.environ.get(bucket_env, "")
    if not BUCKET.fullmatch(bucket):
        raise ValueError("P6_ORDERS_BUCKET must be a configured bucket name")
    return {"bucket": bucket, "key": key, "version_id": version_id}


def batch_id_for_object(feed_id: str, bucket: str, key: str, version_id: str) -> str:
    # 4 + 60 characters fits the existing batch ID limit of 64.
    identity = "\0".join((feed_id, bucket, key, version_id)).encode("utf-8")
    return "obj_" + hashlib.sha256(identity).hexdigest()[:60]


def s3_client():
    # boto3 is an optional loader/Airflow dependency, never a backend import.
    import boto3
    endpoint = os.environ.get("P6_S3_ENDPOINT_URL") or None
    if endpoint:
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("P6_S3_ENDPOINT_URL must be an HTTPS endpoint without user info")
    return boto3.client("s3", endpoint_url=endpoint,
                        region_name=os.environ.get("P6_S3_REGION") or None)


def fetch_versioned_object(feed_id: str, key: str, version_id: str, client=None) -> tuple[bytes, dict]:
    spec = object_spec(feed_id, key, version_id)
    client = client or s3_client()
    response = client.get_object(Bucket=spec["bucket"], Key=key, VersionId=version_id)
    body = response["Body"]
    try:
        if response.get("DeleteMarker") or response.get("VersionId") != version_id:
            raise ValueError("object store did not return the requested immutable version")
        length = response.get("ContentLength")
        if length is not None and length > MAX_BYTES:
            raise ValueError("object exceeds the staged byte cap")
        chunks = []
        size = 0
        while True:
            chunk = body.read(min(CHUNK, MAX_BYTES + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError("object exceeds the staged byte cap")
            chunks.append(chunk)
        payload = b"".join(chunks)
        if length is not None and len(payload) != length:
            raise ValueError("object length changed during read")
        return payload, spec
    finally:
        body.close()


def stage_object(conn, feed_id: str, key: str, version_id: str, client=None) -> dict:
    payload, spec = fetch_versioned_object(feed_id, key, version_id, client)
    batch_id = batch_id_for_object(feed_id, spec["bucket"], key, version_id)
    staged = stage(conn, feed_id, batch_id, payload, provenance=spec)
    return {"feed_id": feed_id, "batch_id": batch_id, "sha256": staged["sha256"],
            "row_count": staged["row_count"], "object": spec}


def saved_object_stage(conn, feed_id: str, key: str, version_id: str,
                       *, missing_ok: bool = False) -> dict | None:
    """Resume solely from persisted bytes; never re-fetch a changed object."""
    spec = object_spec(feed_id, key, version_id)
    batch_id = batch_id_for_object(feed_id, spec["bucket"], key, version_id)
    row = conn.execute("""select payload, sha256, row_count, object_bucket,
        object_key, object_version_id from external_order_batches
        where feed_id = %s and batch_id = %s""", (feed_id, batch_id)).fetchone()
    if row is None and missing_ok:
        return None
    if (not row or row["object_bucket"] != spec["bucket"] or
            row["object_key"] != key or row["object_version_id"] != version_id or
            hashlib.sha256(bytes(row["payload"])).hexdigest() != row["sha256"]):
        raise ValueError("saved stage differs from exact object identity or digest")
    return {"feed_id": feed_id, "batch_id": batch_id, "sha256": row["sha256"],
            "row_count": row["row_count"], "object": spec}


def stage_or_resume_object(conn, feed_id: str, key: str, version_id: str, client=None) -> dict:
    """Airflow task retries use the durable stage instead of another S3 GET."""
    saved = saved_object_stage(conn, feed_id, key, version_id, missing_ok=True)
    return saved if saved is not None else stage_object(conn, feed_id, key, version_id, client)


def trigger_agent(client: httpx.Client, staged: dict) -> str:
    response = client.post(f"/api/external/orders/{staged['feed_id']}/{staged['batch_id']}/trigger")
    response.raise_for_status()
    run_id = response.json()["run_id"]
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("agent returned no run ID")
    return run_id


def batch_status(client: httpx.Client, staged: dict, run_id: str) -> str:
    response = client.get(f"/api/external/orders/{staged['feed_id']}/{staged['batch_id']}/status")
    # The run record can exist before the first graph checkpoint.
    if response.status_code == 404:
        return "running"
    response.raise_for_status()
    result = response.json()
    if result.get("run_id") != run_id:
        raise ValueError("agent status run ID differs from staged batch")
    status = result.get("api_status")
    if status not in {"running", "awaiting_approval", "clean", "healed", "alert", "escalated"}:
        raise ValueError("agent returned unknown batch status")
    return status


def publish_batch(conn, client: httpx.Client, staged: dict, run_id: str) -> dict:
    status = batch_status(client, staged, run_id)
    if status not in {"clean", "healed"}:
        raise ValueError(f"agent blocked publish: {status}")
    response = client.post(f"/api/external/orders/{staged['feed_id']}/{staged['batch_id']}/release")
    response.raise_for_status()
    manifest = response.json()
    if manifest.get("run_id") != run_id or manifest.get("sha256") != staged["sha256"]:
        raise ValueError("release differs from staged object run or digest")
    result = publish(conn, staged["feed_id"], staged["batch_id"], manifest)
    conn.commit()
    return result
