"""DEV-007R-HOTFIX-2 — tool intent routing & conversation context.

Locks the exact founder reproduction on the REAL compiled LangGraph path
plus the deterministic intent layer and no-RAG-fallback guarantees.
"""
import json

import pytest

LANGGRAPH = False
try:
    import importlib.util as _ilu
    LANGGRAPH = _ilu.find_spec("langgraph") is not None
except Exception:
    LANGGRAPH = False


# ------------------------------------------------------------ fixtures

class FakeApifyProvider:
    """Deterministic provider standing in for the Apify chain."""

    def __init__(self, script=None):
        self.script = script

    def available(self):
        return True, "fake provider ready"

    def audit_public(self, username, project_id=None):
        return dict(self.script)

    def audit_owned(self, username, project_id=None):
        return dict(self.script)


_VERIFIED = {
    "account": {"handle": "acme_test", "display_name": "Acme Test Company",
                "followers": 5400, "following": 120, "posts_count": 42},
    "posts": [{"caption": "Launch week: Acme Test Company drops a free Instagram audit tool."},
              {"caption": "Behind the builds: AI marketing agents."}],
    "insights": {},
    "evidence": {"source": "fakeapify", "status": "verified",
                 "collected_at": "2026-09-24T00:00:00Z"},
    "unknowns": [],
}
_FAILED = {
    "account": None, "posts": [], "insights": {},
    "evidence": {"source": "none", "status": "unavailable", "collected_at": ""},
    "unknowns": ["fakeapify raised: boom"],
}


def _fake_router(monkeypatch, script):
    from app.services.social.instagram import router as ig_router
    from app.services.social.instagram import meta_provider as _mp
    from app.services.social.instagram.brightdata_provider import BrightDataProvider
    from app.services.social.instagram.browser_provider import BrowserProvider

    monkeypatch.setattr(
        ig_router, "_PROVIDERS",
        {"apify": lambda: FakeApifyProvider(script),
         "brightdata": BrightDataProvider, "browser": BrowserProvider,
         "meta": _mp.MetaProvider})
    return ig_router


def _db(tmp_path, name="hfx2.db"):
    from app.database.sqlite import connect
    from app.database.seed import ensure_seed

    p = tmp_path / name
    conn = connect(p)
    ensure_seed(conn)
    return conn


def _set_project_handle(conn, handle="acme_test", status="VERIFIED"):
    from app.database import repos

    repos.SocialAccounts.upsert(conn, {
        "project_id": "starter", "platform": "instagram", "handle": handle,
        "url": f"https://instagram.com/{handle}", "status": status,
        "source": "manual"})


def _graph(conn):
    from app.graphs.account_manager_graph import build_account_manager_graph

    return build_account_manager_graph(
        conn_factory=lambda: conn, complete_fn=None)


def _invoke(graph, text, conversation_id="c1", turn_id="t-default"):
    from app.graphs.state import initial_state

    return graph.invoke(
        initial_state(project_id="starter", conversation_id=conversation_id,
                      turn_id=turn_id, user_request=text),
        config={"configurable": {"thread_id": turn_id}})


# ------------------------------------------------------------ intent layer

def test_intent_capability_variants():
    from app.graphs.intent import detect_capability_intent

    cap = detect_capability_intent("scrap this instagram account @acme_test")
    assert cap["capability"] == "instagram_public_profile"
    assert cap["arguments"]["handle"] == "acme_test"
    assert cap["missing"] == []
    assert detect_capability_intent("@acme_test on instagram")["arguments"]["handle"] == "acme_test"
    assert (detect_capability_intent("https://www.instagram.com/acme_test/")
            ["arguments"]["handle"] == "acme_test")
    assert detect_capability_intent("what are the marketing ideas?") is None
    assert detect_capability_intent("strategy is off") is None


@pytest.mark.parametrize("text", ["scrap instagram", "scrape instagram",
                                  "check instagram account"])
def test_intent_missing_handle_is_structured(text):
    from app.graphs.intent import detect_capability_intent

    cap = detect_capability_intent(text)
    assert cap is not None
    assert cap["missing"] == ["handle"]


def test_intent_conversation_meta():
    from app.graphs.intent import detect_conversation_meta

    assert detect_conversation_meta("what this chat talking about")
    assert detect_conversation_meta("summarize our conversation")
    assert not detect_conversation_meta("scrap instagram")


# ------------------------------------------------------------ legacy routing

def test_legacy_routing_precedence():
    from app.graphs.account_manager import classify_to_route

    assert classify_to_route("scrap this instagram account @acme_test") == "tool_capability"
    assert classify_to_route("scrap instagram") == "tool_capability"
    assert classify_to_route("what this chat talking about") == "conversation_meta"
    assert classify_to_route("what needs my attention?") == "state_only"
    assert classify_to_route("please approve and send this") == "approval_operation"
    assert classify_to_route("deep research competitors vs us", deep=True) == "deep_research"


# ------------------------------------------------------------ compiled graph

@pytest.mark.skipif(not LANGGRAPH, reason="langgraph not installed")
def test_founder_reproduction_tool_run_and_live_data(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    _set_project_handle(conn)
    _fake_router(monkeypatch, _VERIFIED)
    graph = _graph(conn)

    res = _invoke(graph, "scrap this instagram account @acme_test", turn_id="t1")
    assert res.get("route") == "tool_capability"
    final = str(res.get("final_answer") or "")
    assert "acme_test" in final
    assert "Fakeapify" in final or "fakeapify" in final
    assert "Launch week" in final
    rows = conn.execute("SELECT tool_id, provider, status FROM tool_runs").fetchall()
    assert [(r[0], r[2]) for r in rows] == [("instagram_public_profile", "success")]
    assert rows[0][1] == "apify", "provider chain name must be recorded in the tool run"
    # the live-data reply is derived from this turn's successful run artifact
    assert f"@acme_test (source: fakeapify)" in final
    conn.close()


@pytest.mark.skipif(not LANGGRAPH, reason="langgraph not installed")
def test_scrap_instagram_without_handle_asks_for_it(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    _fake_router(monkeypatch, _VERIFIED)
    graph = _graph(conn)
    res = _invoke(graph, "scrap instagram", turn_id="t2")
    final = str(res.get("final_answer") or "")
    assert "Which Instagram account should I scrape?" in final
    assert res.get("route") == "tool_capability"


@pytest.mark.skipif(not LANGGRAPH, reason="langgraph not installed")
def test_scrap_instagram_resolves_project_handle(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    _set_project_handle(conn)
    _fake_router(monkeypatch, _VERIFIED)
    graph = _graph(conn)
    res = _invoke(graph, "scrap instagram", turn_id="t3")
    final = str(res.get("final_answer") or "")
    assert "acme_test" in final
    assert any(r[0] == "instagram_public_profile"
               for r in conn.execute("SELECT tool_id FROM tool_runs").fetchall())


@pytest.mark.skipif(not LANGGRAPH, reason="langgraph not installed")
def test_scraping_tool_continuation(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    _set_project_handle(conn)
    _fake_router(monkeypatch, _VERIFIED)
    graph = _graph(conn)
    res = _invoke(graph, "just use scraping tool", turn_id="t4")
    final = str(res.get("final_answer") or "")
    assert res.get("route") == "tool_capability"
    assert "acme_test" in final


@pytest.mark.skipif(not LANGGRAPH, reason="langgraph not installed")
def test_tool_failure_honest_limitation_no_rag(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    _set_project_handle(conn)
    _fake_router(monkeypatch, _FAILED)
    graph = _graph(conn)
    res = _invoke(graph, "scrap this instagram account @acme_test", turn_id="t5")
    final = str(res.get("final_answer") or "")
    assert "I couldn't retrieve @acme_test from Instagram." in final
    assert "[Retry]" in final
    assert "[Test Instagram Connection]" in final
    assert "free homepage" not in final
    assert "recorded knowledge" not in final
    rows = conn.execute("SELECT tool_id, status FROM tool_runs").fetchall()
    assert ("instagram_public_profile", "failed") in [tuple(r) for r in rows]


@pytest.mark.skipif(not LANGGRAPH, reason="langgraph not installed")
def test_conversation_meta_summary_routes_to_thread(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    _fake_router(monkeypatch, _VERIFIED)
    from app.database import repos
    from app.services import state as store

    convo = store.create_conversation(conn, project_id="starter")
    cid = convo["id"]
    repos.Messages.insert(conn, {
        "id": "m1", "conversation_id": cid, "role": "user",
        "body_md": "scrap this instagram account @acme_test",
        "citations_json": "[]", "client_message_id": "cm1",
        "created_at": "2026-09-24T00:00:01Z"})
    repos.Messages.insert(conn, {
        "id": "m2", "conversation_id": cid, "role": "assistant",
        "body_md": "Instagram research for @acme_test ...",
        "citations_json": "[]", "client_message_id": "", "created_at": "2026-09-24T00:00:02Z"})
    repos.Messages.insert(conn, {
        "id": "m3", "conversation_id": cid, "role": "user",
        "body_md": "what this chat talking about",
        "citations_json": "[]", "client_message_id": "cm3",
        "created_at": "2026-09-24T00:00:03Z"})
    graph = _graph(conn)
    res = _invoke(graph, "what this chat talking about",
                  conversation_id=cid, turn_id="t6")
    final = str(res.get("final_answer") or "")
    assert res.get("route") == "conversation_meta"
    assert "@acme_test" in final
    assert "scrap this instagram account" in final
    assert "recorded knowledge" not in final
    conn.close()


# ------------------------------------------------------------ synthesis units

def test_synthesis_templates():
    from app.graphs.tool_capability import synthesize_failure, synthesize_success

    ok = synthesize_success("acme_test", _VERIFIED)
    assert "acme_test" in ok and "Launch week" in ok
    fail = synthesize_failure("acme_test", "boom")
    assert "couldn't retrieve @acme_test" in fail
