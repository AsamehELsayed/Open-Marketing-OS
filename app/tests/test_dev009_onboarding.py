"""Focused DEV-009 tests for persisted Start Here completion state."""
import pytest
from fastapi.testclient import TestClient

from app import deps
from app.database import repos
from app.main import create_app
from app.services import state


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "dev009-onboarding.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    deps.init_db(db)
    with TestClient(create_app()) as test_client:
        yield test_client


def data(response):
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    return body["data"]


def test_intro_completion_is_persisted_and_idempotent(client):
    assert data(client.get("/api/onboarding/status"))["intro_version_seen"] == ""
    completed = data(client.post(
        "/api/onboarding/intro/complete", json={"version": "start-here-v1"}
    ))
    assert completed == {"intro_version_seen": "start-here-v1"}
    assert data(client.post(
        "/api/onboarding/intro/complete", json={"version": "start-here-v1"}
    )) == completed
    assert data(client.get("/api/onboarding/status"))["intro_version_seen"] == "start-here-v1"


def test_status_does_not_expose_credentials(client):
    payload = data(client.get("/api/onboarding/status"))
    serialized = str(payload).lower()
    assert "api_key" not in serialized
    assert "credential" not in serialized
    assert "token" not in serialized
    assert isinstance(payload["business_described"], bool)


def test_described_workspace_is_preserved_and_not_misclassified(client):
    with deps.get_db() as conn:
        project = state.create_project(conn, "Synthetic Demo Workspace", "https://demo.example", "Learn")
        state.set_active_project(conn, project["id"])
    payload = data(client.get("/api/onboarding/status"))
    assert payload["business_described"] is True
    assert payload["project"]["id"] == project["id"]
    assert payload["project"]["name"] == "Synthetic Demo Workspace"
    with deps.get_db() as conn:
        saved = repos.Projects.get(conn, project["id"])
    assert saved["name"] == "Synthetic Demo Workspace"
