"""DEV-002 follow-ups: (a) reload union keeps failed+completed latencies,
(b) single panel entry (assistant_completed logs nothing live).
All offline (tmp DB, no network/provider). Follows test_dev001_latency.py
conventions: tmp_path + connect + ensure_seed, _db helper.
"""
import json
from pathlib import Path

from app.database import repos
from app.database.seed import ensure_seed
from app.database.sqlite import connect


def _db(tmp_path, name="lat2.db"):
    conn = connect(tmp_path / name)
    ensure_seed(conn)
    return conn


# ---- (a) reload union returns completed + failed terminal latencies ----
def test_dev002_reload_union_keeps_failed_and_completed_latency(tmp_path):
    from app.services import turns as turnsvc
    from app.services import state as store

    db_file = str(tmp_path / "lat2.db")
    conn = _db(tmp_path)

    convo = store.get_or_create_conversation(conn, project_id="starter")
    convo_id = convo["id"]

    # Completed turn with contract meta via the real _finish_turn path.
    made_ok = turnsvc.create_turn(conn, db_file, conversation_id=convo_id,
                                  project_id="starter", text="hello ok",
                                  client_message_id="dev002-ok")
    turn_ok = made_ok["turn"]
    res = {"reply_md": "Hi there.", "provenance": [],
           "job_ids": [], "approval_ids": []}
    turnsvc._finish_turn(db_file, str(tmp_path), turn_ok, res,
                         "opencode", res["reply_md"], elapsed_ms=38241)

    # Failed turn with latency meta, persisted with job_id="" (default).
    made_fail = turnsvc.create_turn(conn, db_file, conversation_id=convo_id,
                                    project_id="starter", text="hello fail",
                                    client_message_id="dev002-fail")
    turn_fail = made_fail["turn"]
    turnsvc.emit_event(db_file, project_id="starter", conversation_id=convo_id,
                       turn_id=turn_fail["id"], event_type="turn_failed",
                       label="Something interrupted this run.",
                       metadata={"provider": "opencode", "latency_ms": 400,
                                 "latency_display": "took 0.4s"})

    # Replicate app/routes/chat.py:93-99 past_events query path.
    events = repos.ExecutionEvents.for_conversation_jobs(conn, convo_id)
    _terminal = repos.ExecutionEvents.for_conversation_turn_terminal(conn, convo_id)
    _seen = {e["id"] for e in events}
    past_events = sorted(events + [e for e in _terminal if e["id"] not in _seen],
                         key=lambda e: e["id"])
    conn.close()

    by_type = {}
    for e in past_events:
        by_type.setdefault(e["event_type"], []).append(e)

    assert "turn_completed" in by_type, "completed terminal row missing from reload union"
    assert "turn_failed" in by_type, "failed terminal row missing from reload union"

    meta_ok = json.loads(by_type["turn_completed"][0]["metadata_json"])
    assert meta_ok["latency_ms"] == 38241
    assert meta_ok["latency_display"] == "took 38s"

    meta_fail = json.loads(by_type["turn_failed"][0]["metadata_json"])
    assert meta_fail["latency_ms"] == 400
    assert meta_fail["latency_display"] == "took 0.4s"


# ---- (b) single panel entry: assistant_completed logs nothing live ----
def test_dev002_single_panel_entry_static():
    src = (Path(__file__).resolve().parents[1] / "templates" / "chat.html").read_text(encoding="utf-8")

    ac_marker = 'ev.type === "assistant_completed"'
    tc_marker = 'ev.type === "turn_completed"'
    tf_marker = 'ev.type === "turn_failed"'

    idx_ac = src.find(ac_marker)
    assert idx_ac != -1, "assistant_completed handler marker missing"
    idx_ret = src.find("return;", idx_ac)
    assert idx_ret != -1, "assistant_completed handler return missing"
    block_ac = src[idx_ac:idx_ret]
    assert 'addActivity("assistant_completed"' not in block_ac
    assert "addActivity" not in block_ac, "assistant_completed handler must not call addActivity"

    idx_tc = src.find(tc_marker, idx_ret)
    assert idx_tc != -1, "turn_completed handler marker missing"
    idx_tf = src.find(tf_marker, idx_tc)
    assert idx_tf != -1, "turn_failed handler marker missing"
    block_tc = src[idx_tc:idx_tf]
    assert 'addActivity("turn_completed"' in block_tc, "turn_completed handler must still call addActivity"
