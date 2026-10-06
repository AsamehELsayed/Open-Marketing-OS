"""DEV-005 I2 — W6/W7 contract reconciliation (orchestrator-owned).

Proves the two JSON shapes W7's runtimeClient targets exist behind the
AI_RUNTIME=langgraph gate and behave honestly:
- GET /api/turns/{id}/model-calls (W5 ModelCalls.for_turn, project-scoped)
- POST /api/approvals/{id}/resume (legacy decide + W1 flow passthrough)
"""
import pytest
from fastapi.testclient import TestClient

from app import deps
from app.database import repos
from app.main import create_app
from app.services import state as store
from app.services.config_service import ConfigService
from app.services import turns as turnsvc


@pytest.fixture()
def i2_client(tmp_path, monkeypatch):
    db = tmp_path / "i2.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setenv("AI_RUNTIME", "langgraph")
    deps.init_db(db)
    with deps.get_db(db) as conn:
        ConfigService.set_setting("manager_provider", "deterministic", conn=conn)
        proj = store.create_project(conn, "I2 Project")
        convo = store.create_conversation(conn, project_id=proj["id"],
                                          title="i2 chat")
        made = turnsvc.create_turn(conn, str(db), conversation_id=convo["id"],
                                   project_id=proj["id"], text="i2 hello",
                                   client_message_id="i2-1")
        repos.ModelCalls.insert(conn, {
            "call_id": "call-i2-1", "turn_id": made["turn"]["id"],
            "project_id": proj["id"], "provider": "local", "model": "m",
            "route_mode": "LOCAL", "route_reason": "test",
            "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
            "started_at": "x", "ended_at": "y"})
        repos.Approvals.upsert(conn, {"id": "ap-i2", "project_id": proj["id"],
                                      "kind": "K", "title": "T", "body_md": "",
                                      "status": "pending"})
    app = create_app()
    return TestClient(app), made["turn"]["id"]


def test_model_calls_alias_returns_rows(i2_client):
    client, tid = i2_client
    r = client.get(f"/api/turns/{tid}/model-calls")
    assert r.status_code == 200, r.text
    rows = r.json()["data"]
    assert isinstance(rows, list) and len(rows) == 1
    assert rows[0]["call_id"] == "call-i2-1"
    assert rows[0]["input_tokens"] == 10


def test_model_calls_unknown_turn_404(i2_client):
    client, _tid = i2_client
    r = client.get("/api/turns/nope/model-calls")
    assert r.status_code == 404


def test_approval_resume_decides_and_replays(i2_client):
    client, _tid = i2_client
    payload = {"decision": "approved", "decided_by": "i2",
               "idempotency_key": "approval-ap-i2"}
    r = client.post("/api/approvals/ap-i2/resume", json=payload)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == "approved"
    assert data["executed"] is True
    r2 = client.post("/api/approvals/ap-i2/resume", json=payload)
    assert r2.status_code == 200
    assert r2.json()["data"].get("replayed") is True


def test_approval_resume_rejects_bad_decision(i2_client):
    client, _tid = i2_client
    r = client.post("/api/approvals/ap-i2/resume",
                    json={"decision": "maybe", "decided_by": "i2",
                          "idempotency_key": "k"})
    assert r.status_code == 422


def test_approval_resume_unknown_404(i2_client):
    client, _tid = i2_client
    r = client.post("/api/approvals/nope/resume",
                    json={"decision": "approved", "decided_by": "i2",
                          "idempotency_key": "k"})
    assert r.status_code == 404
