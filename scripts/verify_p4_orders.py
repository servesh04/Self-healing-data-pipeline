"""Opt-in live P4 gate: real API/checkpoint/LLM plus Postgres destination.

Requires P4_LOADER_DATABASE_URL, P4_AGENT_API_URL and P4_AGENT_TOKEN. Run
only against a fresh isolated PIPELINE_ENV, passed as --expected-env. It
leaves its uniquely named staging, run and destination rows for audit.
"""

import argparse
from pathlib import Path
import sys
import time
import uuid

import httpx
import psycopg

from orders_loader import publish, stage

DATA = Path(__file__).resolve().parents[1] / "data" / "sources"


def _poll(client, run_id, expected, seconds=180):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        if response.status_code == 404:
            time.sleep(1)
            continue
        response.raise_for_status()
        state = response.json()
        if state["api_status"] in expected:
            return state
        time.sleep(1)
    raise TimeoutError(f"run {run_id} did not reach {expected}")


def _count(conn, feed, batch):
    return conn.execute("""select count(*) as n from published_orders
        where feed_id = %s and batch_id = %s""", (feed, batch)).fetchone()["n"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-env", required=True)
    args = parser.parse_args()
    if not args.expected_env.startswith("p4-"):
        parser.error("expected env must be a fresh p4-* isolated scope")
    import os
    dsn = os.environ["P4_LOADER_DATABASE_URL"]
    api = os.environ["P4_AGENT_API_URL"].rstrip("/")
    token = os.environ["P4_AGENT_TOKEN"]
    suffix = uuid.uuid4().hex[:10]
    clean_id, repair_id, bad_id = (f"p4_clean_{suffix}", f"p4_repair_{suffix}", f"p4_bad_{suffix}")
    with httpx.Client(base_url=api, headers={"Authorization": f"Bearer {token}"}, timeout=20) as client:
        runtime = client.get("/api/runtime")
        runtime.raise_for_status()
        if runtime.json()["pipeline_env"] != args.expected_env:
            raise ValueError("API pipeline scope differs from --expected-env")
        with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
            stage(conn, "partner_orders_b", clean_id, (DATA / "day1_clean.csv").read_bytes()); conn.commit()
            response = client.post(f"/api/external/orders/partner_orders_b/{clean_id}/trigger")
            response.raise_for_status()
            clean_run = response.json()["run_id"]
            clean = _poll(client, clean_run, {"clean", "escalated"})
            if clean["api_status"] != "clean":
                raise AssertionError(f"control feed failed: {clean['final_message']}")
            release = client.post(f"/api/external/orders/partner_orders_b/{clean_id}/release")
            release.raise_for_status()
            control_manifest = release.json()
            assert publish(conn, "partner_orders_b", clean_id, control_manifest)["published"]
            conn.commit()
            assert _count(conn, "partner_orders_b", clean_id) == 50

            stage(conn, "partner_orders_a", repair_id, (DATA / "day6_ambiguous_rename.csv").read_bytes()); conn.commit()
            response = client.post(f"/api/external/orders/partner_orders_a/{repair_id}/trigger")
            response.raise_for_status()
            repair_run = response.json()["run_id"]
            assert client.post(f"/api/external/orders/partner_orders_a/{repair_id}/trigger").json()["run_id"] == repair_run
            paused = _poll(client, repair_run, {"awaiting_approval", "escalated", "healed"})
            if paused["api_status"] != "awaiting_approval" or paused["assertions_passed"]:
                raise AssertionError(f"expected initial failure and human review, got {paused['api_status']}")
            assert paused["mapping_version_written"] is None and _count(conn, "partner_orders_a", repair_id) == 0
            assert client.post(f"/api/external/orders/partner_orders_a/{repair_id}/release").status_code == 409
            approval = client.post(f"/api/runs/{repair_run}/approve", json={"note": "isolated P4 gate"})
            approval.raise_for_status()
            healed = _poll(client, repair_run, {"healed", "escalated"})
            if healed["api_status"] != "healed":
                raise AssertionError(f"approved batch did not heal: {healed['final_message']}")
            release = client.post(f"/api/external/orders/partner_orders_a/{repair_id}/release")
            release.raise_for_status()
            manifest = release.json()
            assert manifest["mapping_version"] == healed["mapping_version_written"]
            assert manifest["pipeline_env"] != control_manifest["pipeline_env"]
            assert publish(conn, "partner_orders_a", repair_id, manifest)["published"]
            conn.commit()
            assert _count(conn, "partner_orders_a", repair_id) == 50
            assert publish(conn, "partner_orders_a", repair_id, manifest)["published"] is False
            conn.commit()
            assert _count(conn, "partner_orders_a", repair_id) == 50
            current_b = conn.execute("""select current_version_id from mapping_current
                where pipeline_env = %s and contract_name = 'default'""",
                (control_manifest["pipeline_env"],)).fetchone()["current_version_id"]
            assert current_b == control_manifest["mapping_version"]

            stage(conn, "partner_orders_a", bad_id, (DATA / "day5_unfixable.csv").read_bytes()); conn.commit()
            response = client.post(f"/api/external/orders/partner_orders_a/{bad_id}/trigger")
            response.raise_for_status()
            bad = _poll(client, response.json()["run_id"], {"escalated", "awaiting_approval"})
            if bad["api_status"] != "escalated":
                raise AssertionError("unsafe negative unexpectedly requested approval")
            assert client.post(f"/api/external/orders/partner_orders_a/{bad_id}/release").status_code == 409
            assert _count(conn, "partner_orders_a", bad_id) == 0
            print({"control_run": clean_run, "repair_run": repair_run,
                   "control_rows": 50, "repair_rows": 50, "bad_rows": 0,
                   "control_version": control_manifest["mapping_version"],
                   "repair_version": manifest["mapping_version"]})


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"P4 live gate failed: {exc}", file=sys.stderr)
        sys.exit(1)
