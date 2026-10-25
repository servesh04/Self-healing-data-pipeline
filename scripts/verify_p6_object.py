"""Opt-in live P6 object-version gate against an isolated p6-* API scope.

On first run, fetch/stage the specified version. If approval is pending,
approve independently, then rerun with --retry: it uses only the immutable
Postgres stage and does not fetch the object again.
"""

import argparse
import os
from pathlib import Path
import sys
import time

import httpx
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.object_orders_feed import (batch_id_for_object, batch_status, object_spec,
                                        publish_batch, saved_object_stage, stage_object, trigger_agent)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-env", required=True)
    parser.add_argument("--feed", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--retry", action="store_true")
    parser.add_argument("--max-wait-seconds", type=int, default=180)
    args = parser.parse_args()
    if not args.expected_env.startswith("p6-"):
        parser.error("expected environment must be an isolated p6-* scope")
    spec = object_spec(args.feed, args.key, args.version)
    batch_id = batch_id_for_object(args.feed, spec["bucket"], args.key, args.version)
    api = os.environ["P4_AGENT_API_URL"].rstrip("/")
    with httpx.Client(base_url=api, headers={"Authorization": f"Bearer {os.environ['SHARED_TOKEN']}"},
                      timeout=20) as browser:
        runtime = browser.get("/api/runtime")
        runtime.raise_for_status()
        if runtime.json()["pipeline_env"] != args.expected_env:
            raise ValueError("API pipeline environment differs from --expected-env")
    with psycopg.connect(os.environ["P4_LOADER_DATABASE_URL"],
                         row_factory=psycopg.rows.dict_row) as conn:
        if args.retry:
            staged = saved_object_stage(conn, args.feed, args.key, args.version)
        else:
            staged = stage_object(conn, args.feed, args.key, args.version)
            conn.commit()
        with httpx.Client(base_url=api,
                          headers={"Authorization": f"Bearer {os.environ['P5_LOADER_SERVICE_TOKEN']}"},
                          timeout=20) as loader:
            run_id = trigger_agent(loader, staged)
            deadline = time.monotonic() + args.max_wait_seconds
            while time.monotonic() < deadline:
                status = batch_status(loader, staged, run_id)
                if status in {"clean", "healed"}:
                    result = publish_batch(conn, loader, staged, run_id)
                    print({"run_id": run_id, "batch_id": batch_id, "status": status,
                           "digest": staged["sha256"], **result})
                    return 0
                if status == "awaiting_approval":
                    print(f"run {run_id} awaits independent approval; rerun this gate with --retry afterward")
                    return 2
                if status in {"alert", "escalated"}:
                    count = conn.execute("""select count(*) as n from published_order_batches
                        where feed_id = %s and batch_id = %s""", (args.feed, batch_id)).fetchone()["n"]
                    if count:
                        raise AssertionError("blocked object batch was published")
                    raise ValueError(f"agent blocked publish: {status}")
                time.sleep(5)
            raise TimeoutError(f"run {run_id} did not complete before gate timeout")


if __name__ == "__main__":
    raise SystemExit(main())
