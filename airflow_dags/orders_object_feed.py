"""Airflow 3 reference DAG for a versioned object-store orders batch.

Trigger with dag_run.conf = {"feed_id": "partner_orders_a",
"object_key": "incoming/partner_orders_a/orders.csv", "version_id": "..."}.
Approval is an independent human action in the agent API. The reschedule
sensor releases its worker slot while that decision is pending.
"""

from datetime import datetime, timedelta, timezone
import os

import httpx
import psycopg
from airflow.sdk import PokeReturnValue, dag, get_current_context, task

from scripts.object_orders_feed import batch_status, object_spec, publish_batch, stage_or_resume_object, trigger_agent


def _api():
    token = os.environ["P5_LOADER_SERVICE_TOKEN"]
    return httpx.Client(base_url=os.environ["P4_AGENT_API_URL"].rstrip("/"),
                        headers={"Authorization": f"Bearer {token}"}, timeout=20)


@dag(dag_id="versioned_orders_to_agent", schedule=None, catchup=False,
     start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
     tags=["orders", "external", "versioned-object"])
def versioned_orders_to_agent():
    @task(retries=3, retry_delay=timedelta(minutes=1))
    def stage_exact_version() -> dict:
        conf = get_current_context()["dag_run"].conf or {}
        feed, key, version = (conf.get("feed_id"), conf.get("object_key"), conf.get("version_id"))
        object_spec(feed, key, version)  # fail before any DB write or GET
        with psycopg.connect(os.environ["P4_LOADER_DATABASE_URL"],
                             row_factory=psycopg.rows.dict_row) as conn:
            staged = stage_or_resume_object(conn, feed, key, version)
            conn.commit()
        return staged

    @task(retries=3, retry_delay=timedelta(minutes=1))
    def trigger(staged: dict) -> str:
        with _api() as client:
            return trigger_agent(client, staged)

    @task.sensor(poke_interval=60, timeout=24 * 60 * 60, mode="reschedule")
    def wait_for_validation(staged: dict, run_id: str) -> PokeReturnValue:
        with _api() as client:
            status = batch_status(client, staged, run_id)
        if status in {"alert", "escalated"}:
            raise ValueError(f"agent blocked downstream publish: {status}")
        return PokeReturnValue(is_done=status in {"clean", "healed"}, xcom_value=status)

    @task(retries=3, retry_delay=timedelta(minutes=1))
    def publish_validated(staged: dict, run_id: str, ready_status: str) -> dict:
        if ready_status not in {"clean", "healed"}:
            raise ValueError("sensor did not authorize downstream publish")
        with psycopg.connect(os.environ["P4_LOADER_DATABASE_URL"],
                             row_factory=psycopg.rows.dict_row) as conn, _api() as client:
            return publish_batch(conn, client, staged, run_id)

    staged = stage_exact_version()
    run_id = trigger(staged)
    ready = wait_for_validation(staged, run_id)
    publish_validated(staged, run_id, ready)


dag = versioned_orders_to_agent()
