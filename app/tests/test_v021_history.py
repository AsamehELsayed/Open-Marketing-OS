"""Chat history: sidebar scoping, deterministic titles, archive, reload restore."""
from fastapi.testclient import TestClient

from app import deps
from app.database import repos
from app.main import create_app
from app.services import state as store


def _client(tmp_path, monkeypatch):
    from app.services.config_service import ConfigService

    db = tmp_path / "hist.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    deps.init_db(db)
    with deps.get_db(db) as conn:
        ConfigService.set_setting("manager_provider", "deterministic", conn=conn)
    return TestClient(create_app()), str(db)


def test_sidebar_lists_project_chats_only(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    from app.database.sqlite import connect
    conn = connect(tmp_path / "hist.db")
    store.create_project(conn, "Beta")
    ca = store.create_conversation(conn, project_id="starter")
    cb = store.create_conversation(conn, project_id="beta")
    conn.close()
    response_a = client.get("/api/chats?project_id=starter")
    response_b = client.get("/api/chats?project_id=beta")
    assert response_a.status_code == 200
    assert response_b.status_code == 200
    ids_a = {row["id"] for row in response_a.json()["data"]}
    ids_b = {row["id"] for row in response_b.json()["data"]}
    assert ca["id"] in ids_a and cb["id"] not in ids_a
    assert cb["id"] in ids_b and ca["id"] not in ids_b
    legacy = client.get(f"/chat/{ca['id']}", follow_redirects=False)
    assert legacy.status_code == 303
    assert legacy.headers["location"] == f"/app/chat/{ca['id']}"


def test_deterministic_titles_no_llm(tmp_path, monkeypatch):
    assert store.generate_chat_title("راجعلي Instagram نجم بالكامل") == "Instagram Review"
    assert store.generate_chat_title("احنا نركز على ايه الأسبوع ده؟") == "Weekly Marketing Focus"
    assert store.generate_chat_title("random hello xyz") == "random hello xyz"
    client, _ = _client(tmp_path, monkeypatch)
    cid = client.post("/chat/new", data={"project_id": "starter"},
                      follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    client.post("/chat/turn", data={"conversation_id": cid, "text": "راجعلي Instagram نجم",
                                    "client_message_id": "t-1"})
    from app.database.sqlite import connect
    conn = connect(tmp_path / "hist.db")
    assert repos.Conversations.get(conn, cid)["title"] == "Instagram Review"
    conn.close()


def test_archive_hides_chat_without_delete(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    from app.database.sqlite import connect
    cid = client.post("/chat/new", data={"project_id": "starter"},
                      follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    legacy = client.post(f"/chat/{cid}/archive", follow_redirects=False)
    assert legacy.status_code == 404
    r = client.post(f"/api/chats/{cid}/archive")
    assert r.status_code == 200
    conn = connect(tmp_path / "hist.db")
    row = repos.Conversations.get(conn, cid)
    assert row["status"] == "archived" and row["archived_at"]  # archived, not deleted
    assert all(c["id"] != cid for c in store.project_conversations(conn, "starter"))
    conn.close()


def test_reload_restores_messages_sources_timeline(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    cid = client.post("/chat/new", data={"project_id": "starter"},
                      follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    client.post("/chat/turn", data={"conversation_id": cid, "text": "What approvals are waiting?",
                                    "client_message_id": "t-r"})
    import time
    from app.database.sqlite import connect
    t0 = time.time()
    while time.time() - t0 < 60:
        conn = connect(tmp_path / "hist.db")
        assts = [m for m in repos.Messages.for_conversation(conn, cid) if m["role"] == "assistant"]
        conn.close()
        if assts:
            break
        time.sleep(0.5)
    assert assts, "assistant reply must persist"
    messages = client.get(
        f"/api/chats/{cid}/messages?project_id=starter"
    )
    assert messages.status_code == 200
    rows = messages.json()["data"]
    assert any(row["role"] == "user"
               and row["body_md"] == "What approvals are waiting?"
               for row in rows)
    assert any(row["role"] == "assistant"
               and row["body_md"] == assts[0]["body_md"]
               for row in rows)
    activity = client.get(
        f"/api/activity?conversation_id={cid}&project_id=starter"
    )
    assert activity.status_code == 200
    event_types = {row["event_type"] for row in activity.json()["data"]}
    assert "turn_completed" in event_types


def test_conversation_project_immutable_via_turn(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    from app.database.sqlite import connect
    from app.services import turns as turnsvc
    conn = connect(tmp_path / "hist.db")
    store.create_project(conn, "Beta")
    cid = store.create_conversation(conn, project_id="starter")["id"]
    store.set_active_project(conn, "beta")  # global switch must not move the chat
    made = turnsvc.create_turn(conn, str(tmp_path / "hist.db"), conversation_id=cid,
                               project_id=repos.Conversations.get(conn, cid)["project_id"],
                               text="hi", client_message_id="imm-1")
    assert made["turn"]["project_id"] == "starter"
    conn.close()
