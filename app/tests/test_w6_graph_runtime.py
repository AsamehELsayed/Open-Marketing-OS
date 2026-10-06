"""DEV-005 W6 — FastAPI graph runtime (TDD).

Covers: project fail-closed BEFORE graph invoke, thread create/invoke
round-trip, SSE event mapping (frozen LEGACY_EVENT_TYPES only) + reconnect,
idempotent approval resume, model-visibility fields, legacy default untouched.
"""
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.contracts.events import LEGACY_EVENT_TYPES
from app.main import create_app
from app.services import state as store
from app.services.config_service import ConfigService


@pytest.fixture()
def graph_client(tmp_path, monkeypatch):
    db = tmp_path / "w6.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setenv("AI_RUNTIME", "langgraph")
    deps.init_db(db)
    with deps.get_db(db) as conn:
        ConfigService.set_setting("manager_provider", "deterministic", conn=conn)
        proj = store.create_project(conn, "W6 Project")
        convo = store.create_conversation(conn, project_id=proj["id"], title="w6 chat")
    app = create_app()
    return TestClient(app), str(db), proj["id"], convo["id"]


def _create_thread(client, pid, cid, text="hello graph", key="t-1"):
    return client.post(
        "/api/graph/threads",
        json={"project_id": pid, "conversation_id": cid,
              "text": text, "client_message_id": key},
    )


def test_project_fail_closed_before_invoke(graph_client):
    client, _db, pid, cid = graph_client
    r = client.post("/api/graph/threads", json={
        "project_id": "", "conversation_id": cid, "text": "hi",
        "client_message_id": "fail-1"})
    assert r.status_code in (400, 422)
    assert "NO_PROJECT_SCOPE" in r.text
    r2 = client.post("/api/graph/threads", json={
        "project_id": "ghost", "conversation_id": cid, "text": "hi",
        "client_message_id": "fail-2"})
    assert r2.status_code in (400, 404)
    assert "NO_PROJECT_SCOPE" in r2.text


def test_thread_create_invoke_round_trip(graph_client):
    client, _db, pid, cid = graph_client
    r = _create_thread(client, pid, cid, "hello graph", "rt-1")
    assert r.status_code == 201, r.text
    thread_id = r.json()["data"]["thread_id"]
    ex = client.post(f"/api/graph/threads/{thread_id}/execute",
                     headers={"Idempotency-Key": "exec-rt-1"})
    assert ex.status_code == 200, ex.text
    body = ex.json()["data"]
    assert body["thread_id"] == thread_id
    assert body["final_answer"]
    assert body["model_visibility"]["route_reason"]
    # state round-trip
    st = client.get(f"/api/graph/threads/{thread_id}",
                    params={"project_id": pid})
    assert st.status_code == 200
    assert st.json()["data"]["thread_id"] == thread_id


def test_sse_event_mapping_and_reconnect(graph_client):
    client, _db, pid, cid = graph_client
    r = _create_thread(client, pid, cid, "sse check", "sse-1")
    thread_id = r.json()["data"]["thread_id"]
    client.post(f"/api/graph/threads/{thread_id}/execute",
                headers={"Idempotency-Key": "exec-sse-1"})
    sse = client.get(f"/api/graph/threads/{thread_id}/events")
    assert sse.status_code == 200
    assert "text/event-stream" in sse.headers["content-type"]
    events = [ln for ln in sse.text.splitlines() if ln.startswith("event: ")]
    assert events, "expected at least one SSE event"
    for ln in events:
        etype = ln.split("event: ", 1)[1].strip()
        assert etype in list(LEGACY_EVENT_TYPES) + ["stream_end"]
    # reconnect with Last-Event-ID returns 200 and valid envelope
    r2 = client.get(f"/api/graph/threads/{thread_id}/events",
                    headers={"Last-Event-ID": "1"})
    assert r2.status_code == 200
    assert "id: " in r2.text and "data: " in r2.text


def test_idempotent_approval_resume(graph_client):
    client, _db, pid, cid = graph_client
    r = _create_thread(client, pid, cid, "please approve this campaign", "appr-1")
    thread_id = r.json()["data"]["thread_id"]
    client.post(f"/api/graph/threads/{thread_id}/execute",
                headers={"Idempotency-Key": "exec-appr-1"})
    payload = {"approved": True, "decided_by": "founder",
               "idempotency_key": "resume-key-1"}
    a1 = client.post(f"/api/graph/threads/{thread_id}/resume", json=payload)
    assert a1.status_code == 200, a1.text
    a2 = client.post(f"/api/graph/threads/{thread_id}/resume", json=payload)
    assert a2.status_code == 200
    assert a2.json()["data"]["idempotency_key"] == "resume-key-1"
    # replay must not duplicate: same executed flag, replay marker or same body
    assert a1.json()["data"]["executed"] == a2.json()["data"]["executed"]


def test_model_visibility_fields_present(graph_client):
    client, _db, pid, cid = graph_client
    r = _create_thread(client, pid, cid, "visibility please", "vis-1")
    thread_id = r.json()["data"]["thread_id"]
    ex = client.post(f"/api/graph/threads/{thread_id}/execute",
                     headers={"Idempotency-Key": "exec-vis-1"})
    assert ex.status_code == 200
    for hdr in ("x-model-provider", "x-model-name", "x-route-reason"):
        assert hdr in {k.lower() for k in ex.headers.keys()}, f"missing {hdr}"
    vis = ex.json()["data"]["model_visibility"]
    for field in ("provider", "model", "adapter", "quantization",
                  "route_reason", "route_mode"):
        assert field in vis, f"missing visibility field {field}"


def test_legacy_default_untouched(tmp_path, monkeypatch):
    from app import deps as _deps
    db = tmp_path / "legacy.db"
    monkeypatch.setattr(_deps, "DB_PATH", db)
    monkeypatch.delenv("AI_RUNTIME", raising=False)
    _deps.init_db(db)
    with _deps.get_db(db) as conn:
        ConfigService.set_setting("manager_provider", "deterministic", conn=conn)
    app = create_app()
    client = TestClient(app)
    assert client.get("/api/graph/threads/t/openapi.json").status_code in (404, 405)
    r = client.get("/api/graph/threads/nope", params={"project_id": "starter"})
    assert r.status_code == 404
    # legacy chat compat still serves
    with _deps.get_db(db) as conn:
        from app.services import state as _store
        proj = _store.create_project(conn, "Legacy")
    cid = client.post("/chat/new", data={"project_id": proj["id"]},
                      follow_redirects=False)
    assert cid.status_code in (303, 307)


def test_w6_scope_guard():
    """LangGraph is the shipped runtime and must be a declared dependency.

    DEV-008 replaced this guard's original assertion — "langgraph is absent from
    requirements.txt" — which encoded a work-packet boundary for a completed run
    rather than a product property. The app imports langgraph and shipped a
    requirements.txt that did not declare it, so a clean install could not
    start. The durable property is the opposite, and is asserted here.

    The `assert ... or True` line that used to precede this was a tautology and
    was deleted: a test that cannot fail is worse than no test, because a
    reviewer scanning for coverage sees green and concludes the area is covered.
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    req = (root / "requirements.txt").read_text(encoding="utf-8", errors="ignore")
    assert "langgraph" in req.lower(), \
        "langgraph is the shipped runtime and must be a declared dependency"
