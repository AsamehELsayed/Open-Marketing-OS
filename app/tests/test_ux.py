from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.main import create_app
from app.services import state as store

ROOT = Path(__file__).resolve().parents[2]
JARGON = ("FTS_ONLY", "keyword only", "Retrieval:", "Chroma", "chroma", "OpenCode", "opencode", "FTS5", "vector", "Worker engine")
config_store = importlib.import_module("app.services.credentials.store")


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
def client(tmp_path, monkeypatch):
    db = tmp_path / "ux.db"
    vault_dir = tmp_path / "credentials"
    vault_dir.mkdir()
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setenv("OMOS_CREDENTIALS_DIR", str(vault_dir))
    for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "APIFY_API_TOKEN", "BRIGHTDATA_API_KEY", "BRIGHTDATA_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    deps.init_db(db)
    with deps.get_db(db) as conn:
        store.run_imports(conn, ROOT)
    previous = config_store._default
    config_store.set_default_backend(MemoryBackend())
    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        yield test_client
    config_store.set_default_backend(previous)


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    db = tmp_path / "fresh.db"
    vault_dir = tmp_path / "credentials"
    vault_dir.mkdir()
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setenv("OMOS_CREDENTIALS_DIR", str(vault_dir))
    for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "APIFY_API_TOKEN", "BRIGHTDATA_API_KEY", "BRIGHTDATA_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    deps.init_db(db)
    previous = config_store._default
    config_store.set_default_backend(MemoryBackend())
    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        yield test_client
    config_store.set_default_backend(previous)


def test_css_loads_with_design_system(client):
    response = client.get("/static/style.css")
    assert response.status_code == 200 and len(response.text) > 2000
    assert ":root" in response.text and "--accent" in response.text and ".sidebar" in response.text
    assert client.get("/legacy", follow_redirects=False).status_code == 303
    assert client.get("/app").status_code == 200


def test_primary_nav_and_advanced(client):
    redirects = {
        "/legacy": "/app",
        "/settings": "/app/settings",
        "/system": "/app/settings?section=system",
        "/chat": "/app/chat/new",
        "/campaigns": "/app/campaigns",
        "/approvals": "/app/approvals",
        "/results": "/app/results",
        "/brain": "/app/knowledge",
        "/tasks": "/app/jobs",
        "/jobs": "/app/jobs",
        "/companies": "/app",
    }
    for path, target in redirects.items():
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == target
        assert "<html" not in response.text.lower()


def test_no_jargon_on_authoritative_api_surface(client):
    config = client.get("/api/settings/config")
    assert config.status_code == 200
    for token in JARGON:
        assert token.lower() not in config.text.lower()


def test_home_dashboard(client):
    response = client.get("/legacy", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/app"
    spa = client.get("/app")
    assert spa.status_code == 200 and 'id="root"' in spa.text


def test_home_empty_state(fresh):
    response = fresh.get("/legacy", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/app"
    spa = fresh.get("/app")
    assert spa.status_code == 200 and 'id="root"' in spa.text


def test_approvals_empty_state_uses_react_api(fresh):
    response = fresh.get("/api/approvals")
    assert response.status_code == 200
    assert response.json()["data"] == []


def test_chat_form_transport_is_preserved(client):
    response = client.post("/chat/new", data={"project_id": "starter"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/chat/")


def test_campaign_data_uses_react_api(client):
    response = client.get("/api/campaigns?project_id=starter")
    assert response.status_code == 200
    assert isinstance(response.json()["data"], list)


def test_system_health_advanced(client):
    response = client.get("/system", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/app/settings?section=system"
    health = client.get("/api/settings/system/health")
    assert health.status_code == 200
    data = health.json()["data"]
    assert data["status"].lower() != "healthy"
    assert data["backend"].lower() != "healthy"
    assert "model_runtime" in data


def test_results_does_not_invent_decision_data(client):
    response = client.get("/api/results/summary?project_id=starter")
    assert response.status_code == 200
    data = response.json()["data"]
    assert "decisions" in data
    assert data["decisions"] == []
