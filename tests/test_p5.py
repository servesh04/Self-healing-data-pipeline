"""P5 production credential boundaries without connecting to the live database."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from config import Settings, get_settings
from routers import external, runs, analytics, chat


SHARED = "browser-" + "b" * 32
LOADER = "loader-" + "l" * 32
APPROVER = "approver-" + "a" * 32


def _settings(mode="production"):
    return Settings(database_url="postgresql://unused/unused", shared_token=SHARED,
                    auth_mode=mode, loader_service_token=LOADER,
                    approver_token=APPROVER, _env_file=None)


def _client(monkeypatch, mode="production"):
    settings = _settings(mode)
    app = FastAPI()
    app.include_router(runs.router)
    app.include_router(external.router)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[runs._ensure_checkpointer_ready] = lambda: None
    monkeypatch.setattr(runs, "get_settings", lambda: settings)
    monkeypatch.setattr(external, "get_settings", lambda: settings)
    app.state.graph_app = SimpleNamespace()
    return TestClient(app)


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_production_requires_independent_strong_credentials():
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql://unused/unused", shared_token=SHARED,
                 auth_mode="production", loader_service_token=SHARED,
                 approver_token=APPROVER, _env_file=None)
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql://unused/unused", shared_token=SHARED,
                 auth_mode="production", _env_file=None)


def test_external_bridge_and_poll_reject_browser_and_approver(monkeypatch):
    client = _client(monkeypatch)
    for token in (SHARED, APPROVER):
        assert client.post("/api/external/orders/partner_orders_a/batch_one/trigger",
                           headers=_headers(token)).status_code == 401
        assert client.post("/api/external/orders/partner_orders_a/batch_one/release",
                           headers=_headers(token)).status_code == 401
        assert client.get("/api/external/orders/partner_orders_a/batch_one/status",
                          headers=_headers(token)).status_code == 401
    assert client.get("/api/runs/private-run", headers=_headers(LOADER)).status_code == 401


def test_loader_can_poll_only_bound_batch(monkeypatch):
    client = _client(monkeypatch)
    async def batch(feed, batch):
        assert (feed, batch) == ("partner_orders_a", "batch_one")
        return "run-one"
    class Graph:
        async def aget_state(self, *_):
            return SimpleNamespace(values={})
    monkeypatch.setattr(external, "_batch_run", batch)
    client.app.state.graph_app = Graph()
    response = client.get("/api/external/orders/partner_orders_a/batch_one/status",
                          headers=_headers(LOADER))
    assert response.status_code == 200
    assert response.json() == {"run_id": "run-one", "api_status": "running"}


def test_external_decision_requires_approver_and_browser_cannot_use_run_route(monkeypatch):
    client = _client(monkeypatch)
    async def batch(*_): return "run-one"
    async def resume(run, decision, note, *_):
        return {"run_id": run, "resumed_with": decision, "note": note}
    async def record(*_): return {"pipeline_env": "test:external:partner_orders_a",
                                  "source_ref": {"kind": "external_batch", "feed_id": "partner_orders_a"}}
    monkeypatch.setattr(external, "_batch_run", batch)
    monkeypatch.setattr(external, "_resume", resume)
    monkeypatch.setattr(runs, "_resume", resume)
    monkeypatch.setattr(runs.store, "get_run_record", record)
    class Graph:
        async def aget_state(self, *_): return SimpleNamespace(values={"run_id": "run-one"}, next=())
    client.app.state.graph_app = Graph()
    url = "/api/external/orders/partner_orders_a/batch_one/approve"
    for token in (SHARED, LOADER):
        assert client.post(url, headers=_headers(token)).status_code == 401
    result = client.post(url, headers=_headers(APPROVER), json={"note": "reviewed"})
    assert result.status_code == 200 and result.json()["note"] == "reviewed"
    assert client.post("/api/runs/run-one/approve", headers=_headers(SHARED)).status_code == 403
    assert client.post("/api/runs/run-one/reject", headers=_headers(LOADER)).status_code == 401
    assert client.post("/api/runs/run-one/reject", headers=_headers(APPROVER)).status_code == 200
    assert client.get("/api/runs/run-one", headers=_headers(SHARED)).status_code == 403
    assert client.get("/api/runs/run-one/approvals", headers=_headers(SHARED)).status_code == 403
    assert client.get("/api/runs/run-one/approvals", headers=_headers(LOADER)).status_code == 401


def test_demo_keeps_shared_token_for_existing_loader_and_approval(monkeypatch):
    client = _client(monkeypatch, "demo")
    async def batch(*_): return "run-one"
    async def resume(run, decision, *_): return {"run_id": run, "resumed_with": decision}
    monkeypatch.setattr(external, "_batch_run", batch)
    monkeypatch.setattr(external, "_resume", resume)
    client.app.state.graph_app = object()
    assert client.post("/api/external/orders/partner_orders_a/batch_one/approve",
                       headers=_headers(SHARED)).status_code == 200
    assert client.post("/api/external/orders/partner_orders_a/batch_one/approve",
                       headers=_headers(APPROVER)).status_code == 401


def test_production_cli_never_falls_back_to_demo_token(monkeypatch):
    import sys
    from scripts import orders_loader
    monkeypatch.setenv("AUTH_MODE", "production")
    monkeypatch.setenv("P4_AGENT_API_URL", "https://unused.example")
    monkeypatch.setenv("P4_AGENT_TOKEN", SHARED)
    monkeypatch.setenv("P4_LOADER_DATABASE_URL", "postgresql://unused/unused")
    monkeypatch.delenv("P5_LOADER_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("P5_APPROVER_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["orders_loader.py", "approve", "partner_orders_a", "batch_one"])
    with pytest.raises(ValueError, match="P5_APPROVER_TOKEN"):
        orders_loader.main()
    monkeypatch.setattr(sys, "argv", ["orders_loader.py", "retry", "partner_orders_a", "batch_one"])
    with pytest.raises(ValueError, match="P5_LOADER_SERVICE_TOKEN"):
        orders_loader.main()


@pytest.mark.asyncio
async def test_production_run_readiness_verifies_schema_without_ddl(monkeypatch):
    import asyncio
    settings = _settings()
    called = []
    async def verify(): called.append("verify")
    async def forbidden(): raise AssertionError("production attempted DDL")
    monkeypatch.setattr(runs, "get_settings", lambda: settings)
    monkeypatch.setattr(runs.store, "verify_runtime_schema", verify)
    monkeypatch.setattr(runs.store, "init_mapping_table", forbidden)
    monkeypatch.setattr(runs.store, "init_runs_table", forbidden)
    monkeypatch.setattr(runs.store, "init_component_tables", forbidden)
    monkeypatch.setattr(runs.checkpointer, "setup_checkpointer", forbidden)
    monkeypatch.setattr(runs, "_schema_ready", False)
    monkeypatch.setattr(runs, "_schema_lock", asyncio.Lock())
    await runs._ensure_checkpointer_ready()
    assert called == ["verify"]


def test_loader_role_policy_excludes_release_mapping_and_decision_writes():
    from scripts import verify_p5_roles
    class Connection:
        def __init__(self, grants): self.grants = grants
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def execute(self, sql, args=()):
            if "has_table_privilege" in sql:
                return SimpleNamespace(fetchone=lambda: (self.grants.get(tuple(args), False),))
            return SimpleNamespace(fetchone=lambda: (False,))
    grants = {(f"public.{table}", privilege): allowed for (table, privilege), allowed in {
        ("external_order_batches", "INSERT"): True,
        ("external_order_batches", "SELECT"): True,
        ("external_order_releases", "SELECT"): True,
        ("mapping_state", "SELECT"): True,
        ("published_orders", "INSERT"): True,
    }.items()}
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(verify_p5_roles.psycopg, "connect", lambda *_: Connection(grants))
        verify_p5_roles.verify_role("unused", "loader")
        grants[("public.external_order_releases", "INSERT")] = True
        with pytest.raises(AssertionError, match="external_order_releases INSERT"):
            verify_p5_roles.verify_role("unused", "loader")
    finally:
        monkeypatch.undo()


def test_api_role_policy_checks_column_grants(monkeypatch):
    from scripts import verify_p5_roles
    table_grants = {(f"public.{table}", privilege): True for table, privilege in (
        ("external_order_releases", "INSERT"), ("mapping_state", "INSERT"),
        ("approval_decisions", "INSERT"))}
    column_grants = {("public.external_order_batches", column, "UPDATE"): True
                     for column in ("agent_run_id", "dispatch_lease_until")}
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def execute(self, sql, args=()):
            allowed = column_grants.get(tuple(args), False) if "has_column_privilege" in sql else table_grants.get(tuple(args), False)
            return SimpleNamespace(fetchone=lambda: (allowed,))
    monkeypatch.setattr(verify_p5_roles.psycopg, "connect", lambda *_: Connection())
    verify_p5_roles.verify_role("unused", "api")
    column_grants[("public.external_order_batches", "payload", "UPDATE")] = True
    with pytest.raises(AssertionError, match="can update.*payload"):
        verify_p5_roles.verify_role("unused", "api")


def test_browser_cannot_read_external_chat_or_cross_scope_analytics_or_label(monkeypatch):
    import config
    from services import store
    settings = _settings()
    app = FastAPI()
    app.include_router(chat.router)
    app.include_router(analytics.router)
    app.state.graph_app = object()
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(analytics, "get_settings", lambda: settings)
    async def record(*_):
        return {"pipeline_env": "test:external:partner_orders_a",
                "source_ref": {"kind": "external_batch", "feed_id": "partner_orders_a"}}
    async def scope(*_): return await record()
    monkeypatch.setattr(store, "get_run_record", record)
    monkeypatch.setattr(analytics.store, "component_event_scope", scope)
    with TestClient(app) as client:
        h = _headers(SHARED)
        result = client.post("/api/chat/external-run", headers=h,
                             json={"messages": [{"role": "user", "content": "show details"}]})
        assert result.status_code == 403
        result = client.get("/api/analytics/components?pipeline_env=test:external:partner_orders_a", headers=h)
        assert result.status_code == 403
        result = client.post("/api/analytics/components/4/label", headers=h,
                             json={"correct": True, "labeled_by": "browser"})
        assert result.status_code == 403
