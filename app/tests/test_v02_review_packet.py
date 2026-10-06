"""v0.2 review-packet tests: fail-closed scope, empty-local web selection,
delegation synthesis, Arabic behavior, mode contract, job isolation."""
from app.database import repos
from app.database.seed import ensure_seed
from app.database.sqlite import connect


def _db(tmp_path, name="t.db"):
    conn = connect(tmp_path / name)
    ensure_seed(conn)
    return conn


def test_v02_missing_project_id_fails_closed(tmp_path):
    from app.services.tools import build_default_registry
    conn = _db(tmp_path, "fc.db")
    reg = build_default_registry()
    for bad in (None, "", "   "):
        for tool in ("get_project_state", "get_campaigns", "rag_search",
                     "web_search", "propose_task", "request_approval", "remember"):
            args = {"query": "x", "title": "x", "kind": "decision",
                    "body_md": "long enough body text here"} if tool in (
                        "rag_search", "propose_task", "request_approval", "remember") else {}
            out = reg.execute(conn, project_id=bad, root=str(tmp_path), name=tool, args=args)
            assert out["ok"] is False and "project_id" in out["error"], (tool, bad)
    # loop entry also fails closed without calling the provider
    from app.services import manager_loop
    from app.services.llm import FakeProvider
    p = FakeProvider()
    res = manager_loop.run_manager_turn(conn, project_id="", conversation_id="c",
                                        user_text="hi", provider=p,
                                        registry=reg, root=str(tmp_path))
    assert res["mode"] == "NO_PROJECT_SCOPE" and p.calls == []
    conn.close()


def test_v02_web_selected_when_local_empty(tmp_path):
    from app.services import manager_loop
    from app.services.llm import FakeProvider
    from app.services.llm.base import LLMResponse, ToolCall
    from app.services.tools import build_default_registry
    conn = _db(tmp_path, "web.db")  # no RAG seeded: local evidence empty
    from app.services import state as store
    c = store.get_or_create_conversation(conn, project_id="starter")
    seen = {}
    def transport(query, count):
        seen["q"] = query
        return [{"url": "https://example.com/rival", "title": "RivalCo",
                 "snippet": "RivalCo offers fixed-price sites from $900."}]
    p = FakeProvider(script=[
        LLMResponse(text=None, tool_calls=[ToolCall(name="rag_search",
                                                     arguments={"query": "RivalCo"})], usage={}),
        LLMResponse(text=None, tool_calls=[ToolCall(name="web_search",
                                                     arguments={"query": "RivalCo pricing"})], usage={}),
        LLMResponse(text="RivalCo analysis: fixed-price from $900 (web evidence).", tool_calls=[], usage={}),
    ])
    reg = build_default_registry(web_transport=transport)
    res = manager_loop.run_manager_turn(conn, project_id="starter", conversation_id=c["id"],
                                        user_text="Analyze competitor RivalCo",
                                        provider=p, registry=reg, root=str(tmp_path))
    assert res["web_used"] is True and res["retrieval_used"] is True
    assert seen.get("q") == "RivalCo pricing"
    assert "RivalCo" in res["reply_md"]
    assert any(pv.get("path") == "https://example.com/rival" for pv in res["provenance"])
    conn.close()


def test_v02_delegated_result_synthesized_not_dumped(tmp_path):
    import json
    from app.services import manager_loop
    from app.services.llm import FakeProvider
    from app.services.llm.base import LLMResponse, ToolCall
    from app.services.tools import build_default_registry
    conn = _db(tmp_path, "del.db")
    from app.services import state as store
    from app.services import jobs as jobsvc
    c = store.get_or_create_conversation(conn, project_id="starter")
    row = jobsvc.submit(conn, tmp_path, tmp_path, kind="marketing_pm:research",
                        cmd=["echo", "ok"], side_effect="green", project_id="starter",
                        conversation_id=c["id"])
    rd = tmp_path / "data" / "projects" / "starter" / "job-results"
    # NOTE: delegate tool writes briefs under CWD-root; emulate result artifact here
    import pathlib
    root = tmp_path
    rdir = pathlib.Path(root) / "data" / "projects" / "starter" / "job-results"
    rdir.mkdir(parents=True, exist_ok=True)
    raw_canary = "RAW-WORKER-DUMP-5511 :: full unedited log blob stdout stderr"
    (rdir / f"{row['id']}.json").write_text(json.dumps(
        {"summary_md": "Top opportunity: agency white-label outreach.",
         "raw": raw_canary, "confidence": "MEDIUM"}), encoding="utf-8")
    p = FakeProvider(script=[
        LLMResponse(text=None, tool_calls=[ToolCall(name="get_job_status",
                                                     arguments={"job_id": row["id"]})], usage={}),
        LLMResponse(text="The research finished: top opportunity is agency white-label outreach.",
                    tool_calls=[], usage={}),
    ])
    reg = build_default_registry()
    res = manager_loop.run_manager_turn(conn, project_id="starter", conversation_id=c["id"],
                                        user_text="where are we on the research?",
                                        provider=p, registry=reg, root=str(root))
    assert "RAW-WORKER-DUMP-5511" not in res["reply_md"]
    assert "white-label" in res["reply_md"]
    conn.close()


def test_v02_arabic_agentic_response_no_leak(tmp_path):
    from app.services import manager_loop
    from app.services.llm import FakeProvider
    from app.services.llm.base import LLMResponse, ToolCall
    from app.services.tools import build_default_registry
    conn = _db(tmp_path, "ar.db")
    from app.services import state as store
    c = store.get_or_create_conversation(conn, project_id="starter")
    p = FakeProvider(script=[
        LLMResponse(text=None, tool_calls=[ToolCall(name="get_project_state",
                                                     arguments={})], usage={}),
        LLMResponse(text="الخلاصة: عندنا حملة واحدة نشطة ولا توجد موافقات معلقة.",
                    tool_calls=[], usage={}),
    ])
    reg = build_default_registry()
    res = manager_loop.run_manager_turn(conn, project_id="starter", conversation_id=c["id"],
                                        user_text="ايه رأيك في المشروع ده؟",
                                        provider=p, registry=reg, root=str(tmp_path))
    assert any("\u0600" <= ch <= "\u06FF" for ch in res["reply_md"])
    for banned in ("knowledge/", "[VERIFIED]", "chunk_id", "<b>", "status_tag"):
        assert banned not in res["reply_md"]
    conn.close()


def test_v02_arabic_fallback_no_raw_rag(tmp_path):
    from app.services import account_manager
    from app.services.rag.indexing_service import IndexingService
    conn = _db(tmp_path, "arfb.db")
    root = tmp_path / "ws"
    corpus = {
        "knowledge/brand.md": "# Brand\n\nAcme Test Company CANARY-RAW-7734 builds custom websites with live-link proof.\n",
        "strategy/founder-decisions.md": "# Decisions\n\nAgencies CANARY-RAW-7734 are a founder-confirmed major segment for white-label work.\n",
    }
    for rel, text in corpus.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    IndexingService(conn, root).build_or_update()
    for q in ("ايه رأيك في شركة نجم؟", "ليه اخترنا الوكالات؟"):
        turn = account_manager.handle_turn(conn, root, q)
        assert "CANARY-RAW-7734" not in turn["reply_md"], q
        for banned in ("[VERIFIED]", "chunk_id", "<b>", "status_tag"):
            assert banned not in turn["reply_md"], (q, banned)
    src = account_manager.handle_turn(conn, root, "وريني المصادر",
                                      prior_provenance=turn["provenance"])
    assert "المصادر" in src["reply_md"] or "Sources" in src["reply_md"]
    conn.close()


def test_v02_mode_contract(tmp_path, monkeypatch):
    from app import deps
    from app.database.sqlite import connect
    from app.services.config_service import ConfigService
    from app.services.llm.config import config_from_env

    db = tmp_path / "mode-config.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    deps.init_db(db)
    with connect(db) as conn:
        ConfigService.set_setting("manager_provider", "deterministic", conn=conn)
    monkeypatch.setenv("MANAGER_MODE", "agentic")
    monkeypatch.setenv("MANAGER_PROVIDER", "openai")
    cfg = config_from_env()
    assert cfg.manager_provider == "deterministic"
    assert cfg.agentic_enabled is False

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from app.services.llm.openai_provider import OpenAIProvider
    try:
        OpenAIProvider().complete(system="s", messages=[], tools=[])
        assert False, "missing key must raise"
    except RuntimeError:
        pass
    from app.services import manager_loop
    from app.services.tools import build_default_registry
    conn = _db(tmp_path, "mode.db")
    from app.services import state as store
    c = store.get_or_create_conversation(conn, project_id="starter")
    class Boom:
        name = "boom"
        def complete(self, **kw):
            raise ConnectionError("down")
        def supports_server_websearch(self):
            return False
    res = manager_loop.run_manager_turn(conn, project_id="starter", conversation_id=c["id"],
                                        user_text="hi", provider=Boom(),
                                        registry=build_default_registry(), root=str(tmp_path))
    assert res["mode"] == "PROVIDER_ERROR" and res["reply_md"]
    conn.close()


def test_v02_cross_project_job_isolation(tmp_path):
    from app.services import state as store
    from app.services import jobs as jobsvc
    from app.services.tools import build_default_registry
    conn = _db(tmp_path, "jobs.db")
    store.create_project(conn, "other-project")
    ca = store.get_or_create_conversation(conn, project_id="starter")
    ja = jobsvc.submit(conn, tmp_path, tmp_path, kind="a", cmd=["echo", "a"],
                       side_effect="green", project_id="starter", conversation_id=ca["id"])
    reg = build_default_registry()
    # direct cross-project read raises
    try:
        repos.BackgroundJobs.get(conn, ja["id"], "other-project")
        assert False, "cross-project job read must raise"
    except ValueError:
        pass
    # tool-level: invisible + error, never data
    out = reg.execute(conn, project_id="other-project", root=str(tmp_path),
                      name="get_job_status", args={"job_id": ja["id"]})
    assert out["ok"] is False
    latest = reg.execute(conn, project_id="other-project", root=str(tmp_path),
                         name="get_job_status", args={})
    assert latest.get("job_id", None) != ja["id"]
    conn.close()
