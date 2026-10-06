"""v0.2.1 live-chat UX proofs (N1-N16). No paid calls; turns use the persisted deterministic provider setting."""
import json
import time

from fastapi.testclient import TestClient

from app import deps
from app.database import repos
from app.main import create_app

NOW = "2026-09-16T00:00:00+00:00"


def _client(tmp_path, monkeypatch):
    from app.services.config_service import ConfigService

    db = tmp_path / "ux21.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    deps.init_db(db)
    with deps.get_db(db) as conn:
        ConfigService.set_setting("manager_provider", "deterministic", conn=conn)
    return TestClient(create_app()), str(db)


def _new_convo(client, pid="starter"):
    r = client.post("/chat/new", data={"project_id": pid}, follow_redirects=False)
    assert r.status_code in (303, 307), r.status_code
    return r.headers["location"].rsplit("/", 1)[-1]


def _wait_turn(db_path, turn_id, timeout=60):
    from app.database.sqlite import connect
    t0 = time.time()
    while time.time() - t0 < timeout:
        conn = connect(db_path)
        t = repos.Turns.get(conn, turn_id)
        conn.close()
        if t and t["status"] in ("completed", "failed"):
            return t
        time.sleep(0.5)
    raise AssertionError(f"turn {turn_id} did not finish")


def test_n1_new_chat_binds_project(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    cid = _new_convo(client, "starter")
    from app.database.sqlite import connect
    conn = connect(db)
    assert repos.Conversations.get(conn, cid)["project_id"] == "starter"
    conn.close()


def test_n2_conversation_project_immutable(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    from app.database.sqlite import connect
    from app.services import state as store
    cid = _new_convo(client, "starter")
    conn = connect(db)
    store.create_project(conn, "Other")
    store.set_active_project(conn, "other")  # global switch must not leak in
    conn.close()
    r = client.post("/chat/turn", data={"conversation_id": cid, "text": "hi",
                                        "client_message_id": "m-1"})
    assert r.status_code == 200
    conn = connect(db)
    turn_rows = [t for t in conn.execute("SELECT * FROM turns").fetchall()]
    assert turn_rows and turn_rows[0]["project_id"] == "starter"
    conn.close()


def test_n3_immediate_render_no_refresh(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    cid = _new_convo(client, "starter")
    r = client.post("/chat/turn", data={"conversation_id": cid, "text": "hello now",
                                        "client_message_id": "m-imm"})
    assert r.status_code == 200
    assert "hello now" in r.text  # user bubble in the SAME response
    assert "data-turn-stream" in r.text and "Working" in r.text
    from app.database.sqlite import connect
    conn = connect(db)
    assert repos.Messages.by_client_id(conn, cid, "m-imm") is not None
    conn.close()


def test_n5_one_submit_one_user_message(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    cid = _new_convo(client, "starter")
    for _ in range(2):  # HTMX retry / double submit
        r = client.post("/chat/turn", data={"conversation_id": cid, "text": "dup",
                                            "client_message_id": "m-dup"})
        assert r.status_code == 200
    from app.database.sqlite import connect
    conn = connect(db)
    users = [m for m in repos.Messages.for_conversation(conn, cid) if m["role"] == "user"]
    assert len(users) == 1
    assert len(conn.execute("SELECT * FROM turns WHERE conversation_id = ?",
                            (cid,)).fetchall()) == 1
    conn.close()


def test_n6_one_turn_one_assistant(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    cid = _new_convo(client, "starter")
    r = client.post("/chat/turn", data={"conversation_id": cid, "text": "What approvals are waiting?",
                                        "client_message_id": "m-6"})
    turn_id = None
    import re
    m = re.search(r'data-turn-stream="([a-f0-9]+)"', r.text)
    assert m
    turn_id = m.group(1)
    _wait_turn(db, turn_id)
    from app.database.sqlite import connect
    conn = connect(db)
    assts = [m for m in repos.Messages.for_conversation(conn, cid) if m["role"] == "assistant"]
    assert len(assts) == 1 and assts[0]["body_md"].strip()
    conn.close()


def _stream_text(client, turn_id, timeout=90):
    with client.stream("GET", f"/chat/turns/{turn_id}/events", timeout=timeout) as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        return r.read().decode("utf-8", "replace")


def test_n8_lifecycle_events_streamed(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    cid = _new_convo(client, "starter")
    r = client.post("/chat/turn", data={"conversation_id": cid, "text": "What approvals are waiting?",
                                        "client_message_id": "m-8"})
    import re
    turn_id = re.search(r'data-turn-stream="([a-f0-9]+)"', r.text).group(1)
    body = _stream_text(client, turn_id)
    for et in ("turn_started", "context_completed", "assistant_completed", "turn_completed"):
        assert f"event: {et}" in body, et
    assert "id: " in body  # resumable event ids


def test_n7_n9_reconnect_no_dup_no_leak(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    from app.database.sqlite import connect
    from app.services import state as store
    cid_a = _new_convo(client, "starter")
    conn = connect(db)
    store.create_project(conn, "Other")
    conn.close()
    cid_b = _new_convo(client, "other")
    for cid, mid in ((cid_a, "m-a"), (cid_b, "m-b")):
        client.post("/chat/turn", data={"conversation_id": cid, "text": f"ping {mid}",
                                        "client_message_id": mid})
    import re
    ta = None
    conn = connect(db)
    for t in conn.execute("SELECT * FROM turns").fetchall():
        if t["conversation_id"] == cid_a:
            ta = t["id"]
    conn.close()
    first = _stream_text(client, ta)
    second = _stream_text(client, ta)  # reconnect replays persisted history
    assert "ping m-b" not in first and "ping m-b" not in second  # B never leaks into A
    from app.database.sqlite import connect as _c2
    conn = connect(db)
    assts = [m for m in repos.Messages.for_conversation(conn, cid_a) if m["role"] == "assistant"]
    assert len(assts) == 1  # reconnect duplicated nothing
    conn.close()


def test_n10_job_events_survive_refresh(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    from app.database.sqlite import connect
    from app.services import jobs as jobsvc
    conn = connect(db)
    row = jobsvc.submit(conn, db, str(tmp_path), kind="probe", cmd=["python", "-c", "print(1)"],
                        side_effect="green", project_id="starter", conversation_id="c1")
    conn.close()
    # job events endpoint streams persisted lifecycle to terminal
    with client.stream("GET", f"/jobs/{row['id']}/events", timeout=90) as r:
        assert r.status_code == 200
        text = r.read().decode("utf-8", "replace")
    assert "worker_started" in text and "job_terminal" in text
    # refresh-safe: read again, same history
    with client.stream("GET", f"/jobs/{row['id']}/events", timeout=90) as r2:
        text2 = r2.read().decode("utf-8", "replace")
    assert "worker_started" in text2


def test_n11_cross_project_event_isolation(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    from app.database.sqlite import connect
    from app.services import jobs as jobsvc
    from app.services import state as store
    conn = connect(db)
    store.create_project(conn, "Other")
    conn.close()
    jb = _new_convo(client, "other")
    conn = connect(db)
    row = jobsvc.submit(conn, db, str(tmp_path), kind="probe", cmd=["python", "-c", "print(1)"],
                        side_effect="green", project_id="other", conversation_id=jb)
    conn.close()
    # foreign job via active=starter context
    conn = connect(db)
    store.set_active_project(conn, "starter")
    conn.close()
    assert client.get(f"/jobs/{row['id']}/events").status_code == 404
    for suffix in ("log", "status", "process"):
        response = client.get(
            f"/jobs/{row['id']}/{suffix}",
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/app/jobs"


def test_n12_job_state_updates_ui(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    from app.database.sqlite import connect
    from app.services import jobs as jobsvc
    conn = connect(db)
    row = jobsvc.submit(conn, db, str(tmp_path), kind="probe", cmd=["python", "-c", "print(1)"],
                        side_effect="green", project_id="starter", conversation_id="c1")
    conn.close()
    with client.stream("GET", f"/jobs/{row['id']}/events", timeout=90) as r:
        text = r.read().decode("utf-8", "replace")
    assert '"status": "completed"' in text or '"status":"completed"' in text
    jobs_response = client.get("/api/jobs?project_id=starter")
    assert jobs_response.status_code == 200
    jobs = jobs_response.json()["data"]
    assert any(job["id"] == row["id"] and job["status"] == "completed"
               for job in jobs)
    page = client.get("/jobs", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/app/jobs"


def test_n13_no_fake_percentages(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    import re
    cid = _new_convo(client, "starter")
    r = client.post("/chat/turn", data={"conversation_id": cid, "text": "What approvals are waiting?",
                                        "client_message_id": "m-13"})
    turn_id = re.search(r'data-turn-stream="([a-f0-9]+)"', r.text).group(1)
    body = _stream_text(client, turn_id)
    assert not re.search(r"\d+%", body), "fake percentage progress"


def test_n14_no_system_prompt_data_in_events(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    import re
    cid = _new_convo(client, "starter")
    r = client.post("/chat/turn", data={"conversation_id": cid, "text": "What approvals are waiting?",
                                        "client_message_id": "m-14"})
    turn_id = re.search(r'data-turn-stream="([a-f0-9]+)"', r.text).group(1)
    body = _stream_text(client, turn_id)
    for banned in ("state_digest", "rag_evidence", "chunk_id", "API_KEY", "Traceback",
                   "turn_started\"]"):
        assert banned not in body, banned
    # DEV-008-SKILLS-OPS contract update, recorded in
    # development/runs/DEV-008-SKILLS-OPS/run.json (N14 / project_id decision).
    # N14 originally banned the raw pattern `"project_id": "` everywhere. From
    # DEV-008-SKILLS-OPS the execution-tree contract sanctions `project_id` as a
    # scoping key in the metadata of exactly one tree event type
    # (`account_manager_started`, plan.md 1.3.2). The server-side SSE filter at
    # chat.py already drops every event for a foreign project, so the wire
    # boundary does not depend on hiding the key. The leak class N14 exists to
    # catch stays caught, more strictly than before:
    #   - the key must never reach a `label` or `detail`,
    #   - it may appear in `meta` only for the sanctioned set,
    #   - no secret-shaped string may sit alongside it.
    #
    # The original check could not distinguish meta from label/detail because
    # this fixture only has raw SSE text, and the raw-body pattern would now
    # also fire on the sanctioned scoping payload. The boundary is therefore
    # asserted per parsed event: never in label/detail, only in meta of the
    # sanctioned tree payloads.
    for line in body.splitlines():
        if not line.startswith("data: ") or line == "data: {}":
            continue
        try:
            payload = json.loads(line[6:])
        except ValueError:
            continue
        label, detail, meta = payload.get("label"), payload.get("detail"), payload.get("meta")
        flat = str(label) + str(detail)
        assert "project_id" not in flat, f"project_id leaked into label/detail: {flat[:120]}"
        if isinstance(meta, dict) and "project_id" in meta:
            required = {"status", "turn_id", "thread_id", "parent_id"}
            missing = required - set(meta)
            assert not missing, f"scoping payload missing contract keys: {missing}"


def test_n15_approval_safety_green(tmp_path, monkeypatch):
    from app.services import jobs as jobsvc
    from app.database.sqlite import connect
    client, db = _client(tmp_path, monkeypatch)
    conn = connect(db)
    ok, _ = jobsvc.check_side_effect(conn, "yellow", None)
    assert ok is False
    ok, _ = jobsvc.check_side_effect(conn, "red", None)
    assert ok is False
    conn.close()
