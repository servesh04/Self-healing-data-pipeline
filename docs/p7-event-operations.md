# P7 event-driven orders intake

P7 adds a separate SQS long-poll bridge from an **EventBridge S3 Object
Created** event to the existing `versioned_orders_to_agent` Airflow 3 DAG.
The bridge does not read S3 bytes or publish orders. The DAG and P6 loader
still fetch the exact object version, stage its immutable bytes, wait for
approval, and publish only after validation.

## Event and identity

The accepted message body is one EventBridge JSON event with `source=aws.s3`,
`detail-type=Object Created`, a supported `detail.event-version` major,
an allowlisted creation reason, configured 12-digit account and region,
matching bucket ARN/name, a key in exactly one named feed prefix, and a
non-null `detail.object.version-id`. The P6 allowlist resolves the bucket
from `P6_ORDERS_BUCKET`; neither the event nor replay CLI can supply a
bucket, API URL, DAG ID, or latest-version fallback. The bridge treats the
EventBridge `detail.object.key` as the key value it receives; it does not
URL-decode it as an older direct S3 notification format. Direct `Records`
notifications, SNS envelopes, deletes, restore events, and test events
fail closed and should be inspected from the DLQ.

The DagRun ID is `s3v_` plus the P6 hash of feed, bucket, key and version.
An EventBridge event UUID, delivery attempt, or event time does not affect
it. The POST to Airflow 3's
`/api/v2/dags/versioned_orders_to_agent/dagRuns` contains only that ID,
`logical_date: null`, and the existing three-field DAG conf. The bridge
GETs the saved DagRun after POST. On HTTP 409 it also GETs the existing
DagRun and accepts only an exact ID and conf match. A conflict, unreadable
run, or transport failure leaves the SQS message unacknowledged. Once
Airflow has accepted the exact DagRun, the message is deleted. Later DAG
failure is handled in Airflow; another SQS delivery does not create a new
run. See the [official EventBridge S3 payload](https://docs.aws.amazon.com/AmazonS3/latest/userguide/ev-events.html)
and [Airflow DagRun REST API](https://airflow.apache.org/docs/apache-airflow/3.1.6/stable-rest-api-ref.html).
S3 notifications can arrive out of order; each explicit version is treated
as its own batch. Concurrent versions of one feed may contend on that feed's
mapping compare-and-swap, which safely escalates a stale patch for review.

## Separate bridge process

Install the P6 loader dependencies from
`scripts/requirements-p6-loader.txt` in the bridge environment. Provide
SQS access through the standard AWS credential chain or workload identity;
do not put credentials in the event body. Configure:

| Variable | Purpose |
|---|---|
| `P6_ORDERS_BUCKET` | Exact allowlisted bucket shared with P6 loader |
| `P7_EXPECTED_ACCOUNT_ID` | Expected 12-digit bucket-owner account |
| `P7_EXPECTED_REGION` | Expected event and SQS region |
| `P7_SQS_QUEUE_URL` | HTTPS SQS queue URL, process configuration only |
| `P7_AIRFLOW_API_URL` | HTTPS Airflow API origin |
| `P7_AIRFLOW_BEARER_TOKEN` | Credential allowed to create/read this DAG's runs |

Run under a process supervisor:

```powershell
python scripts/orders_event_intake.py consume
```

The bridge long-polls one SQS message at a time with a 120-second
visibility timeout. Configure the queue policy to allow `SendMessage` only
from the intended EventBridge rule/source account, and grant the bridge
only `ReceiveMessage`, `DeleteMessage`, and visibility-related access to
that queue. Use an SQS redrive policy with a DLQ for rejected events and
repeated Airflow failures. Keep the visibility timeout longer than the
worst-case Airflow POST plus readback and network retries; adjust the code
and queue setting together if that budget changes. Configure the Airflow
API credential for only this DAG's trigger/read operations. The intake
does not require S3 GetObjectVersion or database privileges; those remain
with the separate P6 loader tasks.

An invalid event or Airflow failure is logged and left in the queue for
retry/DLQ. The bridge deletes only after Airflow readback confirms the
same object identity. SQS deletion failure can redeliver a successfully
created DagRun; the deterministic ID makes that redelivery safe. Do not
manually delete DLQ entries before deciding whether to reject or replay.

## Operator replay

From an approved operator environment with the same Airflow API token and
P6 bucket configuration, replay a known immutable object version:

```powershell
python scripts/orders_event_intake.py replay --feed partner_orders_a --key incoming/partner_orders_a/orders.csv --version EXACT_VERSION_ID
```

To revalidate a saved EventBridge body from the DLQ, set the expected
account/region variables and run:

```powershell
python scripts/orders_event_intake.py replay-event path/to/event.json
```

Both paths call the same identity and Airflow dispatch code. Replaying the
same version returns `already_exists`; it does **not** clear or restart a
failed DagRun. Inspect that run and use Airflow's controlled task retry
procedure if appropriate. A corrected object version gets a new DagRun ID.
Never replace the exact version with the object's current/latest version.

For read-only operational evidence, run
`python scripts/p7_operations.py --days 7 --fail-on-alerts` with
`P4_LOADER_DATABASE_URL`. It reports object-batch staging, release,
publication, overdue waits, lag, and audit mismatches. It cannot infer
whether a human approval was correct or identify a false publish without
independent labels and review.

Run the report on a schedule and alert on its exit code 2. Also alert on
nonempty DLQ depth, a stopped bridge process, and failed Airflow DAG runs.
Treat an overdue staged batch as a review item: it may be awaiting approval,
rejected, or blocked by validation. Investigate the API run and Airflow task
state before retrying. A release that remains unpublished beyond the report
threshold, or any audit mismatch, requires immediate investigation. Record
independent outcome labels during review to measure false publishes; the
database timestamps and release records alone cannot establish that metric.

## Verification limits

`tests/test_p7.py` uses offline EventBridge, SQS and Airflow stubs for
duplicate delivery, wrong origin, unknown bucket/key, missing version,
409 conflict, acknowledgment order, and replay. No cloud resources or
credentials are created. A deployment gate must exercise an actual
versioned S3 object, EventBridge rule, SQS queue/DLQ, Airflow API and the
P6 loader under isolated identities before subscribing a real feed.
