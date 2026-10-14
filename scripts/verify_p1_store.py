"""Isolated live-Postgres P1 scope/CAS/rollback verification.

Run only after the parent migration audit has completed. This script never
updates the application's default environment: it creates a fresh, random
`p1-verify-*` namespace and leaves immutable audit rows for inspection.
It performs no DDL and never logs credentials.

    cd backend
    python ../scripts/verify_p1_store.py
"""

import asyncio
import json
import selectors
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import db  # noqa: E402
from config import get_settings  # noqa: E402
from pipeline.contract import get_registry  # noqa: E402
from pipeline.mapping import MappingPatch  # noqa: E402
from services import store  # noqa: E402


async def main() -> int:
    env = f"p1-verify-{uuid.uuid4().hex[:12]}"
    registry = get_registry()
    default = registry.get("default")
    compact = registry.get("compact")
    await db.open_pool(get_settings().database_url)
    try:
        default_seed, default_id = await store.get_current_mapping_version(env, "default", default)
        compact_seed, compact_id = await store.get_current_mapping_version(env, "compact", compact)
        assert default_seed == compact_seed == MappingPatch(), "fresh scopes must seed empty"
        assert default_id != compact_id

        async def attempt(source: str):
            return await store.save_mapping_cas(
                MappingPatch(renames={source: "customer_id"}), default_id,
                "p1-concurrency-check", pipeline_env=env, contract_name="default", contract=default)

        writes = await asyncio.gather(attempt("cust_id"), attempt("acct_ref"), return_exceptions=True)
        won = [v for v in writes if isinstance(v, int)]
        stale = [v for v in writes if isinstance(v, ValueError) and "changed" in str(v)]
        assert len(won) == len(stale) == 1, f"CAS outcome unexpected: {writes!r}"
        failed_version = won[0]

        compact_now, compact_now_id = await store.get_current_mapping_version(env, "compact", compact)
        assert compact_now_id == compact_id and compact_now == compact_seed, "contract scopes mixed"
        compact_patch = MappingPatch.model_validate(
            {"value_maps": {"segment": {"R": "Retail"}}}, context={"contract": compact})
        compact_written = await store.save_mapping_cas(
            compact_patch, compact_id, "p1-compact-check", pipeline_env=env,
            contract_name="compact", contract=compact)
        assert (await store.get_current_mapping_version(env, "compact", compact))[1] == compact_written
        assert (await store.get_current_mapping_version(env, "default", default))[1] == failed_version

        restored_default = await store.rollback_mapping_cas(
            failed_version, "p1-verification", "verification rollback", env, "default", default)
        restored_compact = await store.rollback_mapping_cas(
            compact_written, "p1-verification", "verification rollback", env, "compact", compact)
        assert (await store.get_current_mapping_version(env, "default", default)) == (default_seed, restored_default)
        assert (await store.get_current_mapping_version(env, "compact", compact)) == (compact_seed, restored_compact)
        try:
            await store.rollback_mapping_cas(failed_version, "p1-verification", "stale rollback", env, "default", default)
        except ValueError as exc:
            assert "rollback conflict" in str(exc)
        else:
            raise AssertionError("stale rollback was accepted")

        default_history = await store.mapping_history(10, env, "default")
        compact_history = await store.mapping_history(10, env, "compact")
        assert default_history[0]["event_type"] == "rollback"
        assert default_history[0]["restores_version_id"] == default_id
        assert default_history[0]["parent_id"] == failed_version
        assert compact_history[0]["event_type"] == "rollback"
        assert compact_history[0]["restores_version_id"] == compact_id
        print(json.dumps({
            "ok": True, "pipeline_env": env, "contracts": [default.name, compact.name],
            "concurrent_writes_accepted": len(won), "concurrent_writes_rejected": len(stale),
            "default_versions": [default_id, failed_version, restored_default],
            "compact_versions": [compact_id, compact_written, restored_compact],
            "stale_rollback_rejected": True,
        }))
        return 0
    finally:
        await db.close_pool()


if __name__ == "__main__":
    if sys.platform == "win32":
        code = asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    else:
        code = asyncio.run(main())
    raise SystemExit(code)
