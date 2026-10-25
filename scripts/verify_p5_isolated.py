"""Run the P5 role and live batch gates in a temporary Postgres database.

Requires DATABASE_URL for an owner with CREATEDB and CREATEROLE, SHARED_TOKEN,
and GROQ_API_KEY. Creates only a uniquely named database and the p5_api and
p5_loader roles, then removes exactly those resources after verification.
No grants or rows in the source database are changed.
"""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
load_dotenv(ROOT / ".env")


def _run(argv: list[str], env: dict[str, str]) -> str:
    result = subprocess.run(argv, cwd=ROOT, env=env, text=True,
                            capture_output=True, check=False, timeout=600)
    if result.returncode:
        raise RuntimeError(f"{' '.join(argv[1:])} failed:\n{result.stdout[-1200:]}\n{result.stderr[-1200:]}")
    return result.stdout.strip()


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> None:
    owner_url = os.environ["DATABASE_URL"]
    browser_token = os.environ["SHARED_TOKEN"]
    if not os.environ.get("GROQ_API_KEY"):
        raise RuntimeError("GROQ_API_KEY is required for the live repair gate")
    suffix = secrets.token_hex(5)
    database = f"p5_gate_{suffix}"
    scope = f"p5-gate-{suffix}"
    api_password = secrets.token_urlsafe(32)
    loader_password = secrets.token_urlsafe(32)
    loader_token = secrets.token_urlsafe(32)
    approver_token = secrets.token_urlsafe(32)
    owner_gate = make_conninfo(owner_url, dbname=database)
    api_gate = make_conninfo(owner_gate, user="p5_api", password=api_password)
    loader_gate = make_conninfo(owner_gate, user="p5_loader", password=loader_password)
    created_roles: list[str] = []
    created_database = False
    server: subprocess.Popen | None = None
    log_path: Path | None = None
    failure: Exception | None = None
    try:
        with psycopg.connect(owner_url, autocommit=True, connect_timeout=8) as admin:
            existing = admin.execute(
                "select rolname from pg_roles where rolname in ('p5_api', 'p5_loader')"
            ).fetchall()
            if existing:
                raise RuntimeError("p5_api or p5_loader already exists; refusing to reuse it")
            if admin.execute("select 1 from pg_database where datname = %s", (database,)).fetchone():
                raise RuntimeError("generated database name already exists")
            for name, password in (("p5_api", api_password), ("p5_loader", loader_password)):
                admin.execute(sql.SQL("create role {} login password {}").format(
                    sql.Identifier(name), sql.Literal(password)))
                created_roles.append(name)
            admin.execute(sql.SQL("create database {}").format(sql.Identifier(database)))
            created_database = True
        print(f"Created isolated database {database} and test roles", flush=True)

        migrate_env = os.environ.copy()
        migrate_env.update(DATABASE_URL=owner_gate, P5_MIGRATION_DATABASE_URL=owner_gate,
                           PIPELINE_ENV=scope)
        print(_run([sys.executable, str(ROOT / "scripts" / "migrate_p5.py")], migrate_env), flush=True)
        with psycopg.connect(owner_gate, connect_timeout=8) as owner:
            owner.execute((ROOT / "scripts" / "p5_roles.sql").read_text(encoding="utf-8"))
        print("Applied isolated role grants", flush=True)

        verify_env = os.environ.copy()
        verify_env.update(P5_API_DATABASE_URL=api_gate, P4_LOADER_DATABASE_URL=loader_gate)
        print(_run([sys.executable, str(ROOT / "scripts" / "verify_p5_roles.py")], verify_env),
              flush=True)

        port = _port()
        api_env = os.environ.copy()
        api_env.update(DATABASE_URL=api_gate, AUTH_MODE="production", PIPELINE_ENV=scope,
                       LOADER_SERVICE_TOKEN=loader_token, APPROVER_TOKEN=approver_token)
        with tempfile.NamedTemporaryFile(mode="w", suffix="-p5-api.log", delete=False) as log:
            log_path = Path(log.name)
            server = subprocess.Popen([sys.executable, "run_local.py", str(port)],
                                      cwd=BACKEND, env=api_env, stdout=log,
                                      stderr=subprocess.STDOUT,
                                      creationflags=(subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0))
        base_url = f"http://127.0.0.1:{port}"
        with httpx.Client(timeout=2) as client:
            for _ in range(90):
                if server.poll() is not None:
                    raise RuntimeError("isolated API exited during startup")
                try:
                    if client.get(f"{base_url}/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(1)
            else:
                raise TimeoutError("isolated API did not become healthy")
            runtime = client.get(f"{base_url}/api/runtime",
                                 headers={"Authorization": f"Bearer {browser_token}"})
            runtime.raise_for_status()
            if runtime.json()["pipeline_env"] != scope:
                raise RuntimeError("isolated API started in the wrong scope")
        print("Isolated API started under p5_api", flush=True)

        batch_env = os.environ.copy()
        batch_env.update(AUTH_MODE="production", SHARED_TOKEN=browser_token,
                         P4_AGENT_API_URL=base_url, P4_LOADER_DATABASE_URL=loader_gate,
                         P5_AUDIT_DATABASE_URL=owner_gate,
                         P5_LOADER_SERVICE_TOKEN=loader_token,
                         P5_APPROVER_TOKEN=approver_token)
        print(_run([sys.executable, str(ROOT / "scripts" / "verify_p4_orders.py"),
                    "--expected-env", scope], batch_env), flush=True)
        print("P5 isolated role and live batch gates passed", flush=True)
    except Exception as exc:
        failure = exc
        print(f"P5 isolated gate failed: {exc}", file=sys.stderr, flush=True)
        if log_path and log_path.exists():
            print(log_path.read_text(encoding="utf-8", errors="replace")[-2200:],
                  file=sys.stderr)
    finally:
        if server and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        if log_path:
            for _ in range(10):
                try:
                    log_path.unlink(missing_ok=True)
                    break
                except PermissionError:
                    time.sleep(0.5)
            else:
                print(f"Could not remove temporary API log {log_path}", file=sys.stderr)
        try:
            with psycopg.connect(owner_url, autocommit=True, connect_timeout=8) as admin:
                if created_database:
                    admin.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                                  "where datname = %s and pid <> pg_backend_pid()", (database,))
                    admin.execute(sql.SQL("drop database {}").format(sql.Identifier(database)))
                    print(f"Removed isolated database {database}", flush=True)
                for name in reversed(created_roles):
                    admin.execute(sql.SQL("drop role {}").format(sql.Identifier(name)))
                    print(f"Removed test role {name}", flush=True)
        except Exception as cleanup_exc:
            if failure is None:
                failure = RuntimeError(f"gate passed but test resource cleanup failed: {cleanup_exc}")
            else:
                print(f"Test resource cleanup failed: {cleanup_exc}", file=sys.stderr)
    if failure:
        raise failure


if __name__ == "__main__":
    main()
