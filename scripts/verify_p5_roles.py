"""Read-only privilege gate for separately provisioned P5 API and loader roles.

Supply P5_API_DATABASE_URL and P4_LOADER_DATABASE_URL for an isolated database.
No schema changes or data writes are made by this script.
"""

import os
import psycopg


def _has(conn, table: str, privilege: str) -> bool:
    return conn.execute("select has_table_privilege(current_user, %s, %s)",
                        (f"public.{table}", privilege)).fetchone()[0]


def _has_column(conn, table: str, column: str, privilege: str) -> bool:
    return conn.execute("select has_column_privilege(current_user, %s, %s, %s)",
                        (f"public.{table}", column, privilege)).fetchone()[0]


def verify_role(dsn: str, role: str) -> None:
    with psycopg.connect(dsn) as conn:
        expected = {
            "loader": {
                ("external_order_batches", "INSERT"): True,
                ("external_order_batches", "SELECT"): True,
                ("external_order_batches", "UPDATE"): False,
                ("external_order_batches", "DELETE"): False,
                ("external_order_releases", "SELECT"): True,
                ("external_order_releases", "INSERT"): False,
                ("mapping_state", "SELECT"): True,
                ("mapping_state", "INSERT"): False,
                ("approval_decisions", "INSERT"): False,
                ("published_orders", "INSERT"): True,
                ("published_orders", "UPDATE"): False,
                ("published_orders", "DELETE"): False,
                ("published_order_batches", "UPDATE"): False,
                ("published_order_batches", "DELETE"): False,
            },
            "api": {
                ("external_order_batches", "INSERT"): False,
                ("external_order_releases", "INSERT"): True,
                ("mapping_state", "INSERT"): True,
                ("approval_decisions", "INSERT"): True,
                ("published_orders", "INSERT"): False,
                ("external_order_batches", "UPDATE"): False,
            },
        }[role]
        for (table, privilege), allowed in expected.items():
            actual = _has(conn, table, privilege)
            if actual != allowed:
                raise AssertionError(f"{role}: {table} {privilege}={actual}, expected {allowed}")
        if role == "api":
            for column in ("agent_run_id", "dispatch_lease_until"):
                if not _has_column(conn, "external_order_batches", column, "UPDATE"):
                    raise AssertionError(f"api cannot update external_order_batches.{column}")
            for column in ("payload", "sha256", "row_count", "feed_id", "batch_id"):
                if _has_column(conn, "external_order_batches", column, "UPDATE"):
                    raise AssertionError(f"api can update external_order_batches.{column}")
        can_create = conn.execute("select has_schema_privilege(current_user, 'public', 'CREATE')").fetchone()[0]
        if can_create:
            raise AssertionError(f"{role} can create objects in public")


if __name__ == "__main__":
    verify_role(os.environ["P5_API_DATABASE_URL"], "api")
    verify_role(os.environ["P4_LOADER_DATABASE_URL"], "loader")
    print("P5 role privileges verified")
