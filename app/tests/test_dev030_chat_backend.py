"""DEV-030 backend coverage for model selection and safe chat export."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.database import repos
from app.database.sqlite import SCHEMA_PATH, SCHEMA_VERSION, connect, get_user_version
from app.main import create_app
from app.services import state as store


@pytest.fixture
def chat_env(tmp_path, monkeypatch):
    db_path = tmp_path / "dev030.db"
    monkeypatch.setattr(deps, "DB_PATH", db_path)
    deps.init_db(db_path)
    with deps.get_db(db_path) as conn:
        project_a = store.create_project(conn, "Alpha")
        project_b = store.create_project(conn, "Beta")
        convo_a = store.create_conversation(conn, project_id=project_a["id"], title="Alpha chat")
        convo_b = store.create_conversation(conn, project_id=project_b["id"], title="Beta chat")
    with TestClient(create_app()) as client:
        yield client, db_path, {
            "project_a": project_a["id"], "project_b": project_b["id"],
            "convo_a": convo_a["id"], "convo_b": convo_b["id"],
        }


def _fixed_models(monkeypatch):
    from app.services import chat_models

    monkeypatch.setattr(chat_models, "available_chat_models", lambda: {
        "providers": [
            {"provider": "AUTO", "available": True, "detail": "",
             "models": [{"id": "", "name": "Automatic"}]},
            {"provider": "LOCAL", "available": True, "detail": "",
             "models": [{"id": "qwen-local", "name": "Qwen Local"}]},
            {"provider": "OPENROUTER", "available": True, "detail": "",
             "models": [{"id": "vendor/model-a", "name": "Model A"}]},
        ]
    })


def test_v12_migration_adds_preferences_and_turn_linkage(tmp_path):
    old_schema = SCHEMA_PATH.read_text(encoding="utf-8")
    old_schema = old_schema.replace("PRAGMA user_version = 12;", "PRAGMA user_version = 11;")
    old_schema = old_schema.replace("  model_provider TEXT NOT NULL DEFAULT 'AUTO',\n  model_id TEXT NOT NULL DEFAULT '',\n", "")
    old_schema = old_schema.replace("  turn_id TEXT NOT NULL DEFAULT '',\n  client_message_id", "  client_message_id")
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(old_schema)
    raw.close()

    conn = connect(path)
    assert get_user_version(conn) == SCHEMA_VERSION
    for table, expected in {
        "conversations": {"model_provider", "model_id"},
        "turns": {"model_provider", "model_id"},
        "messages": {"turn_id"},
    }.items():
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        assert expected <= columns
    conn.close()
    # Idempotent on the next startup.
    conn = connect(path)
    assert get_user_version(conn) == SCHEMA_VERSION
    conn.close()


def test_model_selection_api_persists_and_fails_closed(chat_env, monkeypatch):
    client, _db, ids = chat_env
    _fixed_models(monkeypatch)

    catalog = client.get("/api/ai/chat-models")
    assert catalog.status_code == 200
    assert {row["provider"] for row in catalog.json()["data"]["providers"]} == {
        "AUTO", "LOCAL", "OPENROUTER"}

    saved = client.put(f"/api/chats/{ids['convo_a']}/model-selection",
                       json={"model_provider": "OPENROUTER", "model_id": "vendor/model-a"})
    assert saved.status_code == 200
    assert saved.json()["data"] == {
        "model_provider": "OPENROUTER", "model_id": "vendor/model-a"}
    assert client.get(f"/api/chats/{ids['convo_a']}/model-selection").json()["data"] == saved.json()["data"]

    invalid = client.put(f"/api/chats/{ids['convo_a']}/model-selection",
                         json={"model_provider": "OPENROUTER", "model_id": "missing/model"})
    assert invalid.status_code == 422
    assert client.get(f"/api/chats/{ids['convo_a']}/model-selection").json()["data"] == saved.json()["data"]
    foreign = client.get(
        f"/api/chats/{ids['convo_a']}/model-selection?project_id={ids['project_b']}")
    assert foreign.status_code == 403


def test_chat_turn_snapshots_explicit_model_and_saved_selection(chat_env, monkeypatch):
    client, db_path, ids = chat_env
    _fixed_models(monkeypatch)
    from app.routes import chat as chat_routes

    monkeypatch.setattr(chat_routes.turnsvc, "start_turn_bg", lambda *_args: None)
    ack = client.post("/chat/turn", data={
        "conversation_id": ids["convo_a"], "text": "First turn",
        "client_message_id": "first", "model_provider": "LOCAL", "model_id": "qwen-local",
    })
    assert ack.status_code == 200
    with deps.get_db(db_path) as conn:
        turn = repos.Turns.by_client_id(conn, ids["convo_a"], "first")
        msg = repos.Messages.by_client_id(conn, ids["convo_a"], "first")
        assert (turn["model_provider"], turn["model_id"]) == ("LOCAL", "qwen-local")
        assert msg["turn_id"] == turn["id"]
        assert (repos.Conversations.get(conn, ids["convo_a"])["model_provider"],
                repos.Conversations.get(conn, ids["convo_a"])["model_id"]) == (
                    "LOCAL", "qwen-local")

    ack = client.post("/chat/turn", data={
        "conversation_id": ids["convo_a"], "text": "Second turn",
        "client_message_id": "second",
    })
    assert ack.status_code == 200
    with deps.get_db(db_path) as conn:
        turn = repos.Turns.by_client_id(conn, ids["convo_a"], "second")
        assert (turn["model_provider"], turn["model_id"]) == ("LOCAL", "qwen-local")


def test_router_uses_selected_openrouter_model_without_fallback(tmp_path, monkeypatch):
    from app.services.config_service import ConfigService
    from app.services.llm import model_router as module
    from app.services.llm.base import LLMResponse
    from app.services.llm.model_router import ModelRouter

    monkeypatch.setattr(module, "openrouter_configured_default", lambda: True)
    monkeypatch.setattr(module, "openai_configured", lambda: False)
    settings = {"manager_provider": "auto", "cloud_escalation": "off",
                "openrouter_default_model": "configured/default"}
    monkeypatch.setattr(ConfigService, "get_setting",
                        classmethod(lambda _cls, key, default=None, conn=None:
                                    settings.get(key, default)))

    class Provider:
        model = "configured/default"

        def __init__(self):
            self.opts = None
            self.calls = 0

        def complete(self, *, system, messages, tools, opts=None):
            self.calls += 1
            self.opts = dict(opts or {})
            return LLMResponse("ok", [], {"actual_model": "vendor/model-a"})

    provider = Provider()
    router = ModelRouter(openrouter_provider=provider)
    conn = connect(tmp_path / "router.db")
    try:
        _response, call = router.complete(
            conn, turn_id="turn-a", project_id="project-a", system="",
            messages=[], tools=[], mode="OPENROUTER",
            model_override="vendor/model-a", call_id="call-a")
        assert provider.opts["model"] == "vendor/model-a"
        assert provider.calls == 1
        assert call.provider == "openrouter"
        assert call.model == "vendor/model-a"

        monkeypatch.setattr(module, "openrouter_configured_default", lambda: False)
        with pytest.raises(RuntimeError, match="OpenRouter is not connected"):
            router.complete(
                conn, turn_id="turn-b", project_id="project-a", system="",
                messages=[], tools=[], mode="OPENROUTER",
                model_override="vendor/model-a", call_id="call-b")
        assert provider.calls == 1  # no fallback provider invocation
    finally:
        conn.close()


def test_export_is_full_safe_and_project_scoped(chat_env):
    client, db_path, ids = chat_env
    user_id, assistant_id = "user-a", "assistant-a"
    timestamps = ("2026-10-01T10:00:00+00:00", "2026-10-01T10:00:01+00:00")
    with deps.get_db(db_path) as conn:
        repos.Turns.upsert(conn, {
            "id": "turn-a", "conversation_id": ids["convo_a"],
            "project_id": ids["project_a"], "client_message_id": "cid-a",
            "user_message_id": user_id, "status": "completed", "provider": "openrouter",
            "model_provider": "OPENROUTER", "model_id": "vendor/model-a",
            "error": "", "created_at": timestamps[0], "updated_at": timestamps[1],
        })
        repos.Messages.insert(conn, {
            "id": user_id, "conversation_id": ids["convo_a"], "turn_id": "turn-a",
            "role": "user", "body_md": "مرحبا\n\nOPENAI_API_KEY=sk-abcdefghijklmnop",
            "citations_json": "[]", "client_message_id": "cid-a",
            "created_at": timestamps[0],
        })
        repos.Messages.insert(conn, {
            "id": assistant_id, "conversation_id": ids["convo_a"], "turn_id": "turn-a",
            "role": "assistant", "body_md": "English reply\n\n```python\nprint('ok')\n```",
            "citations_json": json.dumps([{"title": "Visible source",
                                           "url": "https://example.com/reference"},
                                          {"source": "project_rag",
                                           "file_name": "Research note.md",
                                           "scope": "project",
                                           "project_id": "project-a",
                                           "chunk_id": "chunk-a",
                                           "retrieval_sources": ["fts", "semantic"]}],
                                          ensure_ascii=False),
            "client_message_id": "", "created_at": timestamps[1],
        })
        repos.Messages.insert(conn, {
            "id": "internal-message", "conversation_id": ids["convo_a"],
            "turn_id": "turn-a", "role": "system",
            "body_md": "HIDDEN_SYSTEM_PROMPT_AND_INTERNAL_REASONING",
            "citations_json": "[]", "client_message_id": "",
            "created_at": "2026-10-01T10:00:00.500000+00:00",
        })
        repos.ModelCalls.insert(conn, {
            "call_id": "call-a", "turn_id": "turn-a", "project_id": ids["project_a"],
            "provider": "openrouter", "model": "vendor/model-a", "route_mode": "OPENROUTER",
            "route_reason": "explicit user selection", "started_at": timestamps[0],
            "ended_at": timestamps[1],
        })
        repos.ExecutionEvents.insert(conn, {
            "project_id": ids["project_a"], "conversation_id": ids["convo_a"],
            "turn_id": "turn-a", "job_id": "", "event_type": "tool_completed",
            "label": "Tool get_project finished",
            "detail": "safe status; " + "Stored result: " + ("x" * 1200),
            "metadata_json": json.dumps({"status": "COMPLETE", "tool_id": "get_project",
                                          "duration_ms": 15, "api_key": "sk-abcdefghijklmnop",
                                          "arguments": {"secret": "do-not-export"}}),
            "created_at": timestamps[1],
        })
        # This row reuses the conversation id but belongs to another project;
        # the exporter must not expose it.
        repos.ExecutionEvents.insert(conn, {
            "project_id": ids["project_b"], "conversation_id": ids["convo_a"],
            "turn_id": "foreign-turn", "job_id": "", "event_type": "tool_completed",
            "label": "FOREIGN_PROJECT_MARKER", "detail": "must stay hidden",
            "metadata_json": "{}", "created_at": timestamps[1],
        })

    response = client.get(f"/api/chats/{ids['convo_a']}/export.md")
    assert response.status_code == 200
    assert "text/markdown" in response.headers["content-type"]
    text = response.content.decode("utf-8")
    assert "مرحبا" in text and "English reply" in text
    assert text.index("### User — 2026-10-01T10:00:00+00:00") < text.index(
        "### Assistant — 2026-10-01T10:00:01+00:00")
    assert "```python" in text and "https://example.com/reference" in text
    assert "Research note.md · Business Knowledge · Project project-a · Chunk chunk-a · FTS + Semantic" in text
    assert "HIDDEN_SYSTEM_PROMPT_AND_INTERNAL_REASONING" not in text
    assert "2026-10-01T10:00:00+00:00" in text
    assert "openrouter / vendor/model-a (OPENROUTER)" in text
    assert "COMPLETE · get_project · 15 ms" in text
    assert "Stored result: " + ("x" * 1200) in text
    assert "sk-abcdefghijklmnop" not in text
    assert "OPENAI_API_KEY" not in text
    assert "do-not-export" not in text and "arguments" not in text
    assert "FOREIGN_PROJECT_MARKER" not in text

    messages = client.get(f"/api/chats/{ids['convo_a']}/messages").json()["data"]
    assert messages[1]["model"] == {
        "provider": "openrouter", "model": "vendor/model-a", "route_mode": "OPENROUTER"}


def test_export_arabic_title_uses_ascii_fallback_filename(chat_env):
    client, db_path, ids = chat_env
    with deps.get_db(db_path) as conn:
        conn.execute("UPDATE conversations SET title = ? WHERE id = ?",
                     ("محادثة عربية", ids["convo_a"]))
        conn.commit()

    response = client.get(f"/api/chats/{ids['convo_a']}/export.md")
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert disposition.isascii()
    assert 'filename="chat.md"' in disposition
    assert "filename*=UTF-8''%D9%85%D8%AD%D8%A7%D8%AF%D8%AB%D8%A9" in disposition
    assert "# محادثة عربية" in response.content.decode("utf-8")
