from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.database import repos
from app.main import create_app
from app.services import state as store

NOW = "2026-09-16T00:00:00+00:00"


def _seed_two(conn):
    store.create_project(conn, "other-project")
    store.set_active_project(conn, "other-project")
    rows = {
        "campaigns": [
            {"id": "ca", "project_id": "starter", "title": "CANARY-CAMP-A", "status": "executing", "updated_at": NOW},
            {"id": "cb", "project_id": "other-project", "title": "CANARY-CAMP-B", "status": "open", "updated_at": NOW},
        ],
        "tasks": [
            {"id": "ta", "project_id": "starter", "campaign_id": None, "title": "CANARY-TASK-A", "lane": "", "status": "open", "acceptance": "", "job_id": None, "due_at": None, "updated_at": NOW},
            {"id": "tb", "project_id": "other-project", "campaign_id": None, "title": "CANARY-TASK-B", "lane": "", "status": "open", "acceptance": "", "job_id": None, "due_at": None, "updated_at": NOW},
        ],
        "approvals": [
            {"id": "aa", "project_id": "starter", "kind": "k", "title": "CANARY-APPR-A", "body_md": "", "status": "pending", "fields_json": "{}", "decided_by": None, "decided_at": None, "updated_at": NOW},
            {"id": "ab", "project_id": "other-project", "kind": "k", "title": "CANARY-APPR-B", "body_md": "", "status": "pending", "fields_json": "{}", "decided_by": None, "decided_at": None, "updated_at": NOW},
        ],
    }
    for row in rows["campaigns"]:
        repos.Campaigns.upsert(conn, row)
    for row in rows["tasks"]:
        repos.Tasks.upsert(conn, row)
    for row in rows["approvals"]:
        repos.Approvals.upsert(conn, row)
    repos.Experiments.upsert(conn, {"id": "ea", "project_id": "starter", "campaign_id": None, "hypothesis": "CANARY-EXP-A", "metric": "m", "window_days": 1, "status": "measuring", "updated_at": NOW})
    repos.Experiments.upsert(conn, {"id": "eb", "project_id": "other-project", "campaign_id": None, "hypothesis": "CANARY-EXP-B", "metric": "m", "window_days": 1, "status": "measuring", "updated_at": NOW})
    repos.Measurements.insert(conn, {"id": "ma", "experiment_id": "ea", "observed_at": NOW, "evidence_md": "CANARY-MEAS-A", "decision": "none"})
    repos.Measurements.insert(conn, {"id": "mb", "experiment_id": "eb", "observed_at": NOW, "evidence_md": "CANARY-MEAS-B", "decision": "none"})
    repos.Learnings.insert(conn, {"id": "la", "project_id": "starter", "experiment_id": "ea", "body_md": "CANARY-LEARN-A", "source": "s", "observed_at": NOW})
    repos.Learnings.insert(conn, {"id": "lb", "project_id": "other-project", "experiment_id": "eb", "body_md": "CANARY-LEARN-B", "source": "s", "observed_at": NOW})
    for job_id, project_id, kind in (("ja", "starter", "CANARY-JOB-A"), ("jb", "other-project", "CANARY-JOB-B")):
        repos.BackgroundJobs.upsert(conn, {"id": job_id, "project_id": project_id, "conversation_id": "", "kind": kind, "status": "failed", "cmd_json": "[]", "cwd": "", "exit_code": 1, "log_path": "", "started_at": NOW, "finished_at": NOW})
    conn.execute("INSERT OR REPLACE INTO documents (id, project_id, path, file_sha, status_tag, indexed_at) VALUES (?,?,?,?,?,?)", ("da", "starter", "knowledge/CANARY-DOC-A.md", "s", "VERIFIED", NOW))
    conn.execute("INSERT OR REPLACE INTO documents (id, project_id, path, file_sha, status_tag, indexed_at) VALUES (?,?,?,?,?,?)", ("db", "other-project", "knowledge/CANARY-DOC-B.md", "s", "VERIFIED", NOW))
    conn.commit()


def _client(tmp_path, monkeypatch):
    db = tmp_path / "ui.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    deps.init_db(db)
    with deps.get_db(db) as conn:
        _seed_two(conn)
    return TestClient(create_app(), raise_server_exceptions=False)


def test_pages_redirect_and_api_project_scoping(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    for path, target in (("/campaigns", "/app/campaigns"), ("/tasks", "/app/jobs"), ("/approvals", "/app/approvals"), ("/jobs", "/app/jobs"), ("/results", "/app/results"), ("/brain", "/app/knowledge")):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == target
        assert "<html" not in response.text.lower()
    campaigns = client.get("/api/campaigns?project_id=other-project").json()["data"]
    assert campaigns[0]["title"] == "CANARY-CAMP-B"
    assert "CANARY-CAMP-A" not in str(campaigns)
    approvals = client.get("/api/approvals?project_id=other-project").json()["data"]
    assert approvals[0]["title"] == "CANARY-APPR-B"
    assert "CANARY-APPR-A" not in str(approvals)
    jobs = client.get("/api/jobs?project_id=other-project").json()["data"]
    assert jobs[0]["kind"] == "CANARY-JOB-B"
    assert "CANARY-JOB-A" not in str(jobs)
    with deps.get_db() as conn:
        store.set_active_project(conn, "starter")
    campaigns = client.get("/api/campaigns?project_id=starter").json()["data"]
    assert campaigns[0]["title"] == "CANARY-CAMP-A"
    assert "CANARY-CAMP-B" not in str(campaigns)
    approvals = client.get("/api/approvals?project_id=starter").json()["data"]
    assert approvals[0]["title"] == "CANARY-APPR-A"
    assert "CANARY-APPR-B" not in str(approvals)


def test_cross_project_writes_rejected_by_existing_state_services(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.post("/approvals/ab/decide", data={"decision": "approved"}).status_code == 404
    assert client.post("/campaigns/cb/status", data={"status": "approved"}).status_code == 404
    assert client.post("/tasks/tb/status", data={"status": "done"}).status_code == 404
    with deps.get_db() as conn:
        store.set_active_project(conn, "starter")
    with pytest.raises(ValueError):
        with deps.get_db() as conn:
            store.decide_approval(conn, "ab", "approved", project_id="starter")
    with pytest.raises(ValueError):
        with deps.get_db() as conn:
            store.set_campaign_status(conn, "cb", "approved", project_id="starter")
    with pytest.raises(ValueError):
        with deps.get_db() as conn:
            store.set_task_status(conn, "tb", "done", project_id="starter")


def test_missing_active_project_still_uses_frozen_legacy_redirect(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with deps.get_db() as conn:
        conn.execute("DELETE FROM settings WHERE key = 'active_project_id'")
        conn.commit()
    response = client.get("/campaigns", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/app/campaigns"
