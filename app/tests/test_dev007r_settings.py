from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.main import create_app
import app.services.config_service as config_module
from app.services.config_service import ConfigService

config_store = importlib.import_module("app.services.credentials.store")


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
def client(tmp_path, monkeypatch):
    db = tmp_path / "settings.db"
    vault_dir = tmp_path / "credentials"
    vault_dir.mkdir()
    monkeypatch.setattr(deps, "DB_PATH", db)
    for name in (
        "OPENAI_API_KEY", "OPENROUTER_API_KEY", "APIFY_API_TOKEN",
        "BRIGHTDATA_API_KEY", "BRIGHTDATA_API_TOKEN", "META_IG_ACCESS_TOKEN",
        "META_IG_ACCOUNT_ID", "MANAGER_PROVIDER", "ROUTER_MODE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OMOS_CREDENTIALS_DIR", str(vault_dir))
    deps.init_db(db)
    previous = config_store._default
    config_store.set_default_backend(MemoryBackend())
    config_module.clear_all_test_overrides()
    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        yield test_client
    config_store.set_default_backend(previous)
    config_module.clear_all_test_overrides()


def test_zero_config_empty_state_and_seven_sections(client):
    response = client.get("/api/settings/config")
    assert response.status_code == 200
    data = response.json()["data"]
    assert {"general", "ai", "integrations", "files_knowledge", "privacy_security", "system"} <= set(data)
    assert data["ai"]["openai_connected"] is False
    assert data["ai"]["openrouter_connected"] is False
    assert data["ai"]["local_ai_status"].lower() not in {"running", "healthy", "ready", "available", "verified"}
    assert data["integrations"]["instagram_public"]["connected"] is False
    assert data["integrations"]["meta_insights"]["connected"] is False
    assert data["system"]["backend"].lower() != "healthy"
    assert data["system"]["status"].lower() != "healthy"


def test_legacy_routes_303_redirect(client):
    for path, target in (
        ("/", "/app"),
        ("/legacy", "/app"),
        ("/settings", "/app/settings"),
        ("/system", "/app/settings?section=system"),
    ):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == target
        assert "<html" not in response.text.lower()


def test_openai_connect_test_disconnect_lifecycle(client, monkeypatch):
    response = client.post("/api/settings/credentials/connect", json={
        "target": "openai",
        "token": "fake-openai-token",
    })
    assert response.status_code == 200
    assert "fake-openai-token" not in response.text
    config = client.get("/api/settings/config").json()["data"]
    assert config["ai"]["openai_connected"] is True
    assert "fake-openai-token" not in str(config)

    class Models:
        def list(self):
            return []

    class OpenAI:
        def __init__(self, **kwargs):
            self.models = Models()

    import openai
    monkeypatch.setattr(openai, "OpenAI", OpenAI)
    test_response = client.post("/api/settings/credentials/test", json={"target": "openai"})
    assert test_response.status_code == 200
    assert test_response.json()["data"]["configured"] is True
    assert "fake-openai-token" not in test_response.text

    disconnect = client.post("/api/settings/credentials/disconnect", json={"target": "openai"})
    assert disconnect.status_code == 200
    assert client.get("/api/settings/config").json()["data"]["ai"]["openai_connected"] is False


def test_apify_connect_test_disconnect_lifecycle(client):
    response = client.post("/api/settings/credentials/connect", json={
        "target": "apify",
        "token": "fake-apify-token",
    })
    assert response.status_code == 200
    assert "fake-apify-token" not in response.text
    config = client.get("/api/settings/config").json()["data"]
    assert config["integrations"]["instagram_public"]["connected"] is True
    assert config["integrations"]["instagram_public"].get("apify_capability_health") in {
        "unknown", "not_verified", "not_configured",
    }
    disconnect = client.post("/api/settings/credentials/disconnect", json={"target": "apify"})
    assert disconnect.status_code == 200
    assert client.get("/api/settings/config").json()["data"]["integrations"]["instagram_public"]["connected"] is False


def test_ai_settings_update(client):
    response = client.post("/api/settings/ai", json={
        "ai_mode": "BASE",
        "active_model": "Llama-3.1-8B-Instruct",
        "marketing_adapter": "Custom Brand Adapter",
    })
    assert response.status_code == 200
    config = client.get("/api/settings/config").json()["data"]
    assert config["ai"]["ai_mode"] == "BASE"
    assert config["ai"]["active_model"] == "Llama-3.1-8B-Instruct"
    assert config["ai"]["marketing_adapter"] == "Custom Brand Adapter"


def test_general_settings_update(client):
    response = client.post("/api/settings/general", json={
        "theme": "dark",
        "auto_load_project": True,
        "language": "en",
    })
    assert response.status_code == 200
    config = client.get("/api/settings/config").json()["data"]
    assert config["general"]["theme"] == "dark"
    assert config["general"]["auto_load_project"] is True


def test_no_raw_environment_surface_in_settings_payload(client):
    response = client.get("/api/settings/config")
    assert response.status_code == 200
    text = response.text
    for token in (
        "OPENAI_API_KEY", "OPENROUTER_API_KEY", "APIFY_API_TOKEN",
        "BRIGHTDATA_API_KEY", "BRIGHTDATA_API_TOKEN", "META_IG_ACCESS_TOKEN",
        "AI_RUNTIME", "ROUTER_MODE", "OMOS_CLOUD_PROVIDER", "os.environ",
        "os.getenv", "load_dotenv", "env_symbol", "unimported_env", "migrate-env",
        "vault://", "Traceback",
    ):
        assert token not in text
