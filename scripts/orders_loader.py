"""Independent staged-orders loader. No agent pipeline or graph imports.

Usage: python scripts/orders_loader.py submit FEED BATCH CSV
       python scripts/orders_loader.py approve FEED BATCH --note "reviewed"
       python scripts/orders_loader.py reject FEED BATCH --note "reason"
       python scripts/orders_loader.py retry FEED BATCH

The loader stages immutable bytes, invokes the authenticated agent bridge,
and publishes only after a release manifest and its own full validation.
On human approval pause, use the existing run UI/API, then invoke `retry`.
"""

import argparse
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from io import StringIO
import os
from pathlib import Path
import re
import sys
import time

import httpx
import psycopg

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 5000
EXPECTED = {"order_id", "customer_id", "region", "order_date", "revenue"}
REGIONS = {"North", "South", "East", "West"}
# Independently reviewed orders contract. A server-side contract edit must
# stop this loader until its validator/parity tests are updated deliberately.
SUPPORTED_CONTRACT_VERSION = "f84a8bf27067ded363a190284bc5033d527324dfbd289622c4156be19de69d7d"
ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DMY = re.compile(r"^\d{2}/\d{2}/\d{4}$")


def _id(value: str) -> str:
    if not ID.fullmatch(value):
        raise ValueError("feed and batch IDs must be lower-case names")
    return value


def _rows(payload: bytes) -> list[dict]:
    if len(payload) > MAX_BYTES:
        raise ValueError("staged batch exceeds byte cap")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("staged batch is not UTF-8") from exc
    reader = csv.DictReader(StringIO(text, newline=""), strict=True)
    if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise ValueError("CSV header is missing or duplicated")
    rows = []
    for row in reader:
        if None in row:
            raise ValueError("CSV row has extra fields")
        rows.append(row)
        if len(rows) > MAX_ROWS:
            raise ValueError("staged batch exceeds row cap")
    if len(rows) < 20:
        raise ValueError("staged batch has fewer than 20 rows")
    return rows


def _date(value: str, operation: str | None) -> str:
    if ISO.fullmatch(value):
        parsed = datetime.strptime(value, "%Y-%m-%d")
    elif operation == "dmy_slash_utc" and DMY.fullmatch(value):
        day, month, _ = value.split("/")
        if int(day) <= 12 and int(month) <= 12:
            raise ValueError("ambiguous day/month date")
        parsed = datetime.strptime(value, "%d/%m/%Y")
    else:
        raise ValueError("date does not match strict ISO or declared format")
    return parsed.date().isoformat()


def validate_for_publish(payload: bytes, mapping: dict) -> list[tuple]:
    """Independent, fail-closed orders validation over the complete batch."""
    if set(mapping) - {"renames", "drops", "casts", "null_policy", "value_maps", "date_parse"}:
        raise ValueError("mapping contains unsupported section")
    renames = mapping.get("renames") or {}
    drops = set(mapping.get("drops") or [])
    casts = mapping.get("casts") or {}
    nulls = mapping.get("null_policy") or {}
    value_maps = mapping.get("value_maps") or {}
    date_ops = mapping.get("date_parse") or {}
    if (set(casts.items()) - {("revenue", "strip_commas_to_float")} or
            set(date_ops.items()) - {("order_date", "dmy_slash_utc")} or
            set(nulls.values()) - {"fill_unknown", "fill_zero"} or
            set(nulls) - EXPECTED or
            set(value_maps) - {"region"}):
        raise ValueError("mapping operation is unsupported by loader")
    output = []
    seen = set()
    for raw in _rows(payload):
        row = {renames.get(key, key): value for key, value in raw.items() if key not in drops}
        if set(row) != EXPECTED or len(row) != len(raw) - len(set(raw) & drops):
            raise ValueError("mapped CSV columns do not match orders contract")
        for column, operation in nulls.items():
            if not row[column]:
                row[column] = "Unknown" if operation == "fill_unknown" else "0"
        if not row["region"]:
            raise ValueError("region is null")
        row["region"] = value_maps.get("region", {}).get(row["region"], row["region"])
        if row["region"] not in REGIONS:
            raise ValueError("region is outside the contract allowed values")
        try:
            order_id = int(row["order_id"])
            customer_id = int(row["customer_id"])
            revenue_text = row["revenue"].replace(",", "") if casts.get("revenue") else row["revenue"]
            revenue = Decimal(revenue_text)
        except (ValueError, InvalidOperation) as exc:
            raise ValueError("orders contain invalid numeric values") from exc
        if not revenue.is_finite() or revenue < 0 or not math.isfinite(float(revenue)):
            raise ValueError("revenue is not finite or nonnegative")
        if order_id in seen:
            raise ValueError("duplicate order_id")
        seen.add(order_id)
        output.append((order_id, customer_id, row["region"],
                       _date(row["order_date"], date_ops.get("order_date")), float(revenue)))
    return output


def stage(conn, feed_id: str, batch_id: str, payload: bytes) -> dict:
    feed_id, batch_id = _id(feed_id), _id(batch_id)
    rows = _rows(payload)
    digest = hashlib.sha256(payload).hexdigest()
    with conn.transaction():
        conn.execute("""insert into external_order_batches
            (feed_id, batch_id, payload, sha256, row_count) values (%s, %s, %s, %s, %s)
            on conflict (feed_id, batch_id) do nothing""",
            (feed_id, batch_id, payload, digest, len(rows)))
        row = conn.execute("""select sha256, row_count from external_order_batches
            where feed_id = %s and batch_id = %s""", (feed_id, batch_id)).fetchone()
        if row["sha256"] != digest or row["row_count"] != len(rows):
            raise ValueError("batch ID already staged with different bytes")
    return {"sha256": digest, "row_count": len(rows)}


def publish(conn, feed_id: str, batch_id: str, manifest: dict) -> dict:
    if (manifest.get("feed_id") != feed_id or manifest.get("batch_id") != batch_id or
            not isinstance(manifest.get("mapping_version"), int) or
            not isinstance(manifest.get("mapping"), dict) or
            manifest.get("contract_version") != SUPPORTED_CONTRACT_VERSION):
        raise ValueError("release manifest does not match batch")
    with conn.transaction():
        staged = conn.execute("""select payload, sha256, row_count, agent_run_id
            from external_order_batches where feed_id = %s and batch_id = %s for update""",
            (feed_id, batch_id)).fetchone()
        if staged is None:
            raise ValueError("staged batch is missing")
        payload = bytes(staged["payload"])
        digest = hashlib.sha256(payload).hexdigest()
        if (digest != staged["sha256"] or digest != manifest.get("sha256") or
                staged["agent_run_id"] != manifest.get("run_id") or
                staged["row_count"] != manifest.get("row_count")):
            raise ValueError("staged bytes or agent run differ from release")
        record = conn.execute("""select pipeline_env, contract_name, source_ref
            from run_records where run_id = %s""", (manifest["run_id"],)).fetchone()
        if (record is None or record["pipeline_env"] != manifest.get("pipeline_env") or
                not record["pipeline_env"].endswith(f":external:{feed_id}") or
                record["contract_name"] != "default" or
                (record["source_ref"] or {}).get("sha256") != digest or
                (record["source_ref"] or {}).get("feed_id") != feed_id or
                (record["source_ref"] or {}).get("batch_id") != batch_id):
            raise ValueError("release scope does not match agent run")
        authorization = conn.execute("""select sha256, agent_run_id, pipeline_env,
            mapping_version, mapping_sha256, row_count, contract_version
            from external_order_releases where feed_id = %s and batch_id = %s for update""",
            (feed_id, batch_id)).fetchone()
        mapping_hash = hashlib.sha256(json.dumps(manifest["mapping"], sort_keys=True,
                                                 separators=(",", ":")).encode()).hexdigest()
        if (authorization is None or authorization["sha256"] != digest or
                authorization["agent_run_id"] != manifest["run_id"] or
                authorization["pipeline_env"] != manifest["pipeline_env"] or
                authorization["mapping_version"] != manifest["mapping_version"] or
                authorization["mapping_sha256"] != mapping_hash or
                authorization["row_count"] != manifest["row_count"] or
                authorization["contract_version"] != manifest.get("contract_version")):
            raise ValueError("release was not authorized for exact mapping and batch")
        version = conn.execute("""select mapping, exists (
            select 1 from mapping_state where restores_version_id = %s
                and pipeline_env = %s and contract_name = 'default' and event_type = 'rollback'
            ) as rolled_back from mapping_state
            where id = %s and pipeline_env = %s and contract_name = 'default'""",
            (manifest["mapping_version"], manifest["pipeline_env"],
             manifest["mapping_version"], manifest["pipeline_env"])).fetchone()
        if version is None or version["rolled_back"] or version["mapping"] != manifest["mapping"]:
            raise ValueError("release mapping version was rolled back or unavailable")
        rows = validate_for_publish(payload, manifest["mapping"])
        if len(rows) != staged["row_count"]:
            raise ValueError("validated row count differs from stage")
        existing = conn.execute("""select sha256, agent_run_id, mapping_version, row_count
            from published_order_batches where feed_id = %s and batch_id = %s for update""",
            (feed_id, batch_id)).fetchone()
        if existing:
            if (existing["sha256"], existing["agent_run_id"], existing["mapping_version"], existing["row_count"]) != (
                digest, manifest["run_id"], manifest["mapping_version"], len(rows)):
                raise ValueError("published batch conflicts with release")
            count = conn.execute("""select count(*) as n from published_orders
                where feed_id = %s and batch_id = %s""", (feed_id, batch_id)).fetchone()["n"]
            if count != len(rows):
                raise ValueError("published destination row count differs from audit")
            return {"published": False, "row_count": len(rows)}
        with conn.cursor() as cursor:
            cursor.executemany("""insert into published_orders
                (feed_id, batch_id, order_id, customer_id, region, order_date, revenue)
                values (%s, %s, %s, %s, %s, %s, %s)""",
                [(feed_id, batch_id, *row) for row in rows])
        conn.execute("""insert into published_order_batches
            (feed_id, batch_id, sha256, agent_run_id, mapping_version, row_count)
            values (%s, %s, %s, %s, %s, %s)""",
            (feed_id, batch_id, digest, manifest["run_id"], manifest["mapping_version"], len(rows)))
        return {"published": True, "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("submit", "retry", "approve", "reject"))
    parser.add_argument("feed_id")
    parser.add_argument("batch_id")
    parser.add_argument("csv_path", nargs="?")
    parser.add_argument("--note", default=None)
    args = parser.parse_args()
    _id(args.feed_id); _id(args.batch_id)
    if args.action == "submit" and not args.csv_path:
        parser.error("submit requires csv_path")
    dsn = os.environ["P4_LOADER_DATABASE_URL"]
    api = os.environ["P4_AGENT_API_URL"].rstrip("/")
    token = os.environ["P4_AGENT_TOKEN"]
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        if args.action == "submit":
            staged = stage(conn, args.feed_id, args.batch_id, Path(args.csv_path).read_bytes())
            conn.commit()  # API bridge runs in another process/transaction.
            print(f"staged {args.feed_id}/{args.batch_id} sha256={staged['sha256']}")
        with httpx.Client(base_url=api, headers={"Authorization": f"Bearer {token}"}, timeout=20) as client:
            response = client.post(f"/api/external/orders/{args.feed_id}/{args.batch_id}/trigger")
            response.raise_for_status()
            run_id = response.json()["run_id"]
            if args.action in {"approve", "reject"}:
                decision = client.post(f"/api/runs/{run_id}/{args.action}", json={"note": args.note})
                decision.raise_for_status()
                print(f"{args.action} recorded for run {run_id}; invoke retry after it completes")
                return 0
            for _ in range(120):
                detail = client.get(f"/api/runs/{run_id}")
                # The trigger registers the run before its background graph
                # writes the first checkpoint. A short-lived 404 is expected.
                if detail.status_code == 404:
                    time.sleep(1)
                    continue
                detail.raise_for_status()
                status = detail.json()["api_status"]
                if status in {"clean", "healed"}:
                    release = client.post(f"/api/external/orders/{args.feed_id}/{args.batch_id}/release")
                    release.raise_for_status()
                    print(publish(conn, args.feed_id, args.batch_id, release.json()))
                    return 0
                if status == "awaiting_approval":
                    print(f"run {run_id} awaits approval; run orders_loader.py approve {args.feed_id} {args.batch_id}, then retry")
                    return 2
                if status in {"alert", "escalated"}:
                    raise ValueError(f"agent blocked publish: {status}")
                time.sleep(1)
            raise TimeoutError(f"run {run_id} did not reach a terminal state")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, TimeoutError, httpx.HTTPError, psycopg.Error) as exc:
        print(f"orders loader failed: {exc}", file=sys.stderr)
        sys.exit(1)
