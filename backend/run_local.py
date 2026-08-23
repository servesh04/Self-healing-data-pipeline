"""Local dev server. Use this instead of bare `uvicorn` on Windows.

uvicorn hard-codes ProactorEventLoop on win32 (see uvicorn/loops/asyncio.py),
and it does so with a loop *factory*, which ignores asyncio's event-loop policy.
psycopg's async mode refuses to run on ProactorEventLoop, so a bare
`uvicorn main:app` on Windows starts fine and then every database call times out
with no obvious cause.

This entrypoint owns the loop itself and hands uvicorn a selector loop. On Linux
(including the container) it is a plain uvicorn run, so production is unaffected.

    python run_local.py            # defaults to 127.0.0.1:8000
    python run_local.py 8123       # custom port
"""

import asyncio
import selectors
import sys

import uvicorn


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    config = uvicorn.Config(
        "main:app",
        host="127.0.0.1",
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)

    if sys.platform == "win32":
        asyncio.run(
            server.serve(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    else:
        server.run()


if __name__ == "__main__":
    main()
