"""Offline EventBridge/SQS intake, exact DagRun identity and operator replay."""

from copy import deepcopy
import json
import os
import sys

import pytest

from scripts import orders_event_intake as intake

ACCOUNT = "111122223333"
REGION = "us-east-1"
BUCKET = "orders-p7-test"
KEY = "incoming/partner_orders_a/orders.csv"


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("P6_ORDERS_BUCKET", BUCKET)


def event(version="version-one", key=KEY, event_id="event-one"):
    return {"version": "0", "id": event_id, "detail-type": "Object Created",
            "source": "aws.s3", "account": ACCOUNT, "region": REGION,
            "resources": [f"arn:aws:s3:::{BUCKET}"],
            "detail": {"version": "0", "event-version": "1.2",
                       "bucket": {"name": BUCKET},
                       "object": {"key": key, "version-id": version, "size": 100},
                       "reason": "PutObject"}}


class Response:
    def __init__(self, status, payload=None): self.status_code, self.payload = status, payload or {}
    def raise_for_status(self):
        if self.status_code >= 400:
            raise ValueError(f"HTTP {self.status_code}")
    def json(self): return self.payload


class Airflow:
    def __init__(self):
        self.runs = {}
        self.posts = []
        self.gets = []
        self.conflict_payload = None
    def post(self, path, json):
        self.posts.append((path, json))
        run_id = json["dag_run_id"]
        if run_id in self.runs:
            return Response(409)
        self.runs[run_id] = {"dag_run_id": run_id, "conf": json["conf"]}
        return Response(200, self.runs[run_id])
    def get(self, path):
        self.gets.append(path)
        run_id = path.rsplit("/", 1)[-1]
        if self.conflict_payload is not None:
            return Response(200, self.conflict_payload)
        return Response(200, self.runs[run_id]) if run_id in self.runs else Response(404)


class Queue:
    def __init__(self): self.deletes = []
    def delete_message(self, **kwargs): self.deletes.append(kwargs)


def test_duplicate_events_and_manual_replay_converge_to_one_dagrun():
    first = intake.identity_from_event(event(), ACCOUNT, REGION)
    duplicate = intake.identity_from_event(event(event_id="different-uuid"), ACCOUNT, REGION)
    manual = intake.identity_for_object("partner_orders_a", KEY, "version-one")
    assert first == duplicate == manual
    airflow = Airflow()
    created = intake.dispatch_identity(airflow, first)
    again = intake.dispatch_identity(airflow, duplicate)
    replay = intake.dispatch_identity(airflow, manual)
    assert created["dag_run_id"] == again["dag_run_id"] == replay["dag_run_id"]
    assert [created["result"], again["result"], replay["result"]] == [
        "created", "already_exists", "already_exists"]
    assert airflow.posts[0][0] == "/api/v2/dags/versioned_orders_to_agent/dagRuns"
    assert airflow.posts[0][1]["conf"] == {"feed_id": "partner_orders_a",
                                            "object_key": KEY, "version_id": "version-one"}
    assert airflow.posts[0][1]["logical_date"] is None
    assert intake.dag_run_id(first) != intake.dag_run_id(
        intake.identity_from_event(event(version="version-two"), ACCOUNT, REGION))


def test_conflicting_airflow_409_is_never_acknowledged():
    identity = intake.identity_from_event(event(), ACCOUNT, REGION)
    airflow = Airflow()
    run_id = intake.dag_run_id(identity)
    airflow.runs[run_id] = {"dag_run_id": run_id,
                            "conf": {"feed_id": "partner_orders_b", "object_key": KEY,
                                     "version_id": "version-one"}}
    with pytest.raises(intake.IntakeError, match="different object identity"):
        intake.dispatch_identity(airflow, identity)


@pytest.mark.parametrize("mutate", [
    lambda e: e.update({"source": "attacker"}),
    lambda e: e.update({"detail-type": "Object Deleted"}),
    lambda e: e.update({"account": "999900001111"}),
    lambda e: e.update({"region": "eu-west-1"}),
    lambda e: e.update({"resources": ["arn:aws:s3:::wrong-bucket"]}),
    lambda e: e["detail"]["bucket"].update({"name": "wrong-bucket"}),
    lambda e: e["detail"]["object"].update({"version-id": "null"}),
    lambda e: e["detail"]["object"].pop("version-id"),
    lambda e: e["detail"]["object"].update({"key": "unregistered/x.csv"}),
    lambda e: e["detail"].update({"reason": "DeleteObject"}),
    lambda e: e["detail"].update({"event-version": "2.0"}),
    lambda e: e["detail"]["object"].update({"size": 11 * 1024 * 1024}),
])
def test_disallowed_event_never_reaches_airflow(mutate):
    candidate = event()
    mutate(candidate)
    with pytest.raises(intake.IntakeError):
        intake.identity_from_event(candidate, ACCOUNT, REGION)


def test_event_json_rejects_duplicates_oversize_and_non_event():
    with pytest.raises(intake.IntakeError, match="duplicate JSON"):
        intake.parse_event_body('{"source":"aws.s3","source":"attacker"}')
    with pytest.raises(intake.IntakeError, match="64 KiB"):
        intake.parse_event_body("x" * (64 * 1024 + 1))
    with pytest.raises(intake.IntakeError, match="single EventBridge"):
        intake.parse_event_body("[]")
    with pytest.raises(intake.IntakeError):
        intake.identity_from_event({"Records": []}, ACCOUNT, REGION)


def test_sqs_delete_only_after_verified_airflow_acceptance():
    queue, airflow = Queue(), Airflow()
    message = {"Body": json.dumps(event()), "ReceiptHandle": "receipt-1"}
    first = intake.process_sqs_message(queue, "https://sqs.example/queue", message,
                                       airflow, ACCOUNT, REGION)
    second = intake.process_sqs_message(queue, "https://sqs.example/queue", message,
                                        airflow, ACCOUNT, REGION)
    assert [first["result"], second["result"]] == ["created", "already_exists"]
    assert len(queue.deletes) == 2
    bad = deepcopy(message)
    bad["Body"] = json.dumps(event(version="null"))
    with pytest.raises(intake.IntakeError):
        intake.process_sqs_message(queue, "https://sqs.example/queue", bad,
                                   airflow, ACCOUNT, REGION)
    assert len(queue.deletes) == 2
    airflow.conflict_payload = {"dag_run_id": first["dag_run_id"], "conf": {"version_id": "wrong"}}
    with pytest.raises(intake.IntakeError):
        intake.process_sqs_message(queue, "https://sqs.example/queue", message,
                                   airflow, ACCOUNT, REGION)
    assert len(queue.deletes) == 2


def test_airflow_failure_or_unreadable_conf_keeps_sqs_message(monkeypatch):
    queue = Queue()
    message = {"Body": json.dumps(event()), "ReceiptHandle": "receipt-1"}
    class Unavailable(Airflow):
        def post(self, *_args, **_kwargs): return Response(503)
    with pytest.raises(ValueError, match="HTTP 503"):
        intake.process_sqs_message(queue, "https://sqs.example/queue", message,
                                   Unavailable(), ACCOUNT, REGION)
    assert queue.deletes == []
    identity = intake.identity_from_event(event(), ACCOUNT, REGION)
    class Unreadable(Airflow):
        def post(self, *_args, **_kwargs): return Response(409)
        def get(self, *_args, **_kwargs): return Response(404)
    monkeypatch.setattr(intake.time, "sleep", lambda *_: None)
    with pytest.raises(ValueError, match="HTTP 404"):
        intake.dispatch_identity(Unreadable(), identity)
    assert queue.deletes == []


def test_replay_cli_uses_same_identity_and_requires_exact_version(monkeypatch, capsys):
    airflow = Airflow()
    class Context:
        def __enter__(self): return airflow
        def __exit__(self, *_): pass
    monkeypatch.setattr(intake, "airflow_client", lambda: Context())
    monkeypatch.setattr(sys, "argv", ["orders_event_intake.py", "replay", "--feed",
        "partner_orders_a", "--key", KEY, "--version", "version-one"])
    assert intake.main() == 0
    assert "created" in capsys.readouterr().out
    assert len(airflow.runs) == 1
    monkeypatch.setattr(sys, "argv", ["orders_event_intake.py", "replay", "--feed",
        "partner_orders_a", "--key", KEY, "--version", "version-one"])
    assert intake.main() == 0
    assert "already_exists" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["orders_event_intake.py", "replay", "--feed",
        "partner_orders_a", "--key", KEY, "--version", "null"])
    with pytest.raises(intake.IntakeError):
        intake.main()
    assert len(airflow.runs) == 1


def test_replay_saved_event_uses_same_dagrun(monkeypatch, tmp_path):
    airflow = Airflow()
    class Context:
        def __enter__(self): return airflow
        def __exit__(self, *_): pass
    monkeypatch.setattr(intake, "airflow_client", lambda: Context())
    monkeypatch.setenv("P7_EXPECTED_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("P7_EXPECTED_REGION", REGION)
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event()), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["orders_event_intake.py", "replay-event", str(path)])
    assert intake.main() == 0
    assert list(airflow.runs) == [intake.dag_run_id(intake.identity_from_event(event(), ACCOUNT, REGION))]


def test_sqs_long_poll_acks_only_verified_run(monkeypatch):
    monkeypatch.setenv("P7_SQS_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/111122223333/orders")
    monkeypatch.setenv("P7_EXPECTED_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("P7_EXPECTED_REGION", REGION)
    class OneMessage(Queue):
        def __init__(self): super().__init__(); self.polls = []
        def receive_message(self, **kwargs):
            self.polls.append(kwargs)
            if len(self.polls) > 1:
                raise StopIteration
            return {"Messages": [{"Body": json.dumps(event()), "ReceiptHandle": "r1"}]}
    queue = OneMessage()
    with pytest.raises(StopIteration):
        intake.consume_forever(Airflow(), queue)
    assert queue.deletes == [{"QueueUrl": os.environ["P7_SQS_QUEUE_URL"], "ReceiptHandle": "r1"}]
    assert queue.polls[0]["WaitTimeSeconds"] == 20
    assert queue.polls[0]["VisibilityTimeout"] == 120


def test_bridge_requires_https_and_configured_origin(monkeypatch):
    monkeypatch.setenv("P7_EXPECTED_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("P7_EXPECTED_REGION", REGION)
    assert intake.configured_origin() == (ACCOUNT, REGION)
    monkeypatch.setenv("P7_AIRFLOW_API_URL", "http://localhost:8080")
    monkeypatch.setenv("P7_AIRFLOW_BEARER_TOKEN", "token")
    with pytest.raises(intake.IntakeError, match="HTTPS"):
        intake.airflow_client()
    monkeypatch.setenv("P7_EXPECTED_ACCOUNT_ID", "wrong")
    with pytest.raises(intake.IntakeError, match="configured"):
        intake.configured_origin()
