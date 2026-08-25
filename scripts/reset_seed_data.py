"""One-off maintenance: wipe everything in the dashboard-demo pipeline_env
namespace (run_records, checkpoint tables, mapping_state) so seed_dashboard.py
can be re-run cleanly. Never touches any other namespace, "default" included.

Not part of the app's runtime API -- a local script, run manually:

    cd backend
    ../.venv/Scripts/python.exe ../scripts/reset_seed_data.py [pipeline_env]
"""

import asyncio
import selectors
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import db  # noqa: E402
from config import get_settings  # noqa: E402

ENV = sys.argv[1] if len(sys.argv) > 1 else "dashboard-demo"


async def main() -> None:
    if ENV == "default":
        sys.exit("refusing to reset the 'default' namespace")

    settings = get_settings()
    await db.open_pool(settings.database_url)
    pool = db.get_pool()

    async with pool.connection() as conn:
        cur = await conn.execute("select run_id from run_records where pipeline_env = %s", (ENV,))
        run_ids = [r["run_id"] for r in await cur.fetchall()]
        print(f"found {len(run_ids)} runs in pipeline_env={ENV!r}")

        if run_ids:
            await conn.execute("delete from checkpoint_writes where thread_id = any(%s)", (run_ids,))
            await conn.execute("delete from checkpoint_blobs where thread_id = any(%s)", (run_ids,))
            await conn.execute("delete from checkpoints where thread_id = any(%s)", (run_ids,))
            await conn.execute("delete from run_records where pipeline_env = %s", (ENV,))

        cur = await conn.execute("delete from mapping_state where pipeline_env = %s", (ENV,))
        print(f"cleared checkpoints + run_records + mapping_state for {ENV!r}")

    await db.close_pool()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    else:
        asyncio.run(main())
