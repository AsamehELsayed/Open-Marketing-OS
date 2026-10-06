"""W6 acceptance: deterministic flows, retrieval discipline, provenance, no side effects."""
import json
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from app import deps
from app.database import repos
from app.main import create_app
from app.services import account_manager, state as store
from app.services.rag.indexing_service import IndexingService

CORPUS = {
    "knowledge/brand.md": "# Brand\n\nAcme Test Company builds custom websites with live-link proof.\n",
    "strategy/founder-decisions.md": "# Decisions\n\nAgencies are a founder-confirmed major segment for white-label work.\n",
    "analytics/draft.md": "# Draft\n\nUnlabeled guesses about future pricing tiers.\n",
}


@pytest.fixture()
def env(tmp_path):
    root = tmp_path / "ws"
    for rel, text in CORPUS.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    db = tmp_path / "w6.db"
    deps.init_db(db)
    conn = deps.get_db(db).__enter__() if False else None
    from app.database.sqlite import connect
    conn = connect(db)
    repos.Campaigns.upsert(conn, {"id": "opp-01", "title": "Demo", "status": "approved",
                                  "updated_at": "2026-09-16T00:00:00+00:00"})
    repos.Approvals.upsert(conn, {"id": "ap-1", "kind": "T", "title": "t", "body_md": "",
                                  "status": "pending", "fields_json": "{}",
                                  "decided_by": None, "decided_at": None,
                                  "updated_at": "2026-09-16T00:00:00+00:00"})
    IndexingService(conn, root).build_or_update()
    yield conn, root
    conn.close()


def _spy():
    calls = []
    def retriever(q):
        calls.append(q)
        from app.services.rag import rag_service
        # placeholder replaced by caller conn below
        return {"hits": [], "mode": "SPY"}
    return calls, retriever


def test_direct_state_flows_skip_retrieval(env):
    conn, root = env
    for question in ("What needs my attention?", "What approvals are waiting?",
                     "What changed recently?", "What is blocked?",
                     "What should I do next?", "Company context",
                     "Experiment status"):
        def boom(q):
            raise AssertionError(f"retrieval must not run for {question!r}")
        turn = account_manager.handle_turn(conn, root, question, retriever=boom)
        assert turn["retrieval_used"] is False
        assert turn["reply_md"]


def test_documentary_retrieves_grounded_context(env):
    conn, root = env
    seen = []
    def retriever(q):
        seen.append(q)
        from app.services.rag import rag_service
        return rag_service.retrieve(conn, q)
    turn = account_manager.handle_turn(conn, root, "Why did we choose agencies?", retriever=retriever)
    assert turn["retrieval_used"] is True and seen
    assert turn["flow"] == "explain"  # why-questions synthesize explanations, not raw chunks
    assert turn["evidence"]
    assert turn["provenance"][0]["path"] == "strategy/founder-decisions.md"


def test_empty_retrieval_stays_empty(env):
    conn, root = env
    turn = account_manager.handle_turn(
        conn, root, "Explain quantum badgers in detail",
        retriever=lambda q: {"hits": [], "mode": "FTS_ONLY"})
    assert turn["evidence"] == [] and turn["retrieval_used"] is True
    assert "don't have enough" in turn["reply_md"]


def test_unknown_never_asserted(env):
    conn, root = env
    from app.services.rag import rag_service
    turn = account_manager.handle_turn(
        conn, root, "pricing tiers", retriever=lambda q: rag_service.retrieve(conn, q))
    assert turn["unknowns"] and all(u["status_tag"] == "UNKNOWN" for u in turn["unknowns"])
    assert "not stated as fact" in turn["reply_md"]


def test_stale_marked(env):
    conn, root = env
    (root / "knowledge" / "brand.md").write_text("# Brand\n\nEdited after index.\n", encoding="utf-8")
    from app.services.rag import rag_service
    turn = account_manager.handle_turn(
        conn, root, "live-link proof websites", retriever=lambda q: rag_service.retrieve(conn, q))
    assert any(s["path"] == "knowledge/brand.md" for s in turn["stale"])


def test_conflicts_surface_and_actions_safe(env):
    conn, root = env
    repos.Events.log(conn, {"id": "evt-w6", "kind": "import_conflict", "ref_id": "opp-01",
                            "detail_json": json.dumps({"adapter": "backlog", "source": "x"}),
                            "created_at": store.now_iso()})
    before = {t: [dict(r) for r in conn.execute(f"SELECT * FROM {t}").fetchall()]
              for t in ("campaigns", "approvals", "tasks", "experiments", "companies")}
    turn = account_manager.handle_turn(
        conn, root, "What needs my attention?", retriever=lambda q: {"hits": [], "mode": "X"})
    assert len(turn["conflicts"]) == 1
    assert all(a["kind"] == "link" for a in turn["suggested_actions"])
    after = {t: [dict(r) for r in conn.execute(f"SELECT * FROM {t}").fetchall()]
             for t in ("campaigns", "approvals", "tasks", "experiments", "companies")}
    assert before == after  # turns never mutate state


def test_deleted_docs_behave(env):
    conn, root = env
    (root / "analytics" / "draft.md").unlink()
    IndexingService(conn, root).build_or_update()
    from app.services.rag import rag_service
    assert rag_service.retrieve(conn, "pricing tiers")["hits"] == []


def test_chat_route_persists_transcript(tmp_path, monkeypatch):
    import re
    import time

    db = tmp_path / "chat.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    deps.init_db(db)
    from app.database.sqlite import connect as _connect
    with TestClient(create_app()) as client:
        dead = client.post("/chat", data={"text": "What approvals are waiting?"})
        assert dead.status_code == 404
        legacy = client.get("/chat", follow_redirects=False)
        assert legacy.status_code == 303
        assert legacy.headers["location"] == "/app/chat/new"
        created = client.post(
            "/chat/new", data={"project_id": "starter"}, follow_redirects=False)
        assert created.status_code == 303
        conversation_id = created.headers["location"].rsplit("/", 1)[-1]
        submitted = client.post("/chat/turn", data={
            "conversation_id": conversation_id,
            "text": "What approvals are waiting?",
            "client_message_id": "w6-transcript-1",
        })
        assert submitted.status_code == 200
        match = re.search(r'data-turn-stream="([a-f0-9]+)"', submitted.text)
        assert match
        turn_id = match.group(1)
        deadline = time.time() + 60
        while time.time() < deadline:
            poll = _connect(db)
            turn = repos.Turns.get(poll, turn_id)
            poll.close()
            if turn and turn["status"] in {"completed", "failed"}:
                break
            time.sleep(0.25)
        assert turn and turn["status"] == "completed"
        messages = client.get(
            f"/api/chats/{conversation_id}/messages?project_id=starter")
        assert messages.status_code == 200
        assert len(messages.json()["data"]) == 2
    conn = _connect(db)
    convos = repos.Conversations.list(conn)
    assert len(convos) == 1
    msgs = repos.Messages.for_conversation(conn, convos[0]["id"])
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert all(json.loads(m["citations_json"]) is not None for m in msgs)
    conn.close()
