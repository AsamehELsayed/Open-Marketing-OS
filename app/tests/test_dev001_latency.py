"""DEV-001 tests: per-turn latency display contract. All offline (tmp DB, no network/provider).

Packet W2 builds against the W1 backend contract (field names/semantics in
development/runs/DEV-001/plan.md "Interface contract"):
  meta = {"provider": str, "latency_ms": int >= 0, "latency_display": "took ..."}

W1 DEPENDENCY (landed): `_format_latency` and the `elapsed_ms` parameter
of `_finish_turn` live in app/services/turns.py, which W2 must not touch.
Tests (a) and (b) import/call that W1 shape directly. The helper is NOT
duplicated here. Test (c) passes standalone (chat.py `_safe_meta` already
passes unknown meta keys through).
"""
import json

from app.database import repos
from app.database.seed import ensure_seed
from app.database.sqlite import connect


def _db(tmp_path, name="lat.db"):
    conn = connect(tmp_path / name)
    ensure_seed(conn)
    return conn


# ---- (a) format vectors ----
def test_dev001_format_latency_vectors():
    from app.services.turns import _format_latency  # W1 dependency, not duplicated here
    assert _format_latency(400) == "took 0.4s"
    assert _format_latency(38241) == "took 38s"
    assert _format_latency(72000) == "took 1m 12s"


# ---- (b) _finish_turn emits contract meta ----
def test_dev001_finish_turn_emits_contract_meta(tmp_path):
    from app.services import turns as turnsvc  # W1 dependency for elapsed_ms kwarg
    db_file = str(tmp_path / "lat.db")
    conn = _db(tmp_path)
    from app.services import state as store
    convo = store.get_or_create_conversation(conn, project_id="starter")
    made = turnsvc.create_turn(conn, db_file, conversation_id=convo["id"],
                               project_id="starter", text="hello",
                               client_message_id="lat-1")
    turn = made["turn"]
    res = {"reply_md": "Hi there.", "provenance": [],
           "job_ids": [], "approval_ids": []}
    turnsvc._finish_turn(db_file, str(tmp_path), turn, res,
                         "opencode", res["reply_md"], elapsed_ms=38241)
    events = repos.ExecutionEvents.for_turn(conn, turn["id"])
    conn.close()
    by_type = {}
    for e in events:
        by_type.setdefault(e["event_type"], []).append(e)
    for et in ("assistant_completed", "turn_completed"):
        assert et in by_type, f"missing terminal event {et}"
        meta = json.loads(by_type[et][0]["metadata_json"])
        assert meta["provider"] == "opencode"
        assert isinstance(meta["latency_ms"], int) and meta["latency_ms"] >= 0
        assert meta["latency_ms"] == 38241
        assert meta["latency_display"] == "took 38s"


# ---- (c) SSE serialisation round-trip through _safe_meta ----
def test_dev001_safe_meta_passthrough():
    from app.routes.chat import _safe_meta
    e = {"metadata_json": json.dumps({"provider": "opencode", "latency_ms": 38241,
                                      "latency_display": "took 38s",
                                      "prompt": "strip me", "system": "strip me too"})}
    meta = _safe_meta(e)
    assert meta["provider"] == "opencode"
    assert meta["latency_ms"] == 38241
    assert meta["latency_display"] == "took 38s"
    assert "prompt" not in meta and "system" not in meta
