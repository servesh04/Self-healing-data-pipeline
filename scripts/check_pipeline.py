"""Phase 1 manual verification (ARCHITECTURE.md Phase 1, step 5):

    day1 passes; day2-day5 each fail with the expected assertion error;
    manually patching the mapping makes day2 pass.

Talks to Postgres directly (reuses backend/db.py + services/store.py) so the
mapping it reads and patches is the same live state the deployed API would
see — not a local file. Run from the backend/ directory so its relative
imports resolve, with DATABASE_URL set (.env is picked up automatically):

    cd backend
    ../.venv/Scripts/python.exe ../scripts/check_pipeline.py
"""

import asyncio
import selectors
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import db  # noqa: E402
from config import get_settings  # noqa: E402
from pipeline.contract import load_contract  # noqa: E402
from pipeline.mapping import MappingPatch  # noqa: E402
from pipeline.runner import run_pipeline  # noqa: E402
from services import store  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "sources"

DATASETS = [
    ("day1_clean.csv", "pass", "control — no fault injected"),
    ("day2_renamed.csv", "fail", "customer_id -> cust_id"),
    ("day3_type_drift.csv", "fail", "revenue as comma-formatted string"),
    ("day4_combo.csv", "fail", "rename + type drift together"),
    ("day5_unfixable.csv", "fail", "abbreviated region + unexpected currency column"),
]


def _line() -> None:
    print("-" * 78)


async def main() -> None:
    settings = get_settings()
    await db.open_pool(settings.database_url)
    await store.init_mapping_table()

    contract = load_contract()

    print("=" * 78)
    print("PHASE 1 VERIFICATION — run all 5 fault datasets with the seed mapping")
    print("=" * 78)

    seed_mapping = await store.get_current_mapping()
    print(f"current mapping (from Postgres): {seed_mapping.model_dump()}\n")

    all_ok = True
    for filename, expected, fault in DATASETS:
        path = DATA_DIR / filename
        result = run_pipeline(str(path), seed_mapping, contract)
        actual = "pass" if result.passed else "fail"
        ok = actual == expected
        all_ok &= ok
        _line()
        print(f"{filename}  [{fault}]")
        print(f"  expected: {expected}  |  actual: {actual}  |  {'OK' if ok else 'MISMATCH'}")
        print(f"  rows_in={result.rows_in}  rows_out={result.rows_out}")
        if result.failures:
            print("  failures (distinguishable, parseable):")
            for f in result.failures:
                print(f"    - {f.summary()}")

    _line()
    print()
    print("=" * 78)
    print("PROVING THE HEALING TARGET WORKS — manually patch day2's rename fault")
    print("=" * 78)

    before = run_pipeline(str(DATA_DIR / "day2_renamed.csv"), seed_mapping, contract)
    print(f"day2_renamed BEFORE patch: {'pass' if before.passed else 'fail'}")

    patch = MappingPatch(renames={"cust_id": "customer_id"})
    saved = await store.save_mapping(patch, updated_by="phase1-manual-check")
    print(f"patch saved to Postgres: {saved.model_dump()}")

    reloaded = await store.get_current_mapping()
    after = run_pipeline(str(DATA_DIR / "day2_renamed.csv"), reloaded, contract)
    print(f"day2_renamed AFTER patch (mapping re-read from Postgres): "
          f"{'pass' if after.passed else 'fail'}")
    if not after.passed:
        for f in after.failures:
            print(f"    - {f.summary()}")

    history = await store.mapping_history(limit=5)
    print(f"\nmapping_state history (most recent {len(history)} rows):")
    for row in history:
        print(f"  id={row['id']}  by={row['updated_by']}  at={row['created_at']}  "
              f"mapping={row['mapping']}")

    _line()
    patch_proves_healing = before.passed is False and after.passed is True
    print()
    print("=" * 78)
    if all_ok and patch_proves_healing:
        print("PHASE 1 DEFINITION OF DONE: ALL CHECKS PASSED")
    else:
        print("PHASE 1 DEFINITION OF DONE: FAILED — see MISMATCH lines above")
    print("=" * 78)

    await db.close_pool()

    # Leave the live mapping clean for whoever runs this next / for Phase 2.
    # (Not undone above: the append-only history is the point — see store.py.)

    sys.exit(0 if (all_ok and patch_proves_healing) else 1)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(
            main(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    else:
        asyncio.run(main())
