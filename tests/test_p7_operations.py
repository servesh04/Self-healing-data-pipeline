"""Read-only P7 operational signals over versioned staged batches."""

from datetime import datetime, timedelta, timezone

import pytest

from scripts import p7_operations as ops


NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


def batch(*, age_hours=1, release_minutes=None, publish_minutes=None,
          feed="partner_orders_a", batch_id="obj_one"):
    created = NOW - timedelta(hours=age_hours)
    released = created + timedelta(minutes=release_minutes) if release_minutes is not None else None
    published = released + timedelta(minutes=publish_minutes) if released and publish_minutes is not None else None
    return {"feed_id": feed, "batch_id": batch_id, "created_at": created,
            "sha256": "exact", "row_count": 50, "agent_run_id": "run-one",
            "released_at": released,
            "release_sha256": "exact" if released else None,
            "release_row_count": 50 if released else None,
            "release_run_id": "run-one" if released else None,
            "release_mapping_version": 7 if released else None,
            "published_at": published,
            "published_sha256": "exact" if published else None,
            "published_row_count": 50 if published else None,
            "published_run_id": "run-one" if published else None,
            "published_mapping_version": 7 if published else None}


def test_report_counts_lags_and_overdue_without_mislabeling_approval():
    rows = [batch(age_hours=2, release_minutes=60, publish_minutes=5),
            batch(age_hours=26, batch_id="obj_stale"),
            batch(age_hours=3, release_minutes=60, batch_id="obj_unpublished")]
    report = ops.summarize(rows, now=NOW, stale_hours=24, publish_minutes=30)
    assert report["counts"] == {"staged": 3, "released": 2, "published": 1,
        "staged_without_release_overdue": 1,
        "released_without_publish_overdue": 1, "audit_mismatches": 0}
    assert report["median_stage_to_release_seconds"] == 3600
    assert report["median_release_to_publish_seconds"] == 300
    assert {item["batch_id"] for item in report["examples"]} == {"obj_stale", "obj_unpublished"}


@pytest.mark.parametrize("alter", [
    {"release_sha256": "other"},
    {"release_run_id": "wrong"},
    {"release_row_count": 49},
    {"published_sha256": "other"},
    {"published_run_id": "wrong"},
    {"published_row_count": 49},
    {"published_mapping_version": 8},
])
def test_audit_mismatch_catches_identity_conflicts(alter):
    row = batch(age_hours=2, release_minutes=20, publish_minutes=2)
    row.update(alter)
    assert ops.summarize([row], now=NOW)["counts"]["audit_mismatches"] == 1


def test_publish_without_release_is_an_audit_mismatch():
    row = batch()
    row.update(published_at=NOW, published_sha256="exact", published_row_count=50,
               published_run_id="run-one", published_mapping_version=7)
    report = ops.summarize([row], now=NOW)
    assert report["counts"]["audit_mismatches"] == 1


def test_read_only_query_and_cap(monkeypatch):
    calls = []
    result_rows = [batch()]
    class Cursor:
        def fetchall(self): return result_rows
    class Transaction:
        def __enter__(self): return self
        def __exit__(self, *_): pass
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def transaction(self): return Transaction()
        def execute(self, sql, params=None):
            calls.append((sql, params))
            return Cursor()
    monkeypatch.setattr(ops.psycopg, "connect", lambda *_, **__: Connection())
    assert len(ops.fetch_recent("unused", days=3, feed_id="partner_orders_a")) == 1
    assert calls[0][0] == "set transaction read only"
    assert "b.object_version_id is not null" in calls[1][0]
    assert calls[1][1] == (3, "partner_orders_a", "partner_orders_a", 10_001)
    result_rows.append(batch(batch_id="obj_two"))
    with pytest.raises(ValueError, match="row cap"):
        ops.fetch_recent("unused", limit=1)
