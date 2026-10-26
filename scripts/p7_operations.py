"""Read-only operational gate for versioned external orders batches.

Uses the restricted loader's SELECT grants. Reports recent staged batches,
release/publish latency, aged work, and mismatched release/publish audit rows.
It does not infer whether an unreleased batch was rejected or awaits approval.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from statistics import median

import psycopg
from psycopg.rows import dict_row


DEFAULT_LIMIT = 10_000


def summarize(rows: list[dict], *, now: datetime, stale_hours: int = 24,
              publish_minutes: int = 30) -> dict:
    if now.tzinfo is None or stale_hours < 1 or publish_minutes < 1:
        raise ValueError("timezone-aware now and positive thresholds are required")
    counts = {"staged": len(rows), "released": 0, "published": 0,
              "staged_without_release_overdue": 0,
              "released_without_publish_overdue": 0,
              "audit_mismatches": 0}
    release_lag = []
    publish_lag = []
    examples = []
    for row in rows:
        created = row["created_at"]
        released = row["released_at"]
        published = row["published_at"]
        if released is not None:
            counts["released"] += 1
            release_lag.append((released - created).total_seconds())
        if published is not None:
            counts["published"] += 1
            if released is not None:
                publish_lag.append((published - released).total_seconds())
        issues = []
        if released is None and published is None and (now - created).total_seconds() > stale_hours * 3600:
            counts["staged_without_release_overdue"] += 1
            issues.append("staged_without_release_overdue")
        if released is not None and published is None and (now - released).total_seconds() > publish_minutes * 60:
            counts["released_without_publish_overdue"] += 1
            issues.append("released_without_publish_overdue")
        if (published is not None and released is None) or (
            released is not None and (
                row["release_sha256"] != row["sha256"] or
                row["release_run_id"] != row["agent_run_id"] or
                row["release_row_count"] != row["row_count"] or
                released < created
            )
        ) or (
            published is not None and (
                row["published_sha256"] != row["sha256"] or
                row["published_run_id"] != row["agent_run_id"] or
                row["published_row_count"] != row["row_count"] or
                (released is not None and
                 row["published_mapping_version"] != row["release_mapping_version"]) or
                (released is not None and published < released)
            )
        ):
            counts["audit_mismatches"] += 1
            issues.append("audit_mismatch")
        if issues and len(examples) < 25:
            examples.append({"feed_id": row["feed_id"], "batch_id": row["batch_id"],
                             "issues": issues})
    return {"counts": counts,
            "median_stage_to_release_seconds": median(release_lag) if release_lag else None,
            "median_release_to_publish_seconds": median(publish_lag) if publish_lag else None,
            "examples": examples}


def fetch_recent(dsn: str, *, days: int = 7, feed_id: str | None = None,
                 limit: int = DEFAULT_LIMIT) -> list[dict]:
    if not 1 <= days <= 365 or not 1 <= limit <= 100_000:
        raise ValueError("days and limit are outside supported bounds")
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10) as conn:
        with conn.transaction():
            conn.execute("set transaction read only")
            rows = conn.execute("""select b.feed_id, b.batch_id, b.created_at,
                b.sha256, b.row_count, b.agent_run_id,
                r.released_at, r.sha256 as release_sha256,
                r.row_count as release_row_count, r.agent_run_id as release_run_id,
                r.mapping_version as release_mapping_version,
                p.published_at, p.sha256 as published_sha256,
                p.row_count as published_row_count, p.agent_run_id as published_run_id,
                p.mapping_version as published_mapping_version
                from external_order_batches b
                left join external_order_releases r
                  on r.feed_id = b.feed_id and r.batch_id = b.batch_id
                left join published_order_batches p
                  on p.feed_id = b.feed_id and p.batch_id = b.batch_id
                where b.object_version_id is not null
                  and b.created_at >= now() - (%s * interval '1 day')
                  and (%s::text is null or b.feed_id = %s)
                order by b.created_at desc, b.feed_id, b.batch_id limit %s""",
                (days, feed_id, feed_id, limit + 1)).fetchall()
    if len(rows) > limit:
        raise ValueError("report exceeds row cap; narrow --days or --feed")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--feed")
    parser.add_argument("--stale-hours", type=int, default=24)
    parser.add_argument("--publish-minutes", type=int, default=30)
    parser.add_argument("--fail-on-alerts", action="store_true")
    args = parser.parse_args()
    report = summarize(fetch_recent(os.environ["P4_LOADER_DATABASE_URL"],
                                    days=args.days, feed_id=args.feed),
                       now=datetime.now(timezone.utc), stale_hours=args.stale_hours,
                       publish_minutes=args.publish_minutes)
    report["window_days"] = args.days
    report["feed_id"] = args.feed
    print(json.dumps(report, sort_keys=True))
    alerts = sum(report["counts"][name] for name in (
        "staged_without_release_overdue", "released_without_publish_overdue",
        "audit_mismatches"))
    return 2 if args.fail_on_alerts and alerts else 0


if __name__ == "__main__":
    raise SystemExit(main())
