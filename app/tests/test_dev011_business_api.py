"""DEV-011 backend contract for first-run business creation."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import deps
    from app.main import create_app

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(deps, "ROOT", workspace)
    monkeypatch.setattr(deps, "DB_PATH", tmp_path / "data" / "marketing.db")
    with TestClient(create_app()) as test_client:
        yield test_client


def test_business_creation_persists_supported_fields_and_is_retry_safe(client, tmp_path):
    from app import deps
    from app.database import repos
    from app.database.identity import DEFAULT_PROJECT_ID
    from app.services import state

    payload = {
        "name": "  Acme Test Business  ",
        "website": "example.com/about",
        "context": "  Small test company for onboarding.  ",
        "primary_market": "  Egypt  ",
    }
    first = client.post("/api/onboarding/business", json=payload)
    second = client.post("/api/onboarding/business", json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["project"]["id"] == DEFAULT_PROJECT_ID
    assert second.json()["data"]["project"]["id"] == DEFAULT_PROJECT_ID

    with deps.get_db() as conn:
        project = repos.Projects.get(conn, DEFAULT_PROJECT_ID)
        company = repos.Companies.get(conn, DEFAULT_PROJECT_ID)
        assert len(repos.Projects.list(conn)) == 1
        assert project["name"] == company["name"] == "Acme Test Business"
        assert project["website"] == company["website"] == "https://example.com/about"
        assert project["goal"] == "Small test company for onboarding."
        assert state.active_project_id(conn) == DEFAULT_PROJECT_ID

    company_file = (Path(deps.ROOT) / "company" / "company.yaml").read_text(encoding="utf-8")
    assert '"Egypt"' in company_file


def test_blank_or_missing_business_name_has_friendly_validation(client):
    for payload in ({}, {"name": "   "}):
        response = client.post("/api/onboarding/business", json=payload)
        assert response.status_code in {400, 422}
        assert "name" in response.text.lower()
        assert "traceback" not in response.text.lower()


@pytest.mark.parametrize("website", ["example.com", "http://example.com", "HTTPS://example.com/path"])
def test_supported_website_forms_are_normalized(client, website):
    response = client.post("/api/onboarding/business", json={"name": "Example Co", "website": website})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["project"]["website"].startswith(("http://", "https://"))


@pytest.mark.parametrize("website", ["ftp://example.com", "https://", "https:///missing-host"])
def test_unsupported_or_malformed_website_has_friendly_error(client, website):
    response = client.post("/api/onboarding/business", json={"name": "Example Co", "website": website})
    assert response.status_code == 400
    assert "website" in response.json()["detail"].lower()
    assert "traceback" not in response.text.lower()


def test_existing_active_project_identity_and_rows_are_preserved(client):
    from app import deps
    from app.database import repos
    from app.services import state

    with deps.get_db() as conn:
        existing = state.create_project(conn, "Legacy Workspace", goal="Keep this record")
        state.set_active_project(conn, existing["id"])
        initial_ids = {p["id"] for p in repos.Projects.list(conn)}

    response = client.post("/api/onboarding/business", json={"name": "Updated Business"})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["project"]["id"] == existing["id"]
    with deps.get_db() as conn:
        after_ids = {p["id"] for p in repos.Projects.list(conn)}
        project = repos.Projects.get(conn, existing["id"])
        assert after_ids == initial_ids
        assert project["id"] == existing["id"]
        assert project["goal"] == "Keep this record"
        assert state.active_project_id(conn) == existing["id"]
