"""DEV-010 W3: project scoped approval context and honest SPA states."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import deps
from app.database import repos
from app.main import create_app


def _client(tmp_path, monkeypatch):
    db = tmp_path / "dev010-spa.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setattr(deps, "ROOT", tmp_path)
    deps.init_db(db)
    return TestClient(create_app())


def _project(conn, project_id, company_id):
    repos.Projects.upsert(conn, {"id": project_id, "name": project_id,
        "website": "", "goal": "", "status": "active",
        "settings_json": "{}", "created_at": "now", "updated_at": "now"})
    repos.Companies.upsert(conn, {"id": company_id, "name": company_id,
        "website": "", "markets_json": "{}", "languages_json": "[]",
        "services_json": "[]", "created_at": "now"})


def test_approvals_return_only_verified_same_project_context(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with deps.get_db() as conn:
        _project(conn, "p1", "co1")
        _project(conn, "p2", "co2")
        for cid, pid, company in (("conv1", "p1", "co1"), ("conv2", "p2", "co2")):
            repos.Conversations.upsert(conn, {"id": cid, "project_id": pid,
                "company_id": company, "title": cid, "status": "open",
                "archived_at": "", "created_at": "now", "updated_at": "now"})
        repos.Turns.upsert(conn, {"id": "turn1", "conversation_id": "conv1",
            "project_id": "p1", "client_message_id": "", "user_message_id": "",
            "status": "running", "provider": "", "error": "", "created_at": "now",
            "updated_at": "now"})
        for row in (
            {"id": "camp1", "project_id": "p1", "title": "Real campaign", "updated_at": "now"},
            {"id": "camp2", "project_id": "p2", "title": "Foreign campaign", "updated_at": "now"},
        ):
            repos.Campaigns.upsert(conn, row)
        repos.Tasks.upsert(conn, {"id": "task1", "project_id": "p1", "campaign_id": "camp1",
            "title": "Real task", "updated_at": "now"})
        repos.Tasks.upsert(conn, {"id": "task2", "project_id": "p2", "campaign_id": "camp2",
            "title": "Foreign task", "updated_at": "now"})
        workflow = {"conversation_id": "conv1", "turn_id": "turn1", "thread_id": "thread1",
            "task_id": "task1", "campaign_id": "camp1", "assigned_role": "growth",
            "requested_by": "Account Manager"}
        repos.Approvals.upsert(conn, {"id": "a1", "project_id": "p1", "kind": "campaign",
            "title": "Launch campaign", "body_md": "Review this", "status": "pending",
            "fields_json": json.dumps({"workflow": workflow}), "updated_at": "now"})
        foreign_workflow = {**workflow, "conversation_id": "conv2", "turn_id": "turn1",
            "task_id": "task2", "campaign_id": "camp2", "requested_by": "Foreign"}
        repos.Approvals.upsert(conn, {"id": "a2", "project_id": "p1", "kind": "campaign",
            "title": "Stale links", "body_md": "", "status": "pending",
            "fields_json": json.dumps({"workflow": foreign_workflow}), "updated_at": "now"})

    rows = client.get("/api/approvals?project_id=p1").json()["data"]
    assert [r["id"] for r in rows] == ["a1", "a2"]
    valid = next(r for r in rows if r["id"] == "a1")
    assert valid["project_id"] == "p1"
    assert valid["requested_by"] == "Account Manager"
    assert valid["task"] == {"id": "task1", "title": "Real task"}
    assert valid["campaign"] == {"id": "camp1", "title": "Real campaign"}
    stale = next(r for r in rows if r["id"] == "a2")
    assert "requested_by" not in stale and "task" not in stale and "campaign" not in stale
    assert client.get("/api/approvals?project_id=p2").json()["data"] == []


def test_results_summary_zero_and_populated_states_are_evidence_based(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    empty = client.get("/api/results/summary?project_id=starter").json()["data"]
    assert empty["counts"] == {"experiments": 0, "experiments_by_status": {}, "measurements": 0, "learnings": 0}
    assert empty["decisions"] == []
    with deps.get_db() as conn:
        repos.Learnings.insert(conn, {"id": "learn1", "project_id": "starter",
            "body_md": "Observed conversion lift", "source": "measurement",
            "observed_at": "now"})
    populated = client.get("/api/results/summary?project_id=starter").json()["data"]
    assert populated["counts"]["learnings"] == 1
    assert populated["learnings"][0]["body_md"] == "Observed conversion lift"
    assert populated["decisions"] == []


def test_campaign_and_results_empty_states_link_to_account_manager():
    root = Path(__file__).resolve().parents[2] / "frontend" / "src" / "routes"
    campaigns = (root / "Campaigns.tsx").read_text(encoding="utf-8")
    results = (root / "Results.tsx").read_text(encoding="utf-8")
    card = (root.parent / "components" / "activity" / "ApprovalCard.tsx").read_text(encoding="utf-8")
    assert "Account Manager creates campaigns" in campaigns
    assert 'to="/app"' in campaigns and "Open Account Manager" in campaigns
    assert "counts.experiments === 0 && counts.measurements === 0 && counts.learnings === 0" in results
    assert 'to="/app"' in results and "Open Account Manager" in results
    assert all(label in card for label in ("Requested by:", "Task:", "Campaign:", "Project:", "Action:", "Status:"))
