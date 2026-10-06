"""Router / product-cleanup proofs after OpenCode removal (DEV-007 W1).

Covers: routing candidates, deterministic fallback, explicit selection,
provider abstraction intact, OpenAI optional escalation intact.
"""
import json
import os

from app.database.seed import ensure_seed
from app.database.sqlite import connect


def _db(tmp_path, name="t.db"):
    conn = connect(tmp_path / name)
    ensure_seed(conn)
    return conn


def _convo(conn, project_id="starter"):
    from app.services import state as store
    return store.get_or_create_conversation(conn, project_id=project_id)


def _contract(reply, **kw):
    base = {"reply": reply, "sources": [], "unknowns": [], "actions": [],
            "jobs_started": [], "approvals_required": []}
    base.update(kw)
    return base


# 1. No OPENAI_API_KEY -> deterministic fallback via router.
def test_proof1_agentic_falls_back_without_openai(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from app.services.llm.router import resolve_candidates
    assert resolve_candidates("auto", ("openai", "deterministic"),
                              openai_ok=False) == ["deterministic"]
    from app.services import account_manager
    conn = _db(tmp_path, "p1.db")
    turn = account_manager.handle_turn(conn, str(tmp_path), "What approvals are waiting?")
    assert turn["reply_md"]
    conn.close()


# 2. No OpenAI key -> deterministic fallback still answers.
def test_proof2_no_key_falls_back(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from app.services.llm.router import resolve_candidates
    assert resolve_candidates("auto", ("openai", "deterministic"),
                              openai_ok=False) == ["deterministic"]
    from app.services import account_manager
    conn = _db(tmp_path, "p2.db")
    turn = account_manager.handle_turn(conn, str(tmp_path), "What approvals are waiting?")
    assert turn["reply_md"]
    conn.close()


# 3. Explicit provider selection works (no opencode candidate).
def test_proof3_explicit_selection(tmp_path):
    from app.services.llm.router import normalize_selection, resolve_candidates
    pri = ("openai", "deterministic")
    assert resolve_candidates("openai", pri, openai_ok=False) == ["openai"]
    assert resolve_candidates("deterministic", pri, openai_ok=True) == ["deterministic"]
    assert normalize_selection("opencode") == "auto"  # unknown -> auto, never opencode
    assert resolve_candidates("auto", pri, openai_ok=True)[0] == "openai"
    assert resolve_candidates("auto", pri, openai_ok=False) == ["deterministic"]


# 4. Malformed agent contract fails cleanly via manager_loop.
def test_proof4_malformed_contract_fails_cleanly(tmp_path):
    from app.services import manager_loop
    from app.services.llm import FakeProvider
    from app.services.llm.base import LLMResponse
    from app.services.tools import build_default_registry
    conn = _db(tmp_path, "p4.db")
    c = _convo(conn)
    bad_json = "not json at all"
    p = FakeProvider(script=[LLMResponse(text="", tool_calls=[],
                                         usage={"agent_contract": {"reply": "  "}})])
    res = manager_loop.run_manager_turn(conn, project_id="starter", conversation_id=c["id"],
                                        user_text="hi", provider=p,
                                        registry=build_default_registry(), root=str(tmp_path))
    # empty/missing contract fields degrade to text path without crash
    assert res["reply_md"]
    # parse_agent_contract raises on malformed input
    try:
        manager_loop.parse_agent_contract(bad_json)
        assert False, "must raise"
    except RuntimeError:
        pass
    conn.close()


# 5. manager_loop still fails closed without project scope.
def test_proof5_missing_scope_closed(tmp_path):
    from app.services import manager_loop
    from app.services.llm import FakeProvider
    from app.services.tools import build_default_registry
    conn = _db(tmp_path, "p5.db")
    p = FakeProvider()
    res = manager_loop.run_manager_turn(conn, project_id="", conversation_id="c",
                                        user_text="hi", provider=p,
                                        registry=build_default_registry(), root=str(tmp_path))
    assert res["mode"] == "NO_PROJECT_SCOPE" and p.calls == []
    conn.close()


# 6. Multi-step contract merge via agent_contract usage key.
def test_proof6_multistep_contract(tmp_path):
    from app.services import manager_loop
    from app.services.llm import FakeProvider
    from app.services.llm.base import LLMResponse
    from app.services.tools import build_default_registry
    conn = _db(tmp_path, "p6.db")
    c = _convo(conn)
    contract = _contract(
        "Competitor analysis done; recommend agency outreach next, pending your approval for outreach.",
        sources=[{"url": "https://example.com/rival", "title": "Rival"}],
        actions=["Start agency outreach"],
        jobs_started=[{"job_id": "job-1", "goal": "teardown"}],
        approvals_required=[{"kind": "outreach", "title": "Approve outreach"}],
        evidence_used=["state", "rag", "web", "delegation"])
    p = FakeProvider(script=[LLMResponse(text=contract["reply"], tool_calls=[],
                                         usage={"agent_contract": contract})])
    res = manager_loop.run_manager_turn(conn, project_id="starter", conversation_id=c["id"],
                                        user_text="Analyze competitor Rival and tell me what to do",
                                        provider=p, registry=build_default_registry(),
                                        root=str(tmp_path))
    assert res["mode"] == "AGENTIC" and res["delegated"] is True and res["web_used"] is True
    assert res["job_ids"] == ["job-1"]
    assert "approval" in res["reply_md"].lower()
    assert any(p["path"] == "https://example.com/rival" for p in res["provenance"])
    conn.close()


# 7. Raw RAG chunks are never normal final answers.
def test_proof7_no_raw_rag(tmp_path):
    from app.services import manager_loop
    from app.services.llm import FakeProvider
    from app.services.llm.base import LLMResponse
    from app.services.tools import build_default_registry
    conn = _db(tmp_path, "p7.db")
    did = "doc-1"
    conn.execute("INSERT OR REPLACE INTO documents (id, project_id, path, file_sha, status_tag, indexed_at)"
                 " VALUES (?,?,?,?,?,?)", (did, "starter", "knowledge/offer.md", "s", "VERIFIED", "2026-01-01"))
    conn.execute("INSERT OR REPLACE INTO chunks (id, project_id, document_id, chunk_id, header, text, token_est)"
                 " VALUES (?,?,?,?,?,?,?)",
                 ("c-1", "starter", did, "h1", "Offer", "CANARY-CHUNK-4488 full raw chunk text here", 10))
    conn.execute("INSERT INTO chunks_fts (text, header, path) VALUES (?,?,?)",
                 ("CANARY-CHUNK-4488 full raw chunk text here", "Offer", "knowledge/offer.md"))
    conn.commit()
    c = _convo(conn)
    bundle = manager_loop.build_project_bundle(conn, "starter", [{"role": "user", "content": "what is the offer?"}])
    assert any("CANARY-CHUNK-4488" in e.get("snippet", "") for e in bundle["rag_evidence"])
    p = FakeProvider(script=[LLMResponse(text="We offer a free homepage concept before commitment.",
                                         tool_calls=[], usage={})])
    res = manager_loop.run_manager_turn(conn, project_id="starter", conversation_id=c["id"],
                                        user_text="what is the offer?",
                                        provider=p, registry=build_default_registry(),
                                        root=str(tmp_path))
    assert "CANARY-CHUNK-4488" not in res["reply_md"]
    conn.close()


# 8. Worker/delegation result is synthesized, not dumped.
def test_proof8_delegation_synthesized(tmp_path):
    from app.services import manager_loop
    from app.services.llm import FakeProvider
    from app.services.llm.base import LLMResponse
    from app.services.tools import build_default_registry
    conn = _db(tmp_path, "p8.db")
    c = _convo(conn)
    contract = _contract(
        "Deep research finished: best bet is agency white-label outreach.",
        jobs_started=[{"job_id": "deep-1", "goal": "market scan",
                       "raw_blob": "RAW-WORKER-9911 unedited payload"}])
    p = FakeProvider(script=[LLMResponse(text=contract["reply"], tool_calls=[],
                                         usage={"agent_contract": contract})])
    res = manager_loop.run_manager_turn(conn, project_id="starter", conversation_id=c["id"],
                                        user_text="research the best marketing opportunity deeply",
                                        provider=p, registry=build_default_registry(),
                                        root=str(tmp_path))
    assert "RAW-WORKER-9911" not in res["reply_md"] and "white-label" in res["reply_md"]
    conn.close()


# 9. No cross-project context in the project bundle.
def test_proof9_bundle_isolation(tmp_path):
    from app.services import state as store
    from app.services import manager_loop
    conn = _db(tmp_path, "p9.db")
    store.create_project(conn, "other-project")
    conn.execute("INSERT OR REPLACE INTO campaigns (id, project_id, title, updated_at)"
                 " VALUES (?,?,?,?)", ("cb", "other-project", "CANARY-ECO-6060", "2026-01-01"))
    conn.execute("INSERT OR REPLACE INTO memories (id, project_id, kind, body_md, confidence,"
                 " source_ref, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                 ("m1", "other-project", "decision", "CANARY-ECO-MEM-7070 secret", "HIGH", "",
                  "2026-01-01", "2026-01-01"))
    conn.commit()
    bundle = manager_loop.build_project_bundle(conn, "starter", [{"role": "user", "content": "status?"}])
    blob = json.dumps(bundle)
    assert "CANARY-ECO-6060" not in blob and "CANARY-ECO-MEM-7070" not in blob
    conn.close()


# 10. Existing OpenAIProvider still works unchanged.
def test_proof10_openai_intact(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from app.services.llm.openai_provider import OpenAIProvider, build_provider
    try:
        OpenAIProvider().complete(system="s", messages=[], tools=[])
        assert False
    except RuntimeError as e:
        assert "OPENAI_API_KEY" in str(e) or "Cloud AI is not configured" in str(e)
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    assert build_provider().name == "fake"
