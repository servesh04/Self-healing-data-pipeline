# P6 versioned object orders and Airflow reference DAG

This is a narrow input for the existing external orders loader. An Airflow
run names an allowlisted feed, a CSV key under that feed's configured prefix,
and an explicit non-null object version ID. The bucket comes from
`P6_ORDERS_BUCKET` in the loader environment, never from the DAG request.
`scripts/object_orders_feed.py` rejects control/backslash characters and
keys over 1,024 UTF-8 bytes, then calls S3-compatible `GetObject` with that
exact `VersionId`, verifies the returned version and length, and streams no
more than 10 MiB into memory. It derives a stable batch ID from feed,
bucket, key and version. The complete bytes, SHA-256 digest and object
identity are persisted in the existing staged-batch table. Reusing the same
version and batch ID is idempotent; a payload or identity conflict fails.
The agent bridge still processes the exact Postgres snapshot. Publish after
approval reads that snapshot, not a newly fetched object.

Object storage must have versioning enabled; an absent or `null` version ID
is rejected. Retain referenced versions through the approval window and
configure the loader identity for `s3:GetObjectVersion` on only the allowed
bucket/prefixes. The adapter uses boto3's standard credential chain. For an
S3-compatible deployment, set `P6_S3_ENDPOINT_URL` and `P6_S3_REGION` in the
loader/Airflow worker environment. The endpoint is deployment configuration,
not a DAG parameter. See [boto3 GetObject](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3/client/get_object.html)
and [S3 versioning behavior](https://docs.aws.amazon.com/AmazonS3/latest/userguide/versioning-workflows.html).

## Airflow 3 reference

Install the project root on the Airflow worker's `PYTHONPATH`; install
Airflow and the standard provider using that deployment's constraints, plus
`scripts/requirements-p6-loader.txt` (which includes boto3). The backend does not import
Airflow or boto3. Copy `airflow_dags/orders_object_feed.py` into the DAG
folder. Provide `P4_LOADER_DATABASE_URL`, `P4_AGENT_API_URL`,
`P5_LOADER_SERVICE_TOKEN`, `P6_ORDERS_BUCKET`, and object-store credentials
to the worker. Do not give the DAG `SHARED_TOKEN` or `P5_APPROVER_TOKEN`.

Trigger `versioned_orders_to_agent` with:

```json
{"feed_id":"partner_orders_a","object_key":"incoming/partner_orders_a/orders.csv","version_id":"EXACT_VERSION_ID"}
```

The DAG stages the exact object version, triggers the idempotent agent
bridge, waits using an Airflow sensor in `reschedule` mode, then requests a
release and runs the independent loader's full validation and atomic
destination publish. `running` and `awaiting_approval` keep the sensor
waiting without occupying a worker slot between checks. A human decides
through the separate P5 approver endpoint or CLI. `alert` and `escalated`
fail the sensor, so the publish task cannot run. Stage task retries first
check Postgres and avoid another object GET after an initial commit. A retry
of the publish task uses the same batch ID and Postgres bytes. [Airflow documents reschedule
sensors](https://airflow.apache.org/docs/apache-airflow/stable/authoring-and-scheduling/deferring.html)
for this worker-slot behavior. The reference targets Airflow 3's public
`airflow.sdk` API; it is not installed as a mandatory application dependency.

## Opt-in isolated gate

After the schema-owner migration and P5 role grants are reviewed and applied
to an isolated database, provision an actual versioned object containing a
contract-clean or repairable orders CSV. With a backend in a fresh `p6-*`
scope, set the P5 loader/browser credentials, loader DB URL, API URL, object
bucket and object-store credentials. Run:

```powershell
python scripts/verify_p6_object.py --expected-env p6-my-test --feed partner_orders_a --key incoming/partner_orders_a/orders.csv --version EXACT_VERSION_ID
```

If it pauses for human approval, decide with the separate approver credential,
then rerun the same command with `--retry`. The retry reads only the saved
Postgres stage and checks its bucket/key/version and digest; it makes no S3
request. The gate checks scoped runtime identity, terminal status, release,
and destination publication. A blocked alert/escalation must leave no
published batch. `tests/test_p6.py` exercises object/version mismatch,
byte caps, invalid source identity, stable IDs, approval wait, blocked
publish and exact release using stubs. An Airflow scheduler and a real
versioned object store are opt-in deployment gates, not local test
dependencies.

The P5 isolated live gate was rerun after this migration: the new columns
migrated, the restricted API and loader privilege checks passed, and the
production-mode clean and repaired batches published 50 rows each. Its
temporary database and roles were removed. This verifies schema and role
compatibility; the object-store and Airflow deployment gates remain opt-in.

The object adapter handles CSV orders only, one object version per batch.
It does not list buckets, follow arbitrary URLs, load multiple partitions,
or make human approval decisions.
