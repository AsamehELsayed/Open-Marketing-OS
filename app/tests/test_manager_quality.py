"""Account Manager response-quality acceptance (synthesis, Arabic, sources, dedupe)."""
import re
import time
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from app import deps
from app.database import repos
from app.database.sqlite import connect
from app.main import create_app
from app.services import account_manager
from app.services.rag.indexing_service import IndexingService
from app.services.marketing_guardrails import (
    enforce_numeric_guardrail,
    verify_numeric_guardrail_compliance,
)

AR_CORPUS = {
    "knowledge/offers.md": (
        "# العرض\n\nنقدم ديمو مجاني قبل أي التزام. بعد السداد الكامل السورس "
        "باسم العميل. نشتغل white-label للوكالات تحت NDA والوكالة تبيع باسمها.\n"),
    "knowledge/proof-library.md": (
        "# الدليل\n\nعندنا 7 مشاريع حية معروضة. مفيش testimonials أو نتائج "
        "موثقة مسجلة لسه.\n"),
    "strategy/founder-decisions.md": (
        "# القرارات\n\nالوكالات قطاع رئيسي مؤكد. التركيز الأردن ثم مصر.\n"),
}

FORBIDDEN = ("From knowledge/", "[VERIFIED]", "chunk", "RAG", ".md", "UNKNOWN []")


def test_numeric_experiment_targets_are_labeled_as_hypotheses():
    unsafe = "Target: conversion +12%"
    guarded = enforce_numeric_guardrail(unsafe, has_evidence=False)
    assert "Proposed experiment threshold" in guarded
    assert "working hypothesis" in guarded
    compliant, explanation = verify_numeric_guardrail_compliance(guarded)
    assert compliant, explanation


@pytest.fixture()
def ar_env(tmp_path):
    root = tmp_path / "ws"
    for rel, text in AR_CORPUS.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    db = tmp_path / "q.db"
    conn = connect(db)
    IndexingService(conn, root).build_or_update()
    yield conn, root
    conn.close()


def test_1_arabic_company_assessment(ar_env):
    conn, root = ar_env
    turn = account_manager.handle_turn(conn, root, "ايه رأيك في شركة نجم")
    assert turn["flow"] == "assessment"
    assert turn["retrieval_used"] is True
    for token in FORBIDDEN:
        assert token not in turn["reply_md"], token
    assert "نقاط القوة" in turn["reply_md"] or "الخلاصة" in turn["reply_md"]
    assert turn["provenance"]  # kept internally + persisted, not pasted


def test_2_arabic_why_agencies(ar_env):
    conn, root = ar_env
    turn = account_manager.handle_turn(conn, root, "ليه اخترنا الوكالات؟")
    assert turn["flow"] == "explain"
    for token in FORBIDDEN:
        assert token not in turn["reply_md"], token
    assert "الوكال" in turn["reply_md"]


def test_3_sources_on_demand(ar_env):
    conn, root = ar_env
    prior = [{"path": "knowledge/offers.md", "chunk_id": "c000",
              "file_sha": "x", "status_tag": "VERIFIED"}]
    turn = account_manager.handle_turn(conn, root, "وريني المصدر", prior_provenance=prior)
    assert turn["flow"] == "sources"
    assert "knowledge/offers.md" in turn["reply_md"]
    assert turn["retrieval_used"] is False
    empty = account_manager.handle_turn(conn, root, "وريني المصدر", prior_provenance=[])
    assert "البيانات المباشرة" in empty["reply_md"]


def test_4_empty_stays_honest(ar_env):
    conn, root = ar_env
    turn = account_manager.handle_turn(
        conn, root, "كلام عن البطاريق في القطب",
        retriever=lambda q: {"hits": [], "mode": "FTS_ONLY"})
    assert "مش كفاية" in turn["reply_md"]
    for token in FORBIDDEN:
        assert token not in turn["reply_md"], token


def test_5_one_submit_one_pair(tmp_path, monkeypatch):
    db = tmp_path / "chat.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    deps.init_db(db)
    with TestClient(create_app()) as client:
        dead = client.post("/chat", data={"text": "legacy"})
        assert dead.status_code == 404
        created = client.post(
            "/chat/new",
            data={"project_id": "starter"},
            follow_redirects=False,
        )
        assert created.status_code == 303
        conversation_id = created.headers["location"].rsplit("/", 1)[-1]
        submitted = client.post(
            "/chat/turn",
            data={
                "conversation_id": conversation_id,
                "text": "What approvals are waiting?",
                "client_message_id": "manager-quality-1",
            },
        )
        assert submitted.status_code == 200
        match = re.search(r'data-turn-stream="([a-f0-9]+)"', submitted.text)
        assert match
        turn_id = match.group(1)
    deadline = time.time() + 60
    while time.time() < deadline:
        conn = connect(db)
        turn = repos.Turns.get(conn, turn_id)
        conn.close()
        if turn and turn["status"] in {"completed", "failed"}:
            break
        time.sleep(0.25)
    assert turn and turn["status"] == "completed"
    conn = connect(db)
    convos = repos.Conversations.list(conn)
    assert len(convos) == 1
    msgs = repos.Messages.for_conversation(conn, convos[0]["id"])
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
    conn.close()


def test_submit_guard_in_react_new_chat():
    source_root = Path(__file__).resolve().parents[2] / "frontend" / "src"
    new_chat = (source_root / "components" / "shell" / "NewChat.tsx").read_text(
        encoding="utf-8")
    chat = (source_root / "routes" / "Chat.tsx").read_text(encoding="utf-8")
    composer = (source_root / "components" / "chat" / "Composer.tsx").read_text(
        encoding="utf-8")

    # NewChat prevents duplicate creation; the selected project is later
    # verified against the conversation's immutable project binding in Chat.
    assert "if (!activeId || creating) return" in new_chat
    assert "disabled={!activeId || creating || loading}" in new_chat

    # Resolve conversation identity from the real chats API, then fail closed
    # if the route, active project, and persisted conversation binding disagree.
    assert "api.getChats({ status: \"all\" })" in chat
    assert "chat.id === convoId" in chat
    assert "projectId: conversation.project_id" in chat
    assert re.search(
        r"conversationBinding\?\.conversationId !== convoId\s*\|\|\s*"
        r"conversationBinding\.projectId !== projectId\s*\|\|\s*"
        r"activeScopeRef\.current !== submittedScope",
        chat,
    )

    # Scope is captured at submit time and checked again synchronously; the
    # ref closes the same-render double-submit window before network dispatch.
    assert "const submittedScope = scopeKey;" in chat
    assert "sending || sendLockRef.current" in chat
    assert "sendLockRef.current = true;" in chat
    assert "sendLockRef.current = false;" in chat
    assert re.search(r"sendChatTurn\(convoId,\s*clean,\s*newClientId\(\)\)", chat)

    # Composer availability and submit behavior both require explicit binding.
    assert "scopeKey={scopeKey}" in chat
    assert "canSubmit={Boolean(" in chat
    assert "conversationBinding.projectId === projectId" in chat
    assert "if (!text || sending || !canSubmit) return;" in composer
    assert "disabled={!canSend || !canSubmit}" in composer


def test_english_still_natural(ar_env):
    conn, root = ar_env
    turn = account_manager.handle_turn(conn, root, "What do you think about Acme Test Company company?")
    assert turn["flow"] == "assessment"
    assert "Strengths" in turn["reply_md"] or "inference" in turn["reply_md"]
    for token in ("From knowledge/", "[VERIFIED]", "chunk"):
        assert token not in turn["reply_md"], token
