"""v0.2 tests: provider abstraction, tools, loop, policy, delegation,
isolation, approvals, migration, memory. All offline (fake provider)."""
import json

import pytest

from app.database import repos
from app.database.seed import ensure_seed
from app.database.sqlite import connect, get_user_version


def _db(tmp_path, name="t.db"):
    conn = connect(tmp_path / name)
    ensure_seed(conn)
    return conn


def _seed_rag(conn):
    did = "doc-1"
    conn.execute("INSERT OR REPLACE INTO documents (id, project_id, path, file_sha, status_tag, indexed_at)"
                 " VALUES (?,?,?,?,?,?)",
                 (did, "starter", "knowledge/offer.md", "sha1", "VERIFIED", "2026-01-01"))
    conn.execute("INSERT OR REPLACE INTO chunks (id, project_id, document_id, chunk_id, header, text, token_est)"
                 " VALUES (?,?,?,?,?,?,?)",
                 ("c-1", "starter", did, "h1", "Offer", "CANARY-OFFER-9371 free homepage concept", 10))
    conn.execute("INSERT INTO chunks_fts (text, header, path) VALUES (?,?,?)",
                 ("CANARY-OFFER-9371 free homepage concept", "Offer", "knowledge/offer.md"))
    conn.commit()


# ---- 1. provider ----
def test_v02_provider_abstraction(tmp_path):
    from app.services.llm import FakeProvider, config_from_env
    from app.services.llm.base import ToolCall
    p = FakeProvider()
    p.queue(text="hello")
    resp = p.complete(system="s", messages=[], tools=[])
    assert resp.text == "hello" and resp.tool_calls == []
    assert p.supports_server_websearch() is False
    cfg = config_from_env()
    assert cfg.max_tool_iters >= 1 and cfg.model

    import os
    os.environ.pop("OPENAI_API_KEY", None)
    from app.services.llm.openai_provider import OpenAIProvider
    with pytest.raises(RuntimeError):
        OpenAIProvider().complete(system="s", messages=[], tools=[])


# ---- 2. tools ----
def test_v02_tools_typed_and_unknown(tmp_path):
    from app.services.tools import build_default_registry
    conn = _db(tmp_path)
    reg = build_default_registry()
    assert {"get_project_state", "rag_search", "web_search",
            "delegate_to_marketing_pm", "get_job_status",
            "propose_task", "request_approval"} <= set(reg.names())
    out = reg.execute(conn, project_id="starter", root=str(tmp_path), name="nope", args={})
    assert out["ok"] is False and "unknown tool" in out["error"]
    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="get_project_state", args="bad")
    assert out["ok"] is False
    out = reg.execute(conn, project_id="starter", root=str(tmp_path), name="get_project_state", args={})
    assert out["ok"] is True and out["state"]["project_id"] == "starter"
    # tool failure does not raise
    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="rag_search", args={"query": ""})
    assert out["ok"] is False
    conn.close()


def test_v02_multi_tool_turn(tmp_path):
    from app.services import manager_loop
    from app.services.llm import FakeProvider
    from app.services.llm.base import LLMResponse, ToolCall
    from app.services.tools import build_default_registry
    conn = _db(tmp_path)
    _seed_rag(conn)
    convo = conn.execute("SELECT * FROM conversations LIMIT 1").fetchone()
    cid = convo["id"] if convo else "c"
    if convo is None:
        from app.services import state as store
        c = store.get_or_create_conversation(conn, project_id="starter")
        cid = c["id"]
    p = FakeProvider(script=[
        LLMResponse(text=None, tool_calls=[ToolCall(name="get_project_state", arguments={})], usage={}),
        LLMResponse(text=None, tool_calls=[ToolCall(name="rag_search",
                                                     arguments={"query": "offer"})], usage={}),
        LLMResponse(text="Synthesized plan for more customers.", tool_calls=[], usage={}),
    ])
    reg = build_default_registry()
    res = manager_loop.run_manager_turn(conn, project_id="starter", conversation_id=cid,
                                        user_text="I need more customers",
                                        provider=p, registry=reg, root=str(tmp_path))
    assert res["mode"] == "AGENTIC"
    assert set(res["tools_used"]) >= {"get_project_state", "rag_search"}
    assert res["retrieval_used"] is True
    assert "Synthesized" in res["reply_md"]
    conn.close()


def test_v02_tool_failure_continues(tmp_path):
    from app.services import manager_loop
    from app.services.llm import FakeProvider
    from app.services.llm.base import LLMResponse, ToolCall
    from app.services.tools import build_default_registry
    conn = _db(tmp_path)
    from app.services import state as store
    c = store.get_or_create_conversation(conn, project_id="starter")
    p = FakeProvider(script=[
        LLMResponse(text=None, tool_calls=[ToolCall(name="bogus_tool", arguments={})], usage={}),
        LLMResponse(text="Recovered answer.", tool_calls=[], usage={}),
    ])
    reg = build_default_registry()
    res = manager_loop.run_manager_turn(conn, project_id="starter", conversation_id=c["id"],
                                        user_text="hi", provider=p, registry=reg, root=str(tmp_path))
    assert res["reply_md"] == "Recovered answer."
    conn.close()


# ---- 3. no raw rag ----
def test_v02_no_raw_rag(tmp_path):
    from app.services import manager_loop
    from app.services.llm import FakeProvider
    from app.services.llm.base import LLMResponse, ToolCall
    from app.services.tools import build_default_registry
    conn = _db(tmp_path)
    _seed_rag(conn)
    from app.services import state as store
    c = store.get_or_create_conversation(conn, project_id="starter")
    p = FakeProvider(script=[
        LLMResponse(text=None, tool_calls=[ToolCall(name="rag_search",
                                                     arguments={"query": "offer"})], usage={}),
        LLMResponse(text="We offer a free homepage concept before commitment.", tool_calls=[], usage={}),
    ])
    reg = build_default_registry()
    res = manager_loop.run_manager_turn(conn, project_id="starter", conversation_id=c["id"],
                                        user_text="what is the offer?",
                                        provider=p, registry=reg, root=str(tmp_path))
    assert "CANARY-OFFER-9371" not in res["reply_md"]
    assert res["provenance"], "provenance must reference the chunk"
    conn.close()


# ---- 4. research policy ----
def test_v02_research_policy(tmp_path):
    from app.services import manager_loop
    from app.services.llm import FakeProvider
    from app.services.llm.base import LLMResponse, ToolCall
    from app.services.tools import build_default_registry
    conn = _db(tmp_path)
    from app.services import state as store
    c = store.get_or_create_conversation(conn, project_id="starter")

    def fake_transport(query, count):
        return [{"url": "https://example.com/acme", "title": "Acme",
                 "snippet": "Acme sells widgets, pricing public."}]

    # empty local -> web used
    p = FakeProvider(script=[
        LLMResponse(text=None, tool_calls=[ToolCall(name="rag_search",
                                                     arguments={"query": "acme"})], usage={}),
        LLMResponse(text=None, tool_calls=[ToolCall(name="web_search",
                                                     arguments={"query": "acme competitor"})], usage={}),
        LLMResponse(text="Acme analysis with web evidence.", tool_calls=[], usage={}),
    ])
    reg = build_default_registry(web_transport=fake_transport)
    res = manager_loop.run_manager_turn(conn, project_id="starter", conversation_id=c["id"],
                                        user_text="Analyze competitor Acme",
                                        provider=p, registry=reg, root=str(tmp_path))
    assert res["web_used"] is True

    # state question -> authoritative, no web
    p2 = FakeProvider(script=[
        LLMResponse(text=None, tool_calls=[ToolCall(name="get_approvals",
                                                     arguments={"status": "pending"})], usage={}),
        LLMResponse(text="Two approvals pending.", tool_calls=[], usage={}),
    ])
    res2 = manager_loop.run_manager_turn(conn, project_id="starter", conversation_id=c["id"],
                                         user_text="What needs my approval?",
                                         provider=p2, registry=reg, root=str(tmp_path))
    assert res2["web_used"] is False and "get_approvals" in res2["tools_used"]
    conn.close()


# ---- 5. delegation ----
def test_v02_delegation_lifecycle(tmp_path):
    from app.services import manager_loop
    from app.services.llm import FakeProvider
    from app.services.llm.base import LLMResponse, ToolCall
    from app.services.tools import build_default_registry
    conn = _db(tmp_path)
    from app.services import state as store
    c = store.get_or_create_conversation(conn, project_id="starter")
    p = FakeProvider(script=[
        LLMResponse(text=None, tool_calls=[ToolCall(name="delegate_to_marketing_pm",
                                                     arguments={"goal": "audit site",
                                                                "brief_md": "quick audit"})], usage={}),
        LLMResponse(text="Started audit job.", tool_calls=[], usage={}),
    ])
    reg = build_default_registry()
    res = manager_loop.run_manager_turn(conn, project_id="starter", conversation_id=c["id"],
                                        user_text="audit my site and draft a campaign",
                                        provider=p, registry=reg, root=str(tmp_path))
    assert res["delegated"] is True and len(res["job_ids"]) == 1
    jid = res["job_ids"][0]
    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="get_job_status", args={"job_id": jid})
    assert out["ok"] is True and out["job_id"] == jid
    conn.close()


# ---- 6. isolation ----
def test_v02_project_isolation(tmp_path):
    from app.services import state as store
    from app.services.tools import build_default_registry
    conn = _db(tmp_path)
    store.create_project(conn, "other-project")
    reg = build_default_registry()
    # seed canary rows per project
    conn.execute("INSERT OR REPLACE INTO campaigns (id, project_id, title, updated_at)"
                 " VALUES (?,?,?,?)", ("camp-a", "starter", "CANARY-ACME-111", "2026-01-01"))
    conn.execute("INSERT OR REPLACE INTO campaigns (id, project_id, title, updated_at)"
                 " VALUES (?,?,?,?)", ("camp-b", "other-project", "CANARY-ECO-222", "2026-01-01"))
    conn.commit()
    a = reg.execute(conn, project_id="starter", root=str(tmp_path), name="get_campaigns", args={})
    b = reg.execute(conn, project_id="other-project", root=str(tmp_path), name="get_campaigns", args={})
    assert all("CANARY-ECO-222" not in c["title"] for c in a["campaigns"])
    assert all("CANARY-ACME-111" not in c["title"] for c in b["campaigns"])
    # cross-project get raises
    with pytest.raises(ValueError):
        repos.Campaigns.get(conn, "camp-b", "starter")
    # RAG isolation: doc scoped to other-project invisible from starter
    did = "doc-eco"
    conn.execute("INSERT OR REPLACE INTO documents (id, project_id, path, file_sha, status_tag, indexed_at)"
                 " VALUES (?,?,?,?,?,?)", (did, "other-project", "knowledge/eco.md", "s", "VERIFIED", "2026-01-01"))
    conn.execute("INSERT OR REPLACE INTO chunks (id, project_id, document_id, chunk_id, header, text, token_est)"
                 " VALUES (?,?,?,?,?,?,?)",
                 ("ce-1", "other-project", did, "h", "Eco", "CANARY-ECO-RAG-999 secret recipe", 5))
    conn.execute("INSERT INTO chunks_fts (text, header, path) VALUES (?,?,?)",
                 ("CANARY-ECO-RAG-999 secret recipe", "Eco", "knowledge/eco.md"))
    conn.commit()
    r = reg.execute(conn, project_id="starter", root=str(tmp_path),
                    name="rag_search", args={"query": "secret recipe"})
    assert all("CANARY-ECO-RAG-999" not in (h.get("snippet", "") + h.get("path", "")) for h in r["hits"])
    # conversations isolation
    ca = store.get_or_create_conversation(conn, project_id="starter")
    cb = store.get_or_create_conversation(conn, project_id="other-project")
    assert ca["id"] != cb["id"]
    conn.close()


# ---- 7. approvals ----
def test_v02_approval_safety(tmp_path):
    from app.services.tools import build_default_registry
    from app.services import state as store
    from app.services import jobs as jobsvc
    conn = _db(tmp_path)
    reg = build_default_registry()
    # yellow without approval blocked at executor
    allowed, reason = jobsvc.check_side_effect(conn, "yellow", None)
    assert allowed is False
    row = jobsvc.submit(conn, tmp_path, tmp_path, kind="x", cmd=["echo", "hi"],
                        side_effect="yellow", project_id="starter")
    assert row["status"] == "waiting_approval"
    # request_approval creates pending only
    out = reg.execute(conn, project_id="starter", root=str(tmp_path), name="request_approval",
                      args={"title": "Spend $500 on ads", "kind": "ad_spend"})
    assert out["ok"] is True
    appr = repos.Approvals.get(conn, out["approval_id"])
    assert appr["status"] == "pending"
    # cross-project approval cannot unlock other project
    store.create_project(conn, "Farm")
    with pytest.raises(ValueError):
        repos.Approvals.get(conn, out["approval_id"], "farm")
    conn.close()


# ---- 8. migration ----
def test_v02_migration_backfill(tmp_path):
    conn = _db(tmp_path)
    from app.database.sqlite import SCHEMA_VERSION
    assert get_user_version(conn) == SCHEMA_VERSION
    cols = {r[1] for r in conn.execute("PRAGMA table_info(campaigns)").fetchall()}
    assert "project_id" in cols
    assert repos.Projects.get(conn, "starter") is not None
    jcols = {r[1] for r in conn.execute("PRAGMA table_info(background_jobs)").fetchall()}
    assert {"job_type", "brief_md", "created_at", "updated_at"} <= jcols
    conn.close()


# ---- 9. memory ----
def test_v02_memory_policy(tmp_path):
    from app.services.tools import build_default_registry
    conn = _db(tmp_path)
    reg = build_default_registry()
    assert repos.Memories.list(conn, "starter") == []
    out = reg.execute(conn, project_id="starter", root=str(tmp_path), name="remember",
                      args={"kind": "decision", "body_md": "Founder decided agencies are the primary ICP."})
    assert out["ok"] is True
    bad = reg.execute(conn, project_id="starter", root=str(tmp_path), name="remember",
                      args={"kind": "chit-chat", "body_md": "hello there friend, this is casual talk"})
    assert bad["ok"] is False
    short = reg.execute(conn, project_id="starter", root=str(tmp_path), name="remember",
                        args={"kind": "decision", "body_md": "hi"})
    assert short["ok"] is False
    assert len(repos.Memories.list(conn, "starter")) == 1
    assert repos.Memories.list(conn, "other-project") == []
    conn.close()
