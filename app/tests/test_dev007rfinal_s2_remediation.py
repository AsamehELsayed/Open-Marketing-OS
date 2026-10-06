from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.database import repos
from app.database.sqlite import connect
from app.main import create_app
import app.services.config_service as config_module
from app.services.config_service import ConfigService
from app.services.credentials import vault as vault_api
from app.services.integrations import registry
from app.services.social.instagram import capabilities as instagram_capabilities
from app.services.social.instagram import oauth as meta_oauth

secret_store = importlib.import_module("app.services.credentials.store")


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
def isolated(tmp_path, monkeypatch):
    db = tmp_path / "s2.db"
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    monkeypatch.setattr(deps, "DB_PATH", db)
    for name in (
        "OPENAI_API_KEY", "OPENROUTER_API_KEY", "APIFY_API_TOKEN",
        "BRIGHTDATA_API_KEY", "BRIGHTDATA_API_TOKEN", "BRIGHTDATA_IG_DATASET",
        "META_IG_ACCESS_TOKEN", "META_IG_ACCOUNT_ID", "META_OAUTH_CLIENT_ID",
        "META_APP_ID", "META_OAUTH_CLIENT_SECRET", "META_APP_SECRET",
        "MANAGER_PROVIDER", "ROUTER_MODE", "LLAMA_BASE_URL", "OMOS_DATA_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OMOS_CREDENTIALS_DIR", str(vault_dir))
    deps.init_db(db)
    previous = secret_store._default
    secret_store.set_default_backend(MemoryBackend())
    config_module.clear_all_test_overrides()
    from app.services.llm import openrouter_provider
    openrouter_provider.clear_catalog_cache()
    from app.services.mcp import reset_gateway
    reset_gateway()
    from app.routes import graph_runtime
    monkeypatch.setattr(graph_runtime, "_GRAPH", None, raising=False)
    monkeypatch.setattr(instagram_capabilities, "_i7_registered", False, raising=False)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client, db
    openrouter_provider.clear_catalog_cache()
    reset_gateway()
    secret_store.set_default_backend(previous)
    config_module.clear_all_test_overrides()


def test_openrouter_settings_round_trip_and_cloud_mode_uses_connected_provider(isolated):
    client, db = isolated
    with connect(db) as conn:
        ConfigService.store_openrouter_key("s2-openrouter-key", conn=conn)
    response = client.post(
        "/api/settings/ai",
        json={
            "ai_mode": "CLOUD",
            "manager_provider": "auto",
            "openrouter_default_model": "fixture/openrouter-model",
        },
    )
    assert response.status_code == 200
    config = client.get("/api/settings/config").json()["data"]
    assert config["ai"]["ai_mode"] == "CLOUD"
    assert config["ai"]["manager_provider"] == "auto"
    assert config["ai"]["openrouter_default_model"] == "fixture/openrouter-model"

    from app.routes import graph_runtime
    from app.services.llm.base import LLMResponse

    class Router:
        def __init__(self):
            self.mode = ""
            self.opts = {}

        def complete(self, conn, **kwargs):
            self.mode = kwargs["mode"]
            self.opts = kwargs.get("opts") or {}
            return LLMResponse(text="ok", tool_calls=[], usage={}), SimpleNamespace(
                call_id="s2-call", model_dump=lambda: {},
            )

    router = Router()
    complete = graph_runtime._make_complete_fn(router)
    assert complete(
        turn_id="s2-turn", project_id="starter", user_request="hello",
        route="state_only", conversation_id="s2-conversation",
    ) == "ok"
    assert router.mode == "OPENROUTER"

    from app.services.llm.model_router import ModelRouter

    class Provider:
        name = "openrouter"
        model = "fallback/model"

        def __init__(self):
            self.opts = {}

        def complete(self, *, system, messages, tools, opts=None):
            self.opts = dict(opts or {})
            return LLMResponse(text="ok", tool_calls=[], usage={})

    provider = Provider()
    configured_router = ModelRouter(
        openrouter_provider=provider,
        default_openrouter_model="fallback/model",
    )
    with connect(db) as conn:
        configured_router.complete(
            conn, turn_id="s2-model-turn", project_id="starter", system="s",
            messages=[{"role": "user", "content": "hello"}], tools=[],
            mode="OPENROUTER",
        )
    assert provider.opts["model"] == "fixture/openrouter-model"


def test_meta_connect_test_disconnect_use_project_scoped_oauth_registry_and_vault(isolated, monkeypatch):
    client, db = isolated
    token = "s2-meta-access-token"
    response = client.post(
        "/api/settings/credentials/connect",
        json={
            "target": "meta",
            "token": token,
            "project_id": "starter",
            "ig_account_id": "ig-account-1",
        },
    )
    assert response.status_code == 200
    assert token not in response.text
    with connect(db) as conn:
        view = registry.resolve_integration(
            instagram_capabilities.CAP_INSTAGRAM_OWNED_INSIGHTS, "starter", conn=conn,
        )
        assert view.provider == "meta"
        assert view.scope == "project"
        assert view.status == "connected"
        assert view.config["ig_account_id"] == "ig-account-1"
        assert view.secret_ref and view.secret_ref.startswith("vault://project/")
        assert vault_api.resolve(view.secret_ref, conn=conn) == token
    assert token.encode() not in db.read_bytes()

    from app.services.social.instagram.meta_provider import MetaProvider

    monkeypatch.setattr(
        MetaProvider,
        "audit_owned",
        lambda self, username="", *, project_id=None: {
            "account": {"username": "starter"},
            "posts": [{"id": "post-1"}],
            "insights": {"series": []},
            "evidence": {"source": "meta", "status": "verified", "collected_at": "now"},
            "unknowns": [],
        },
    )
    tested = client.post(
        "/api/settings/credentials/test",
        json={"target": "meta", "project_id": "starter"},
    )
    assert tested.status_code == 200
    data = tested.json()["data"]
    assert data["capability_health"] == "verified"
    assert data["provider_health"] == "healthy"
    assert token not in tested.text

    disconnected = client.post(
        "/api/settings/credentials/disconnect",
        json={"target": "meta", "project_id": "starter"},
    )
    assert disconnected.status_code == 200
    with connect(db) as conn:
        view = registry.resolve_integration(
            instagram_capabilities.CAP_INSTAGRAM_OWNED_INSIGHTS, "starter", conn=conn,
        )
        assert view.status == "not_configured"
        assert view.secret_ref is None
        assert vault_api.get_ref_row(view.secret_ref or "vault://project/" + "0" * 32, conn=conn) is None


def test_meta_authorization_code_path_uses_existing_oauth_service(isolated, monkeypatch):
    client, db = isolated
    token = "s2-meta-code-access-token"
    monkeypatch.setattr(
        meta_oauth,
        "_default_transport",
        lambda url, params: {"access_token": token, "scope": ["instagram_basic"]},
    )
    response = client.post(
        "/api/settings/credentials/connect",
        json={
            "target": "meta",
            "project_id": "starter",
            "ig_account_id": "ig-account-code",
            "code": "s2-auth-code",
            "client_id": "s2-client-id",
            "client_secret": "s2-client-secret",
            "redirect_uri": "https://localhost/settings/callback",
        },
    )
    assert response.status_code == 200
    assert token not in response.text
    with connect(db) as conn:
        view = registry.resolve_integration(
            instagram_capabilities.CAP_INSTAGRAM_OWNED_INSIGHTS, "starter", conn=conn,
        )
        assert view.status == "connected"
        assert view.config["ig_account_id"] == "ig-account-code"
        assert vault_api.resolve(view.secret_ref, conn=conn) == token


def test_brightdata_connect_persists_dataset_and_test_uses_real_provider_boundary(isolated, monkeypatch):
    client, db = isolated
    response = client.post(
        "/api/settings/credentials/connect",
        json={
            "target": "brightdata",
            "token": "s2-brightdata-key",
            "dataset_id": "dataset-s2",
        },
    )
    assert response.status_code == 200
    with connect(db) as conn:
        view = registry.resolve_integration(
            instagram_capabilities.CAP_INSTAGRAM_PUBLIC_PROFILE, "starter", conn=conn,
        )
        assert view.provider == "brightdata"
        assert view.config["dataset_id"] == "dataset-s2"
        assert view.status == "connected"

    updated = client.post(
        "/api/settings/credentials/connect",
        json={"target": "brightdata", "dataset_id": "dataset-s2"},
    )
    assert updated.status_code == 200
    with connect(db) as conn:
        view = registry.resolve_integration(
            instagram_capabilities.CAP_INSTAGRAM_PUBLIC_PROFILE, "starter", conn=conn,
        )
        assert view.config["dataset_id"] == "dataset-s2"

    from app.services.social.instagram.brightdata_provider import BrightDataProvider

    calls = []

    def test_connection(self, project_id=None):
        calls.append(project_id)
        return {
            "credential_status": "connected",
            "provider_health": "healthy",
            "capability_health": "verified",
            "detail": "Authenticated; bounded provider check passed",
            "last_checked_at": "2026-09-24T12:00:00+00:00",
        }

    monkeypatch.setattr(BrightDataProvider, "test_connection", test_connection)
    tested = client.post(
        "/api/settings/credentials/test",
        json={"target": "brightdata", "project_id": "starter"},
    )
    assert tested.status_code == 200
    data = tested.json()["data"]
    assert calls == ["starter"]
    assert data["capability_health"] == "verified"
    config = client.get("/api/settings/config").json()["data"]
    assert config["integrations"]["instagram_public"]["brightdata_dataset_configured"] is True
    assert config["integrations"]["instagram_public"]["brightdata_dataset_id"] == "dataset-s2"


def test_brightdata_test_is_bounded_and_uses_registry_dataset(isolated, monkeypatch):
    client, db = isolated
    response = client.post(
        "/api/settings/credentials/connect",
        json={"target": "brightdata", "token": "s2-brightdata-key", "dataset_id": "dataset-s2"},
    )
    assert response.status_code == 200
    from app.services.social.instagram.brightdata_provider import BrightDataProvider

    calls = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"datasets": []}

    def fake_get(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr("app.services.social.instagram.brightdata_provider.httpx.get", fake_get)
    result = BrightDataProvider().test_connection(project_id="starter")
    assert result["credential_status"] == "connected"
    assert result["provider_health"] == "healthy"
    assert result["capability_health"] == "verified"
    assert calls and calls[0]["headers"]["Authorization"] == "Bearer s2-brightdata-key"
    assert calls[0]["params"]["dataset_id"] == "dataset-s2"


def test_default_mcp_gateway_registers_persists_and_waits_for_health(isolated, monkeypatch):
    client, db = isolated
    from app.services import mcp
    from app.services.mcp import get_gateway, reset_gateway

    reset_gateway()
    gateway = get_gateway()
    assert gateway._transport is not None
    gateway.register_mcp_server("s2-server", "https://mcp.example.test/rpc", "project:starter")
    before = gateway.mcp_status("starter")[0]
    assert before["connected"] is False
    assert before["last_health"] == "unknown"
    assert before["last_checked_at"] is None
    with connect(db) as conn:
        row = repos.McpServers.get(conn, "s2-server")
        assert row is not None
        assert row["connected"] == 0
        assert row["last_health"] == "unknown"

    def health(endpoint, *, timeout_s):
        assert endpoint == "https://mcp.example.test/rpc"
        assert timeout_s == 5
        return {"healthy": True, "latency_ms": 2}

    monkeypatch.setattr(gateway._transport, "health", health)
    result = gateway.check_mcp_health("s2-server")
    assert result["connected"] is True
    assert result["status"] == "healthy"
    with connect(db) as conn:
        row = repos.McpServers.get(conn, "s2-server")
        assert row["connected"] == 1
        assert row["last_health"] == "healthy"
        assert row["last_checked_at"]

    reset_gateway()
    restored = get_gateway()
    restored_status = [row for row in restored.mcp_status("starter") if row["server_id"] == "s2-server"]
    assert restored_status and restored_status[0]["last_health"] == "healthy"


def test_mcp_registration_rejects_inactive_vault_ref(isolated):
    client, db = isolated
    from app.services.mcp import get_gateway, reset_gateway

    reset_gateway()
    gateway = get_gateway()
    with pytest.raises(ValueError):
        gateway.register_mcp_server(
            "invalid-ref", "https://mcp.example.test/rpc", "project:starter",
            secret_ref="vault://project/" + "0" * 32,
        )


def test_openai_vision_key_uses_config_service_authority_label(isolated):
    client, db = isolated
    with connect(db) as conn:
        ConfigService.store_openai_api_key("s2-openai-key", conn=conn)
    from app.services.vision.openai_provider import resolve_openai_key

    assert resolve_openai_key() == "s2-openai-key"
    from app.services.vision.openai_provider import OpenAIVisionProvider

    with connect(db) as conn:
        ConfigService.clear_openai_api_key(conn=conn)
    with pytest.raises(Exception) as exc:
        resolve_openai_key()
    assert "vault://" not in str(exc.value)


def test_settings_exposes_honest_unavailable_vision_projection(isolated):
    client, _ = isolated
    with connect(deps.DB_PATH) as conn:
        ConfigService.store_openai_api_key("s2-openai-key", conn=conn)
    response = client.get("/api/settings/config")
    assert response.status_code == 200
    vision = response.json()["data"]["integrations"]["vision"]
    assert vision["status"] in {"unavailable", "unknown"}
    assert vision["capability_health"] not in {"ready", "healthy", "available", "verified"}
    assert vision["credential_status"] == "connected"
    assert "fake" not in json.dumps(vision).lower()


def test_normal_settings_projection_omits_local_url_and_runtime_path(isolated, monkeypatch):
    client, db = isolated
    with connect(db) as conn:
        ConfigService.set_setting("llama_base_url", "http://127.0.0.1:65535", conn=conn)
    response = client.get("/api/settings/config")
    assert response.status_code == 200
    data = response.json()["data"]
    assert "data_dir" not in data["general"]
    assert "llama_base_url" not in data["ai"]
    assert "http://127.0.0.1:65535" not in response.text
    assert str(deps.DB_PATH) not in response.text
    from app.services.llm.local_config import config_from_env
    assert config_from_env().base_url == "http://127.0.0.1:65535"


def test_s3_credential_payloads_match_current_meta_and_mcp_api(isolated, monkeypatch):
    client, db = isolated
    meta = client.post(
        "/api/settings/credentials/connect",
        json={
            "target": "meta",
            "token": "s3-meta-token",
            "project_id": "starter",
            "ig_account_id": "s3-account",
            "config": {"ig_account_id": "s3-account"},
        },
    )
    assert meta.status_code == 200
    with connect(db) as conn:
        view = registry.resolve_integration(
            instagram_capabilities.CAP_INSTAGRAM_OWNED_INSIGHTS, "starter", conn=conn,
        )
        assert view.config["ig_account_id"] == "s3-account"
    config = client.get("/api/settings/config?project_id=starter").json()["data"]
    assert config["integrations"]["meta_insights"]["account_id"] == "s3-account"
    assert config["integrations"]["meta_insights"]["ig_account_id"] == "s3-account"

    mcp = client.post(
        "/api/settings/credentials/connect",
        json={
            "target": "mcp",
            "project_id": "starter",
            "scope": "project",
            "server_id": "s3-server",
            "endpoint": "https://mcp.example.test/rpc",
            "config": {
                "server_id": "s3-server",
                "endpoint": "https://mcp.example.test/rpc",
                "scope": "project",
            },
        },
    )
    assert mcp.status_code == 200
    assert "https://mcp.example.test/rpc" not in mcp.text
    with connect(db) as conn:
        row = repos.McpServers.get(conn, "s3-server")
        assert row is not None
        assert row["scope"] == "project:starter"
        assert row["connected"] == 0
        assert row["last_health"] == "unknown"

    from app.services import mcp
    gateway = mcp.get_gateway()
    monkeypatch.setattr(
        gateway._transport,
        "health",
        lambda endpoint, *, timeout_s: {"healthy": True, "latency_ms": 1},
    )
    tested = client.post(
        "/api/settings/credentials/test",
        json={"target": "mcp", "project_id": "starter", "server_id": "s3-server"},
    )
    assert tested.status_code == 200
    data = tested.json()["data"]
    assert data["server_id"] == "s3-server"
    assert data["provider_health"] == "healthy"
    assert data["connected"] is True
    config_text = client.get(
        "/api/settings/config?project_id=starter"
    ).text
    assert "https://mcp.example.test/rpc" not in config_text
    assert "s3-meta-token" not in config_text
    assert "vault://" not in config_text


def test_default_mcp_transport_is_offline_safe(isolated, monkeypatch):
    import httpx
    from app.services import mcp

    gateway = mcp.get_gateway()
    gateway.register_mcp_server(
        "offline-server", "https://mcp.example.test/rpc", "project:starter",
    )
    calls = []

    def record_post(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("default MCP transport must not make network calls")

    monkeypatch.setattr(httpx, "post", record_post)
    result = gateway.check_mcp_health("offline-server")
    assert calls == []
    assert result["connected"] is False
    assert result["status"] in {"unhealthy", "unavailable"}
