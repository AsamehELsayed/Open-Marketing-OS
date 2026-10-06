from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.database import repos
from app.database.sqlite import connect
from app.main import create_app
import app.services.config_service as config_module
from app.services.config_service import ConfigService
secret_store = importlib.import_module("app.services.credentials.store")
from app.services.integrations import registry
from app.services.social.instagram import capabilities as instagram_capabilities

ROOT = Path(__file__).resolve().parents[2]

CLEAR_ENV = (
    "OPENAI_API_KEY", "OPENROUTER_API_KEY", "APIFY_API_TOKEN",
    "BRIGHTDATA_API_KEY", "BRIGHTDATA_API_TOKEN", "BRIGHTDATA_IG_DATASET",
    "META_IG_ACCESS_TOKEN", "META_IG_ACCOUNT_ID", "META_OAUTH_CLIENT_ID",
    "META_APP_ID", "META_OAUTH_CLIENT_SECRET", "META_APP_SECRET",
    "MANAGER_PROVIDER", "MANAGER_MODE", "ROUTER_MODE", "AI_RUNTIME",
    "OMOS_CLOUD_PROVIDER", "OPENROUTER_TEST_MODEL", "CHROMA_ENABLED",
)


class MemoryBackend:
    name = "test-memory"

    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def put(self, key: str, plaintext: bytes) -> None:
        self.values[key] = bytes(plaintext)

    def get(self, key: str) -> bytes | None:
        return self.values.get(key)

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


@pytest.fixture()
def isolated_client(tmp_path, monkeypatch):
    db = tmp_path / "health.db"
    workspace = tmp_path / "workspace"
    vault_dir = tmp_path / "vault"
    workspace.mkdir()
    vault_dir.mkdir()
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setattr(deps, "ROOT", workspace)
    for name in CLEAR_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OMOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OMOS_CREDENTIALS_DIR", str(vault_dir))
    monkeypatch.setenv("CHROMA_ENABLED", "0")
    deps.init_db(db)
    previous_backend = secret_store._default
    secret_store.set_default_backend(MemoryBackend())
    config_module.clear_all_test_overrides()
    from app.services.llm import openrouter_provider
    openrouter_provider.clear_catalog_cache()
    from app.services.mcp import reset_gateway
    reset_gateway()
    monkeypatch.setattr(instagram_capabilities, "_i7_registered", False, raising=False)
    from app.routes import graph_runtime
    monkeypatch.setattr(graph_runtime, "_GRAPH", None, raising=False)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client, db
    openrouter_provider.clear_catalog_cache()
    reset_gateway()
    secret_store.set_default_backend(previous_backend)
    config_module.clear_all_test_overrides()


def _positive_state(value: Any) -> bool:
    return str(value or "").strip().lower() in {
        "ready", "healthy", "running", "active", "available", "verified", "ok",
    }


def test_fresh_config_keeps_all_settings_sections_honest(isolated_client):
    client, _ = isolated_client
    response = client.get("/api/settings/config")
    assert response.status_code == 200
    data = response.json()["data"]
    assert {
        "general", "ai", "integrations", "files_knowledge",
        "privacy_security", "system",
    } <= set(data)
    assert not _positive_state(data["ai"].get("local_ai_status"))
    assert data["ai"].get("openai_connected") is False
    assert data["ai"].get("openrouter_connected") is False
    assert not _positive_state(data["integrations"]["web_research"].get("status"))
    assert data["integrations"]["instagram_public"].get("browser_fallback") is not True
    assert not _positive_state(data["system"].get("status"))
    assert not _positive_state(data["system"].get("backend"))
    for provider in ("apify", "brightdata", "openrouter"):
        section = data["integrations"].get(provider, {})
        for key in ("credential", "provider", "capability", "provider_health", "capability_health"):
            if key in section:
                assert not _positive_state(section[key])


def test_liveness_and_component_readiness_are_separate_authorities(isolated_client):
    client, _ = isolated_client
    liveness = client.get("/health")
    assert liveness.status_code == 200
    assert liveness.json()["status"] == "ok"
    assert not any(word in liveness.text.lower() for word in ("healthy", "ready", "verified", "running"))
    component = client.get("/api/settings/system/health")
    assert component.status_code == 200
    data = component.json()["data"]
    assert not _positive_state(data.get("status"))
    assert not _positive_state(data.get("backend"))
    assert "model_runtime" in data


def test_browser_test_never_fabricates_verified_state(isolated_client):
    client, _ = isolated_client
    response = client.post("/api/settings/credentials/test", json={"target": "browser"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert not _positive_state(data.get("capability_health"))
    assert not _positive_state(data.get("provider_health"))
    assert not data.get("last_checked_at")


def test_mcp_is_unknown_until_a_real_health_probe():
    from app.services.mcp.gateway import McpGateway

    class Transport:
        def __init__(self):
            self.calls = 0

        def health(self, endpoint, *, timeout_s):
            self.calls += 1
            return {"healthy": True, "latency_ms": 3}

    transport = Transport()
    gateway = McpGateway(transport=transport)
    gateway.register_mcp_server("fixture-server", "loopback://mcp", "project")
    before = gateway.mcp_status("starter")[0]
    assert before["connected"] is False
    assert before["last_health"] == "unknown"
    assert before["last_checked_at"] is None
    after = gateway.check_mcp_health("fixture-server")
    assert transport.calls == 1
    assert after["connected"] is True
    assert after["status"] == "healthy"


def test_workspace_reindex_uses_the_existing_root_boundary(isolated_client, monkeypatch):
    client, db = isolated_client
    from app.services import state as store

    workspace = Path(deps.ROOT)
    (workspace / "knowledge").mkdir()
    (workspace / "knowledge" / "fixture.md").write_text("# Fixture\n\nEvidence.", encoding="utf-8")

    def fake_build_index(conn, root, *args, **kwargs):
        assert Path(root) == workspace
        return {"mode": "FTS_ONLY", "indexed": 1, "skipped": 0, "deleted": 0, "quarantined": [], "chunks": 1}

    monkeypatch.setattr(store, "build_index", fake_build_index)
    response = client.post("/api/settings/workspace/reindex", json={"project_id": "starter"})
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "indexed"


def test_model_call_tool_run_and_sse_invariants_remain_project_scoped(isolated_client):
    _, db = isolated_client
    from app.contracts.events import GraphExecutionEvent
    from app.contracts.model_call import ModelCall
    from app.graphs.tool_capability import persist_tool_run

    with connect(db) as conn:
        call = ModelCall(
            turn_id="turn-health",
            project_id="starter",
            call_id="call-health",
            provider="local",
            model="local-model",
            route_mode="LOCAL",
            route_reason="fixture",
        )
        assert call.input_tokens is None
        assert call.total_tokens is None
        assert call.local_api_cost_usd() == 0.0
        repos.ModelCalls.insert(conn, call.model_dump())
        tool_run_id = persist_tool_run(
            conn,
            project_id="starter",
            tool_id="instagram_public_profile",
            provider="apify",
            status="success",
            latency_ms=2,
        )
        assert tool_run_id
        assert repos.ToolRuns.for_tool(conn, "instagram_public_profile", "starter")[0]["status"] == "success"
        assert repos.ToolRuns.for_tool(conn, "instagram_public_profile", "other") == []
        event = GraphExecutionEvent(
            project_id="starter",
            conversation_id="conversation-health",
            turn_id="turn-health",
            event_type="model_delta",
            label="Writing response",
            detail="Alpha",
            metadata={"delta": "Alpha", "sequence": 1, "token": "must-not-persist", "api_key": "must-not-persist"},
        )
        assert "token" not in event.metadata
        assert "api_key" not in event.metadata
        sse = event.to_sse(id=7)
        assert sse["id"] == 7
        assert sse["event"] == "model_delta"
        assert sse["data"]["delta"] == "Alpha"
        assert "must-not-persist" not in json.dumps(sse)


def test_instagram_failure_uses_fake_transport_and_redacts_provider_detail(isolated_client, monkeypatch):
    _, db = isolated_client
    from app.services.integrations import registry
    from app.services.social.instagram.apify_provider import ApifyProvider

    token = "vault-apify-failure"
    calls: list[str] = []

    class FailingClient:
        def run_actor_sync(self, actor_id, payload, *, token, timeout):
            calls.append(token)
            raise RuntimeError(f"Bearer {token} https://api.apify.com failure Traceback")

        def classify_error(self, exc):
            return "AUTH_ERROR"

    with connect(db) as conn:
        ref = ConfigService.store_apify_token(token, conn=conn)
        registry.register_integration(
            registry.IntegrationRecord(
                integration_id="apify-integration",
                capability=instagram_capabilities.CAP_INSTAGRAM_PUBLIC_PROFILE,
                provider="apify",
                scope="installation",
                status="connected",
                config={"actor_id": "apify/instagram-profile-scraper"},
                secret_ref=ref,
                last_health=None,
            ),
            conn=conn,
        )
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    provider = ApifyProvider(FailingClient())
    result = provider.audit_public("acme_test", project_id="starter")
    assert calls == [token]
    assert result["evidence"]["status"] == "unavailable"
    text = json.dumps(result)
    assert token not in text
    assert "api.apify.com" not in text
    assert "Traceback" not in text
