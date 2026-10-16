"""True streaming: merge logic, secret hygiene, job lifecycle (no OpenCode NDJSON)."""
from pathlib import Path


def test_cumulative_or_append_merge():
    # Mirrors streaming merge logic: cumulative snapshots replace,
    # genuinely new chunks append, repeats are ignored.
    def merge(acc, text):
        if text.startswith(acc) and len(text) > len(acc):
            return text
        if text not in acc:
            return acc + text
        return acc

    acc = merge("", "hello")
    acc = merge(acc, "hello world")
    assert acc == "hello world"
    acc = merge(acc, "hello world")
    assert acc == "hello world"
    acc = merge(acc, "!")
    assert acc == "hello world!"


def test_streaming_emits_no_prompts_or_secrets(tmp_path, monkeypatch):
    import json
    from app.services import turns as turnsvc
    from app.database.sqlite import connect
    from app.database.seed import ensure_seed
    conn = connect(tmp_path / "s.db")
    ensure_seed(conn)
    seen = []
    orig = turnsvc.emit_event

    def spy(db_path, **kw):
        seen.append(kw)
        return orig(db_path, **kw)

    monkeypatch.setattr(turnsvc, "emit_event", spy)
    turnsvc._on_loop_event(str(tmp_path / "s.db"), "starter", "c1", "t1", "assistant_delta",
                           {"text": "partial answer"})
    blob = json.dumps(seen, ensure_ascii=False)
    assert "partial answer" in blob
    for banned in ("system prompt", "API_KEY", "sk-", "bundle", "Traceback"):
        assert banned not in blob
    conn.close()


def test_job_lifecycle_events_persist_and_isolate(tmp_path):
    import json
    import time
    from app.database import repos
    from app.database.sqlite import connect
    from app.database.seed import ensure_seed
    from app.services import jobs as jobsvc
    from app.services import state as store
    conn = connect(tmp_path / "j.db")
    ensure_seed(conn)
    store.create_project(conn, "Beta")
    row = jobsvc.submit(conn, str(tmp_path / "j.db"), str(tmp_path), kind="probe",
                        cmd=["python", "-c", "print(1)"], side_effect="green",
                        project_id="starter", conversation_id="c1")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        c2 = connect(tmp_path / "j.db")
        j = repos.BackgroundJobs.get(c2, row["id"])
        c2.close()
        if j and j["status"] in ("completed", "failed"):
            break
        time.sleep(0.02)
    c3 = connect(tmp_path / "j.db")
    final_job = repos.BackgroundJobs.get(c3, row["id"])
    evs = repos.ExecutionEvents.for_job(c3, row["id"], "starter")
    types = [e["event_type"] for e in evs]
    assert final_job["status"] in ("completed", "failed")
    terminal_type = ("worker_completed" if final_job["status"] == "completed"
                     else "worker_failed")
    assert "worker_started" in types and "job_status_changed" in types
    assert terminal_type in types
    terminal_status_events = [
        e for e in evs if e["event_type"] == "job_status_changed"
        and json.loads(e["metadata_json"]).get("status") == final_job["status"]
    ]
    assert terminal_status_events
    assert types.index(terminal_type) < types.index("job_status_changed", types.index(terminal_type))
    assert repos.ExecutionEvents.for_job(c3, row["id"], "beta") == []
    c3.close()
    conn.close()


def test_instagram_tool_is_read_only(tmp_path):
    from app.database.sqlite import connect
    from app.database.seed import ensure_seed
    from app.services.tools import build_default_registry
    conn = connect(tmp_path / "ro.db")
    ensure_seed(conn)
    reg = build_default_registry()
    spec = [s for s in reg.specs() if s["name"] == "instagram_audit"][0]
    assert spec["side_effect"] == "green"
    before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    out = t_instagram_audit_call(conn, tmp_path)
    assert out["ok"] is True
    after = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert after == before  # audit writes nothing
    conn.close()


def t_instagram_audit_call(conn, root):
    from app.services.tools.instagram_tools import t_instagram_audit
    return t_instagram_audit(conn, project_id="starter", root=root, args={"username": "nobody_here"})


def test_instagram_events_human_language(tmp_path, monkeypatch):
    from app.services import turns as turnsvc
    from app.database.sqlite import connect
    from app.database.seed import ensure_seed
    conn = connect(tmp_path / "s2.db")
    ensure_seed(conn)
    seen = []
    orig = turnsvc.emit_event

    def spy(db_path, **kw):
        seen.append(kw)
        return orig(db_path, **kw)

    monkeypatch.setattr(turnsvc, "emit_event", spy)
    turnsvc._on_loop_event(
        str(tmp_path / "s2.db"), "starter", "c1", "t1", "tool_started",
        {"tool": "instagram_audit", "args": {"username": "competitor_x"}})
    turnsvc._on_loop_event(
        str(tmp_path / "s2.db"), "starter", "c1", "t1", "tool_completed",
        {"tool": "instagram_audit",
         "obs": {"ok": True, "audit": {"evidence": {"source": "apify", "status": "partial"},
                                       "posts": [{}, {}], "unknowns": []}}})
    types = [s["event_type"] for s in seen]
    assert types == ["instagram_provider_started", "instagram_provider_completed"]
    assert all("RAG" not in s["label"] and "subprocess" not in s["label"].lower() for s in seen)
    conn.close()
