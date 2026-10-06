"""DEV-008-SKILLS-OPS W4 — routing E2E over the real HTTP graph surface.

Proves both public surfaces (turns._run_graph_turn and
graph_runtime.execute_thread) stream the same tree, that routing still visits
load_project (provider independence — a skill never bypasses the pipeline),
and that approval + replay behave end to end. Uses the proven isolated
TestClient pattern from test_w6_graph_runtime.py.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app import deps
from app.database import repos
from app.database.sqlite import connect
from app.main import create_app
from app.services import state as store
from app.services.config_service import ConfigService


def _client(tmp_path, monkeypatch):
    from app.routes import graph_runtime

    db = tmp_path / "w4e2e.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setenv("AI_RUNTIME", "langgraph")
    monkeypatch.setattr(graph_runtime, "_GRAPH", None, raising=False)
    deps.init_db(db)
    with deps.get_db(db) as conn:
        ConfigService.set_setting("manager_provider", "deterministic", conn=conn)
        proj = store.create_project(conn, "W4 E2E Project")
        convo = store.create_conversation(conn, project_id=proj["id"],
                                          title="w4 e2e")
    return TestClient(create_app()), str(db), proj["id"], convo["id"]


def _create(client, pid, cid, text, key):
    return client.post(
        "/api/graph/threads",
        json={"project_id": pid, "conversation_id": cid,
              "text": text, "client_message_id": key})


def _turn_rows(db, turn_id):
    conn = connect(db)
    try:
        return repos.ExecutionEvents.for_turn(conn, turn_id)
    finally:
        conn.close()


def _orphans(rows):
    ids = {r["id"] for r in rows}
    bad = []
    for row in rows:
        try:
            meta = json.loads(row.get("metadata_json") or "{}")
        except ValueError:
            meta = {}
        parent = meta.get("parent_id") or ""
        if not parent:
            continue
        try:
            parent_int = int(parent)
        except (TypeError, ValueError):
            bad.append((row["id"], row["event_type"], parent))
            continue
        if parent_int not in ids:
            bad.append((row["id"], row["event_type"], parent))
    return bad


def test_full_turn_streams_tree_and_still_visits_load_project(tmp_path,
                                                              monkeypatch):
    client, db, pid, cid = _client(tmp_path, monkeypatch)
    text = ("how do I improve my SEO audit readiness and fix our technical SEO "
            "with a proper site architecture review")
    r = _create(client, pid, cid, text, "w4-e2e-1")
    assert r.status_code == 201, r.text
    thread_id = r.json()["data"]["thread_id"]
    ex = client.post(f"/api/graph/threads/{thread_id}/execute",
                     headers={"Idempotency-Key": "exec-w4-e2e-1"})
    assert ex.status_code == 200, ex.text
    rows = _turn_rows(db, thread_id)
    by_type: dict[str, list] = {}
    for row in rows:
        by_type.setdefault(row["event_type"], []).append(row)
    print(f"\nmeasured E2E: {len(rows)} events; "
          f"task_planned={len(by_type.get('task_planned', []))}; "
          f"skill_selected={len(by_type.get('skill_selected', []))}; "
          f"started={len(by_type.get('employee_started', []))}; "
          f"completed={len(by_type.get('employee_completed', []))}; "
          f"state_read={len(by_type.get('state_read', []))}")
    # provider independence: skills rode along, the pipeline still ran
    assert len(by_type.get("task_planned", [])) >= 1
    assert len(by_type.get("skill_selected", [])) >= 1, (
        "this intent must route to >=1 skill over HTTP")
    queued = {json.loads(x.get("metadata_json") or "{}").get("employee_id")
              for x in by_type.get("employee_queued", [])}
    started = {json.loads(x.get("metadata_json") or "{}").get("employee_id")
               for x in by_type.get("employee_started", [])}
    completed = {json.loads(x.get("metadata_json") or "{}").get("employee_id")
                 for x in by_type.get("employee_completed", [])}
    assert len(queued & started & completed) >= 1
    assert by_type.get("state_read"), (
        "a turn with selected skills must still visit load_project")
    assert _orphans(rows) == [], f"orphans: {_orphans(rows)}"


def test_thread_events_replay_returns_identical_sequence(tmp_path, monkeypatch):
    client, db, pid, cid = _client(tmp_path, monkeypatch)
    r = _create(client, pid, cid, "how do I improve my SEO audit readiness",
                "w4-replay-1")
    thread_id = r.json()["data"]["thread_id"]
    client.post(f"/api/graph/threads/{thread_id}/execute",
                headers={"Idempotency-Key": "exec-w4-replay-1"})
    sse = client.get(f"/api/graph/threads/{thread_id}/events")
    assert sse.status_code == 200
    assert "text/event-stream" in sse.headers["content-type"]
    live_ids = [int(line.split("id: ", 1)[1])
                for line in sse.text.splitlines() if line.startswith("id: ")]
    assert live_ids, "expected SSE ids for the executed thread"
    assert live_ids == sorted(live_ids), "SSE ids must arrive in order"
    tail = client.get(f"/api/graph/threads/{thread_id}/events",
                      params={"after": live_ids[-1]})
    assert tail.status_code == 200
    tail_ids = [int(line.split("id: ", 1)[1])
                for line in tail.text.splitlines() if line.startswith("id: ")]
    assert tail_ids == [], "after=<last> must return only newer rows"
    head = client.get(f"/api/graph/threads/{thread_id}/events",
                      headers={"Last-Event-ID": str(live_ids[0])})
    assert head.status_code == 200
    head_ids = [int(line.split("id: ", 1)[1])
                for line in head.text.splitlines() if line.startswith("id: ")]
    assert head_ids == live_ids[1:], "Last-Event-ID must return the tail"
    from app.contracts.events import LEGACY_EVENT_TYPES
    # turn_summary rides the same table but is deliberately out of band
    # (plan §1.3.1: emitted straight into insert, dropped by the wire filter),
    # so the honest comparison is against the wire-eligible rows.
    db_ids = [x["id"] for x in _turn_rows(db, thread_id)
              if x["event_type"] in LEGACY_EVENT_TYPES]
    assert live_ids == db_ids, "live SSE sequence must equal the DB sequence"
    print(f"\nmeasured E2E replay: {len(live_ids)} ids identical live/DB; "
          f"tail-after-last empty; Last-Event-ID tail={len(head_ids)}")


def test_approval_e2e_interrupt_resume_idempotent(tmp_path, monkeypatch):
    client, db, pid, cid = _client(tmp_path, monkeypatch)
    r = _create(client, pid, cid,
                "please approve and send the launch campaign", "w4-appr-1")
    thread_id = r.json()["data"]["thread_id"]
    ex = client.post(f"/api/graph/threads/{thread_id}/execute",
                     headers={"Idempotency-Key": "exec-w4-appr-1"})
    assert ex.status_code == 200, ex.text
    assert ex.json()["data"]["status"] == "interrupted", (
        "a yellow action must pause at approval_gate over HTTP")
    rows = _turn_rows(db, thread_id)
    approvals = [x for x in rows if x["event_type"] == "approval_required"]
    assert approvals, "approval_required must be persisted"
    meta = json.loads(approvals[0].get("metadata_json") or "{}")
    assert meta.get("status") == "WAITING_FOR_APPROVAL"
    synth_before = len([x for x in rows
                        if x["event_type"] == "synthesis_completed"])
    payload = {"approved": True, "decided_by": "founder",
               "idempotency_key": "w4-resume-1"}
    a1 = client.post(f"/api/graph/threads/{thread_id}/resume", json=payload)
    assert a1.status_code == 200, a1.text
    assert a1.json()["data"]["executed"] is True
    a2 = client.post(f"/api/graph/threads/{thread_id}/resume", json=payload)
    assert a2.status_code == 200
    assert a2.json()["data"].get("replayed") is True, (
        "a repeat resume with the same key must replay, not re-execute")
    rows2 = _turn_rows(db, thread_id)
    synth_after = len([x for x in rows2
                       if x["event_type"] == "synthesis_completed"])
    assert synth_after == synth_before, "resume must not repeat the effect"
    print(f"\napproval E2E: interrupted -> approved -> replayed; "
          f"synthesis_completed stable at {synth_after}")


def test_unknown_project_selects_nothing_and_fails_closed(tmp_path,
                                                          monkeypatch):
    client, db, pid, cid = _client(tmp_path, monkeypatch)
    r = client.post(
        "/api/graph/threads",
        json={"project_id": "ghost", "conversation_id": cid,
              "text": "how do I improve my SEO", "client_message_id": "w4-no-1"})
    assert r.status_code in (400, 404), r.text
    assert "NO_PROJECT_SCOPE" in r.text
