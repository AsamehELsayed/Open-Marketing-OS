from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from app import deps
import app.services.config_service as config_module
from app.database.sqlite import connect
from app.main import create_app
from app.services.config_service import ConfigService
from app.services.llm import openrouter_provider as orp

secret_store = importlib.import_module("app.services.credentials.store")


class MemoryBackend:
    name = "test-memory"

    def __init__(self):
        self.values = {}

    def put(self, key, plaintext):
        self.values[key] = bytes(plaintext)

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        self.values.pop(key, None)


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    db = tmp_path / "openrouter.db"
    vault_dir = tmp_path / "credentials"
    vault_dir.mkdir()
    monkeypatch.setattr(deps, "DB_PATH", db)
    for name in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "OMOS_CLOUD_PROVIDER", "OPENROUTER_CATALOG_TTL_S", "OPENROUTER_TEST_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OMOS_CREDENTIALS_DIR", str(vault_dir))
    deps.init_db(db)
    previous = secret_store._default
    secret_store.set_default_backend(MemoryBackend())
    config_module.clear_all_test_overrides()
    orp.clear_catalog_cache()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client, db
    orp.clear_catalog_cache()
    config_module.clear_all_test_overrides()
    secret_store.set_default_backend(previous)


def test_connect_stores_in_vault_not_plaintext(isolated):
    _, db = isolated
    with connect(db) as conn:
        ref = ConfigService.store_openrouter_key("fake-or-secret", conn=conn)
    assert ref.startswith("vault://")
    assert b"fake-or-secret" not in db.read_bytes()


def test_connect_never_echoes_token(isolated):
    client, _ = isolated
    response = client.post("/api/settings/credentials/connect", json={"target": "openrouter", "token": "fake-or-token"})
    assert response.status_code == 200
    assert "fake-or-token" not in response.text
    client.post("/api/settings/credentials/disconnect", json={"target": "openrouter"})
    assert ConfigService.is_openrouter_configured() is False


def test_config_has_openrouter_section(isolated):
    client, _ = isolated
    response = client.get("/api/settings/config")
    assert response.status_code == 200
    section = response.json()["data"]["integrations"]["openrouter"]
    assert section["capability"] in {"verified", "not_verified", "configuration_error", "unknown"}
    assert section["credential"] in {"connected", "not_configured", "auth_error"}


def test_apify_capability_not_verified_from_presence_alone(isolated):
    client, _ = isolated
    response = client.get("/api/settings/config")
    assert response.status_code == 200
    assert response.json()["data"]["integrations"]["instagram_public"]["apify_capability_health"] == "unknown"


def test_test_target_unknown_400(isolated):
    client, _ = isolated
    assert client.post("/api/settings/credentials/test", json={"target": "nosuch"}).status_code == 400


def _openrouter_test(isolated, monkeypatch, behavior):
    client, db = isolated
    with connect(db) as conn:
        ConfigService.store_openrouter_key("fake-or-key", conn=conn)
    monkeypatch.setattr(orp, "test_connection", lambda **kwargs: behavior)
    return client.post("/api/settings/credentials/test", json={"target": "openrouter"})


def test_openrouter_test_auth_error_tri_state(isolated, monkeypatch):
    response = _openrouter_test(isolated, monkeypatch, {
        "credential_status": "auth_error", "provider_health": "error",
        "capability_health": "unknown", "detail": "OpenRouter rejected the stored credential",
        "last_checked_at": "2026-09-24T01:00:00+00:00",
    })
    data = response.json()["data"]
    assert data["credential_status"] == "auth_error"
    assert data["capability_health"] == "unknown"
    assert data["configured"] is False
    assert ConfigService.get_setting("health_openrouter_credential") == "auth_error"
    assert ConfigService.get_setting("health_openrouter_capability") == "unknown"
    assert ConfigService.get_setting("health_openrouter_last_checked").endswith("+00:00")


def test_openrouter_test_verified_persists(isolated, monkeypatch):
    response = _openrouter_test(isolated, monkeypatch, {
        "credential_status": "connected", "provider_health": "healthy",
        "capability_health": "verified", "model_count": 42,
        "actual_model": "fake/model", "detail": "Authenticated; bounded inference verified",
        "last_checked_at": "2026-09-24T01:00:00+00:00",
    })
    data = response.json()["data"]
    assert data["configured"] is True
    assert data["capability_health"] == "verified"
    assert data["actual_model"] == "fake/model"
    assert ConfigService.get_setting("health_openrouter_capability") == "verified"
    config = ConfigService.get_integrations_overview()
    assert config["openrouter"]["capability"] == "verified"
    assert config["openrouter"]["last_checked_at"] == "2026-09-24T01:00:00+00:00"


def test_disconnect_revokes_and_config_reports_not_connected(isolated):
    client, _ = isolated
    assert client.post("/api/settings/credentials/connect", json={"target": "openrouter", "token": "fake-or-key"}).status_code == 200
    assert ConfigService.is_openrouter_configured() is True
    assert client.post("/api/settings/credentials/disconnect", json={"target": "openrouter"}).status_code == 200
    assert ConfigService.is_openrouter_configured() is False
    assert client.get("/api/settings/config").json()["data"]["integrations"]["openrouter"]["connected"] is False


def test_model_catalog_ttl_cache(isolated, monkeypatch):
    set_override = config_module.set_test_override
    set_override("openrouter_key", "fake-or-key")
    calls = {"n": 0}
    fake = [{"id": "fake/model", "name": "Fake Model", "context_length": 128000, "modalities": ["text"], "supports_tools": True, "pricing": {"prompt": "0.00000015"}}]
    monkeypatch.setattr(orp, "_fetch_models", lambda: calls.__setitem__("n", calls["n"] + 1) or fake)
    monkeypatch.setenv("OPENROUTER_CATALOG_TTL_S", "3600")
    assert orp.model_catalog(refresh=True) == fake
    assert orp.model_catalog() == fake
    assert calls["n"] == 1
    config_module.clear_test_override("openrouter_key")
    orp.clear_catalog_cache()


def test_models_endpoint_fail_closed_without_credential(isolated):
    client, _ = isolated
    response = client.get("/api/ai/models")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["connected"] is False
    assert data["models"] == []


def test_models_endpoint_requires_openrouter(isolated):
    client, _ = isolated
    assert client.get("/api/ai/models?provider=gptx").status_code == 400


class _FakeOpenRouterProvider:
    name = "openrouter"
    model = "openrouter/auto"

    def complete(self, *, system, messages, tools, opts=None):
        from app.services.llm.base import LLMResponse
        return LLMResponse(text="hello world", tool_calls=[], usage={"input": 10, "output": 5, "cost_usd": 0.012, "requested_model": "openrouter/auto", "actual_model": "fake/model"})


def _fake_router():
    from app.services.llm.model_router import ModelRouter
    return ModelRouter(openrouter_provider=_FakeOpenRouterProvider(), default_local_model="local-default", default_openrouter_model="openrouter/auto")


def test_decide_route_openrouter_explicit():
    from app.services.llm.model_router import decide_route
    route = decide_route("OPENROUTER", openrouter_configured=True)
    assert route.provider == "openrouter"
    with pytest.raises(RuntimeError):
        decide_route("OPENROUTER", openrouter_configured=False)


def test_decide_route_openrouter_escalation():
    from app.services.llm.model_router import decide_route
    route = decide_route("AUTO", openrouter_configured=True, cloud_provider="openrouter", escalation_reason="local unavailable")
    assert route.provider == "openrouter"


def test_model_badge_data_has_cost_or_null(isolated):
    _, db = isolated
    with connect(db) as conn:
        ConfigService.store_openrouter_key("fake-or-key", conn=conn)
    router = _fake_router()
    with connect(db) as conn:
        response, call = router.complete(conn, turn_id="t1", project_id="starter", system="s", messages=[{"role": "user", "content": "hi"}], tools=[], mode="OPENROUTER")
    assert response.text == "hello world"
    assert call.provider == "openrouter"
    assert call.model == "fake/model"
    assert call.estimated_cost_usd == 0.012
    assert call.pricing_version == "provider-reported"
    assert call.route_mode == "OPENROUTER"
