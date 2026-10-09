"""DEV-007R-HOTFIX-3 W1 — graph context orchestration & RAG gating contract.

Locks the frozen context contract on the REAL compiled LangGraph path:
conversation/project hydration reaches the model with labeled sections,
Arabic recap routes conversation_meta, website intents are detected
deterministically, knowledge routes gate RAG, and turn_summary is persisted.
"""
import json
from types import SimpleNamespace

import pytest

LANGGRAPH = False
try:
    import importlib.util as _ilu
    LANGGRAPH = _ilu.find_spec("langgraph") is not None
except Exception:
    LANGGRAPH = False


# ------------------------------------------------------------ fixtures

def _db(tmp_path, name="hfx3.db"):
    from app.database.sqlite import connect
    from app.database.seed import ensure_seed

    p = tmp_path / name
    conn = connect(p)
    ensure_seed(conn)
    return conn


def _graph(conn, complete_fn=None, **kw):
    from app.graphs.account_manager_graph import build_account_manager_graph

    return build_account_manager_graph(conn_factory=lambda: conn,
                                       complete_fn=complete_fn, **kw)


def _invoke(graph, text, conversation_id="c1", turn_id="t-default"):
    from app.graphs.state import initial_state

    return graph.invoke(
        initial_state(project_id="starter", conversation_id=conversation_id,
                      turn_id=turn_id, user_request=text),
        config={"configurable": {"thread_id": turn_id}})


def _capture(return_value="MODEL-ANSWER"):
    calls = []

    def fake(*, turn_id, project_id, user_request, route,
             conversation_id="", context=None):
        calls.append({"turn_id": turn_id, "project_id": project_id,
                      "user_request": user_request, "route": route,
                      "conversation_id": conversation_id,
                      "context": context})
        return return_value

    return calls, fake


class _FakeStreamingRouter:
    def __init__(self, answer="MODEL-ANSWER"):
        self.answer = answer
        self.calls = []

    def complete_streaming(self, conn, **kwargs):
        self.calls.append(kwargs)
        on_token = kwargs.get("on_token")
        if callable(on_token):
            try:
                on_token(delta=self.answer, sequence=1,
                         call_id=kwargs.get("call_id", "") or "cid-fake")
            except Exception:
                pass
        return SimpleNamespace(text=self.answer), None


def _deps_db(tmp_path, monkeypatch, name="hfx3-events.db"):
    from app import deps

    db = tmp_path / name
    monkeypatch.setattr(deps, "DB_PATH", db)
    deps.init_db(db)
    return db


def _real_complete(router):
    from app.routes.graph_runtime import _make_complete_fn

    return _make_complete_fn(router)


def _insert_message(conn, cid, msg_id, role, body, client_message_id="",
                    created_at="2026-09-25T00:00:02Z"):
    from app.database import repos

    repos.Messages.insert(conn, {
        "id": msg_id, "conversation_id": cid, "role": role,
        "body_md": body, "citations_json": "[]",
        "client_message_id": client_message_id, "created_at": created_at})


def _conversation(conn):
    from app.services import state as store

    return store.create_conversation(conn, project_id="starter")["id"]


_AR_TEXT = "عايز ملخص ريكاب عن كل حاجة حصلت في الشات ده"


# ------------------------------------------------------------ t1 context

@pytest.mark.skipif(not LANGGRAPH, reason="langgraph not installed")
def test_multi_turn_conversation_context_reaches_model(tmp_path):
    conn = _db(tmp_path, "hfx3-t1.db")
    cid = _conversation(conn)
    _insert_message(conn, cid, "m1", "user", "شغل التسويق للمشروع",
                    "", "2026-09-25T00:00:01Z")
    _insert_message(conn, cid, "m2", "assistant",
                    "ASSISTANT-TURN-ONE: خطة الإطلاق جاهزة.",
                    "", "2026-09-25T00:00:02Z")
    calls, fake = _capture()
    graph = _graph(conn, complete_fn=fake)
    res = _invoke(graph, "why did we choose agencies",
                  conversation_id=cid, turn_id="t1k1")
    assert calls, "model leg must run for a knowledge-route turn"
    ctx = calls[0]["context"]
    bodies = [str(m.get("body_md", "")) for m in ctx["conversation_context"]]
    assert any("ASSISTANT-TURN-ONE" in b for b in bodies)
    assert "conversation_context" in ctx["context_sources_used"]
    assert res.get("route") in ("knowledge", "state_only")
    assert res.get("final_answer") == "MODEL-ANSWER"


# ------------------------------------------------------------ t2 project state

@pytest.mark.skipif(not LANGGRAPH, reason="langgraph not installed")
def test_project_state_injected_into_model_context(tmp_path, monkeypatch):
    from app.database import repos

    _deps_db(tmp_path, monkeypatch)
    monkeypatch.delenv("OMOS_RAG_DEBUG", raising=False)
    conn = _db(tmp_path, "hfx3-t2.db")
    # DEV-008: set the project explicitly instead of leaning on the seed. The
    # neutral first-run seed no longer names a business, and this test's
    # subject is "does project state reach the model", not "what is seeded".
    project = repos.Projects.get(conn, "starter") or {}
    project.update({
        "id": "starter",
        "name": "Acme Websites",
        "website": "https://acme-test.example/",
        "goal": "Get more customers",
        "status": project.get("status") or "active",
        "settings_json": project.get("settings_json") or "{}",
        "created_at": project.get("created_at") or "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    })
    repos.Projects.upsert(conn, project)

    router = _FakeStreamingRouter()
    graph = _graph(conn, complete_fn=_real_complete(router))
    res = _invoke(graph, "why did we choose agencies", turn_id="t2k2")
    assert res["project_state"]["name"] == "Acme Websites"
    assert res["project_state"]["website"] == "https://acme-test.example/"
    assert router.calls, "model leg must be invoked"
    content = router.calls[0]["messages"][0]["content"]
    assert "[PROJECT CONTEXT]" in content
    assert "Acme Websites" in content
    assert "[CURRENT USER TURN] source: user_turn" in content
    assert "SOURCE PRIORITY" in router.calls[0]["system"]
    assert "HONESTY" in router.calls[0]["system"]


# ------------------------------------------------------------ t3 arabic meta

@pytest.mark.skipif(not LANGGRAPH, reason="langgraph not installed")
def test_arabic_meta_recap_routes_and_includes_assistant_line(tmp_path):
    from app.graphs.intent import classify_intent

    text = _AR_TEXT
    assert classify_intent(text)["route"] == "conversation_meta"
    conn = _db(tmp_path, "hfx3-t3.db")
    cid = _conversation(conn)
    _insert_message(conn, cid, "m1", "user", "اعمل تحليل حساب الشركة",
                    "", "2026-09-25T00:00:01Z")
    _insert_message(conn, cid, "m2", "assistant",
                    "حللنا حساب الانستجرام acme_test ووجدنا ٥٤٠٠ متابع.",
                    "", "2026-09-25T00:00:02Z")
    calls, fake = _capture()
    graph = _graph(conn, complete_fn=fake)
    res = _invoke(graph, text, conversation_id=cid, turn_id="t3k3")
    assert res.get("route") == "conversation_meta"
    final = str(res.get("final_answer") or "")
    assert "المساعد" in final
    assert "acme_test" in final.lower()
    assert not calls, "meta recap is deterministic; model must not run"
    assert not res.get("rag_invoked", False)


# ------------------------------------------------------------ t4 website intents

def test_website_intent_detection_table():
    from app.graphs.intent import classify_intent, detect_website_intent

    audit = detect_website_intent("عايزك تحلل موقع نجم acme-test.example")
    assert audit is not None
    assert audit["capability"] == "website_marketing_audit"
    assert audit["arguments"]["url"] == "acme-test.example"
    assert audit["missing"] == []

    crawl = detect_website_intent("crawl our website https://acme-test.example")
    assert crawl["capability"] == "website_crawl"
    assert crawl["arguments"]["url"] == "https://acme-test.example"

    opened = detect_website_intent("افتح موقع acme-test.example")
    assert opened["capability"] == "website_fetch"
    assert opened["arguments"]["url"] == "acme-test.example"

    assert detect_website_intent("اي رايك في نجم") is None
    assert detect_website_intent("scrap this instagram account @acme_test") is None

    routed = classify_intent("عايزك تحلل موقع نجم acme-test.example")
    assert routed["route"] == "tool_capability"
    assert routed["capability"]["capability"] == "website_marketing_audit"
    assert classify_intent("اي رايك في نجم")["route"] == ""

    ig = classify_intent("scrap this instagram account @acme_test")
    assert ig["route"] == "tool_capability"
    assert ig["capability"]["capability"] == "instagram_public_profile"


# ------------------------------------------------------------ t5 knowledge RAG

@pytest.mark.skipif(not LANGGRAPH, reason="langgraph not installed")
def test_knowledge_route_invokes_rag_and_labeled_context(tmp_path, monkeypatch):
    monkeypatch.delenv("OMOS_RAG_DEBUG", raising=False)
    _deps_db(tmp_path, monkeypatch, "hfx3-t5-events.db")
    workspace = tmp_path / "ws5"
    (workspace / "knowledge").mkdir(parents=True)
    (workspace / "knowledge" / "brand.md").write_text(
        "# Brand\n\nAcme Test Company builds custom websites with live-link proof.\n",
        encoding="utf-8")
    conn = _db(tmp_path, "hfx3-t5.db")
    from app.services.rag.indexing_service import IndexingService

    IndexingService(conn, workspace).build_or_update()
    router = _FakeStreamingRouter()
    graph = _graph(conn, complete_fn=_real_complete(router))
    res = _invoke(graph, "why do we use live-link proof websites?",
                  turn_id="t5k5")
    assert res.get("rag_invoked") is True
    hits = res.get("rag_hits") or []
    assert hits, "seeded corpus must surface at least one chunk"
    assert hits[0]["path"] == "knowledge/brand.md"
    assert router.calls
    content = router.calls[0]["messages"][0]["content"]
    assert "[PROJECT KNOWLEDGE]" in content
    assert "SOURCE: knowledge/brand.md#" in content


# ------------------------------------------------------------ t6 clarification

@pytest.mark.skipif(not LANGGRAPH, reason="langgraph not installed")
def test_unresolvable_intent_empty_context_reports_unavailable_provider(tmp_path):
    from app.database.sqlite import connect

    plain = connect(tmp_path / "hfx3-t6-empty.db")
    def fake(**kwargs):
        return None

    graph = _graph(plain, complete_fn=fake)
    res = _invoke(graph, "zzq explaining the unusual violet badgers",
                  conversation_id="c-empty", turn_id="t6k6")
    final = str(res.get("final_answer") or "")
    assert "couldn't generate an answer" in final
    assert "selected provider is available" in final
    assert res.get("context_sources_used") == ["user_turn"]


# ------------------------------------------------------------ t7 turn summary

@pytest.mark.skipif(not LANGGRAPH, reason="langgraph not installed")
def test_turn_summary_event_persisted_with_route_and_sources(tmp_path):
    conn = _db(tmp_path, "hfx3-t7.db")
    calls, fake = _capture()
    graph = _graph(conn, complete_fn=fake)
    _invoke(graph, "why did we choose agencies", turn_id="t7k7")
    from app.database import repos

    rows = repos.ExecutionEvents.for_turn(conn, "t7k7")
    summaries = [r for r in rows if r.get("event_type") == "turn_summary"]
    assert summaries, "turn_summary must best-effort persist in respond"
    meta = json.loads(summaries[0].get("metadata_json") or "{}")
    assert meta.get("route") == "knowledge"
    assert "context_sources_used" in meta
    assert isinstance(meta.get("context_sources_used"), list)
    assert meta.get("provider") == ""
    assert meta.get("actual_model") == ""


# ------------------------------------------------------------ t8 negatives

@pytest.mark.skipif(not LANGGRAPH, reason="langgraph not installed")
def test_meta_and_tool_routes_never_invoke_retrieval(tmp_path, monkeypatch):
    conn = _db(tmp_path, "hfx3-t8.db")
    monkeypatch.setattr(
        "app.graphs.tool_capability.resolve_and_execute",
        lambda conn, **kw: {"answer": "TOOL-RAN", "tool_run_id": "tr-x",
                            "social_results": []})
    calls, fake = _capture()
    graph = _graph(conn, complete_fn=fake)
    res = _invoke(graph, "scrap this instagram account @acme_test",
                  turn_id="t8k8")
    assert res.get("route") == "tool_capability"
    assert not res.get("rag_invoked", False)
    assert not (res.get("rag_hits") or [])
    assert not res.get("rag_query", "")
    meta_res = _invoke(graph, "what this chat talking about", turn_id="t8k8b")
    assert meta_res.get("route") == "conversation_meta"
    assert not meta_res.get("rag_invoked", False)
    assert not (meta_res.get("rag_hits") or [])
