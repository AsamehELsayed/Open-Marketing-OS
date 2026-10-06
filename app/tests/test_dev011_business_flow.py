"""DEV-011: project identity carried into Account Manager read surfaces."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import deps
    from app.main import create_app

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(deps, "ROOT", workspace)
    monkeypatch.setattr(deps, "DB_PATH", tmp_path / "db" / "marketing.db")
    with TestClient(create_app()) as test_client:
        yield test_client


def test_onboarding_selection_is_persisted_into_first_account_manager_conversation(client):
    from app import deps
    from app.database import repos
    from app.services import state

    created = client.post("/api/onboarding/business", json={
        "name": "Acme Flow Test", "website": "example.com",
        "context": "Test first-run project binding",
    })
    assert created.status_code == 200, created.text
    selected_id = created.json()["data"]["project"]["id"]

    # This is the same project_id sent by the SPA when opening Account Manager.
    response = client.post("/chat/new", data={"project_id": selected_id},
                           follow_redirects=False)
    assert response.status_code == 303
    conversation_id = response.headers["location"].rsplit("/", 1)[-1]

    with deps.get_db() as conn:
        conversation = repos.Conversations.get(conn, conversation_id)
        assert conversation["project_id"] == selected_id
        assert conversation["company_id"] == selected_id
        assert state.active_project_id(conn) == selected_id

    # A fresh status request models reload and must resolve the same stored ID.
    status = client.get("/api/onboarding/status").json()["data"]
    assert status["active_project_id"] == selected_id
    assert status["project"]["id"] == selected_id


def test_account_manager_context_and_knowledge_reads_exclude_foreign_project(client):
    from app import deps
    from app.database import repos
    from app.services.tools.state_tools import (
        t_get_approvals, t_get_campaigns, t_get_measurements, t_get_project_state,
        t_get_tasks,
    )

    assert client.post("/api/onboarding/business", json={"name": "Own Business"}).status_code == 200
    with deps.get_db() as conn:
        repos.Projects.upsert(conn, {"id": "foreign", "name": "Foreign", "website": "",
            "goal": "", "status": "active", "settings_json": "{}",
            "created_at": "now", "updated_at": "now"})
        repos.Campaigns.upsert(conn, {"id": "foreign-campaign", "project_id": "foreign",
            "title": "Foreign campaign sentinel", "updated_at": "now"})
        repos.Tasks.upsert(conn, {"id": "foreign-task", "project_id": "foreign",
            "title": "Foreign task sentinel", "updated_at": "now"})
        repos.Approvals.upsert(conn, {"id": "foreign-approval", "project_id": "foreign",
            "title": "Foreign approval sentinel", "updated_at": "now"})
        repos.Experiments.upsert(conn, {"id": "foreign-experiment", "project_id": "foreign",
            "hypothesis": "Foreign experiment sentinel", "updated_at": "now"})
        repos.Measurements.insert(conn, {"id": "foreign-measurement",
            "experiment_id": "foreign-experiment", "observed_at": "now",
            "evidence_md": "Foreign measurement sentinel"})
        repos.Learnings.insert(conn, {"id": "foreign-learning", "project_id": "foreign",
            "body_md": "Foreign learning sentinel", "source": "test", "observed_at": "now"})
        own_id = repos.Projects.list(conn)[0]["id"]

        reads = [
            t_get_campaigns(conn, project_id=own_id, root=None, args={}),
            t_get_tasks(conn, project_id=own_id, root=None, args={}),
            t_get_approvals(conn, project_id=own_id, root=None, args={}),
            t_get_measurements(conn, project_id=own_id, root=None, args={}),
            t_get_project_state(conn, project_id=own_id, root=None, args={}),
        ]
        serialized = str(reads)
        assert "Foreign campaign sentinel" not in serialized
        assert "Foreign task sentinel" not in serialized
        assert "Foreign approval sentinel" not in serialized
        assert reads[3]["measurements"] == []
        assert reads[4]["state"]["measurements"] == 0

    knowledge = client.get("/api/knowledge", params={"project_id": own_id})
    assert knowledge.status_code == 200
    assert "Foreign learning sentinel" not in knowledge.text

