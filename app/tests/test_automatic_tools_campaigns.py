"""Focused natural-language tool routing and Campaign Draft acceptance tests."""
from contextlib import contextmanager

import pytest

from app.database import repos
from app.graphs.account_manager import classify_to_route
from app.graphs.account_manager_graph import _current_turn_write_constraints
from app.graphs.state import initial_state
from app.services.tools import build_default_registry
from app.services.tools.registry import Registry, ToolDef
from app.services.tools.selector import select_registered_tool

from app.tests.test_dev010_graph_flow import _db, _graph, _state


def test_registered_tool_selection_uses_catalog_for_natural_language():
    selected = select_registered_tool(
        "Look up current external facts about performance marketing trends.",
        catalog=build_default_registry().catalog(),
    )
    assert selected and selected["capability"] == "web_search"
    assert selected["arguments"]["query"].startswith("Look up current")


def test_generic_attention_question_does_not_select_an_unrelated_tool():
    catalog = build_default_registry().catalog()
    assert select_registered_tool("What needs my attention?", catalog=catalog) is None

    selected = select_registered_tool(
        "What draft campaigns do we have?", catalog=catalog,
    )
    assert selected and selected["capability"] == "get_campaigns"


def test_generic_strategy_check_does_not_select_an_unrelated_audit():
    catalog = build_default_registry().catalog()
    assert select_registered_tool("Strategy check", catalog=catalog) is None

    selected = select_registered_tool(
        "Check our website", catalog=catalog,
        project_state={"website": "https://example.test"},
    )
    assert selected and selected["capability"] == "website_marketing_audit"


def test_slash_command_remains_optional_for_website_audit():
    natural = classify_to_route("Analyze my website SEO.")
    slash = classify_to_route("/audit https://example.test")
    assert natural == slash == "tool_capability"


@pytest.mark.parametrize(
    ("user_request", "expected_title"),
    [
        ("Create a marketing campaign for my new product.", "Campaign: my new product"),
        ("Start a campaign to increase sales for my store.",
         "Campaign: increase sales for my store"),
    ],
)
def test_natural_language_campaign_request_persists_draft_in_project(
    tmp_path, user_request, expected_title
):
    conn = _db(tmp_path)
    out = _graph(conn).invoke(
        _state(user_request), config={"configurable": {"thread_id": "t1"}},
    )
    campaigns = repos.Campaigns.list(conn, "p1")
    assert len(campaigns) == 1
    campaign = campaigns[0]
    assert campaign["project_id"] == "p1"
    assert campaign["title"] == expected_title
    assert campaign["status"] == "drafted"
    assert out["write_results"]["campaign"]["campaign_id"] == campaign["id"]
    assert "Campaign draft created successfully." in out["final_answer"]
    assert f"Campaign ID: {campaign['id']}" in out["final_answer"]
    assert f"/app/campaigns/{campaign['id']}" in out["final_answer"]


@pytest.mark.parametrize(
    "user_request",
    [
        "Give me some ideas for a marketing campaign.",
        "Don't create anything yet. Just help me plan a campaign.",
        "Create a campaign, but do not create anything yet.",
    ],
)
def test_ideas_and_no_write_requests_do_not_persist_campaigns(tmp_path, user_request):
    conn = _db(tmp_path)
    out = _graph(conn).invoke(
        _state(user_request), config={"configurable": {"thread_id": "t1"}},
    )
    assert repos.Campaigns.list(conn, "p1") == []
    assert "Campaign draft created successfully." not in out["final_answer"]
    if "do not create anything" in user_request.lower():
        assert _current_turn_write_constraints(user_request)["analysis_only"] is True


def test_same_turn_retry_keeps_one_campaign_draft(tmp_path):
    conn = _db(tmp_path)
    graph = _graph(conn)
    state = _state("Create a marketing campaign for my new product.")
    config = {"configurable": {"thread_id": "t1"}}
    first = graph.invoke(state, config=config)
    second = graph.invoke(state, config=config)
    rows = repos.Campaigns.list(conn, "p1")
    assert len(rows) == 1
    assert rows[0]["status"] == "drafted"
    assert first["write_results"]["campaign"]["campaign_id"] == \
        second["write_results"]["campaign"]["campaign_id"] == rows[0]["id"]


def test_campaign_is_returned_by_project_campaigns_api(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    _graph(conn).invoke(
        _state("Create a marketing campaign for my new product."),
        config={"configurable": {"thread_id": "t1"}},
    )

    @contextmanager
    def db_context():
        yield conn

    from app.routes import api_spa
    monkeypatch.setattr(api_spa.deps, "get_db", db_context)
    response = api_spa.spa_campaigns(project_id="p1")
    assert response["ok"] is True
    assert len(response["data"]) == 1
    assert response["data"][0]["status"] == "drafted"
    assert response["data"][0]["project_id"] == "p1"


def test_campaigns_read_request_executes_registered_tool_and_returns_real_row(tmp_path):
    conn = _db(tmp_path)
    created = _graph(conn).invoke(
        _state("Create a marketing campaign for my new product."),
        config={"configurable": {"thread_id": "t1"}},
    )
    campaign_id = created["write_results"]["campaign"]["campaign_id"]
    graph = _graph(conn)
    out = graph.invoke(
        initial_state(project_id="p1", conversation_id="c1", turn_id="t2",
                      user_request="What draft campaigns do we have?"),
        config={"configurable": {"thread_id": "t2"}},
    )
    assert "get_campaigns" in out["final_answer"]
    assert campaign_id in out["final_answer"]
    assert "drafted" in out["final_answer"]
    assert repos.ToolRuns.for_tool(conn, "get_campaigns", "p1")


def test_meta_launch_is_blocked_pending_integration_and_approval(tmp_path):
    conn = _db(tmp_path)
    out = _graph(conn).invoke(
        _state("Launch this campaign on Meta."),
        config={"configurable": {"thread_id": "t1"}},
    )
    assert repos.Campaigns.list(conn, "p1") == []
    assert out["write_results"]["external_action"]["status"] == "blocked"
    assert out["write_results"]["external_action"]["approval_required"] is True
    assert "Settings → Integrations" in out["final_answer"]
    assert "explicit approval" in out["final_answer"].lower()
    assert "No content was published" in out["final_answer"]


def test_missing_instagram_provider_explains_configuration_without_claiming_run(
    tmp_path, monkeypatch
):
    from app.graphs import tool_capability

    conn = _db(tmp_path)
    monkeypatch.setattr(
        "app.services.social.instagram.router.audit_capability",
        lambda *args, **kwargs: {
            "evidence": {"status": "unavailable"}, "account": None, "posts": [],
            "unknowns": [], "attempts": [], "has_configured_provider": False,
            "primary_failure": None,
        },
    )
    out = tool_capability.resolve_and_execute(
        conn, project_id="p1", capability="instagram_public_profile",
        args={"handle": "samplebrand"}, turn_id="t1",
    )
    assert out["status"] == "blocked"
    assert "Tool execution: instagram_audit — Blocked" in out["answer"]
    assert "Settings → Integrations" in out["answer"]
    assert "did not run" in out["answer"]


def test_unavailable_catalog_tool_is_blocked_without_calling_handler():
    from app.graphs.adapters import ToolRegistryAdapter
    from app.graphs.tool_capability import _execute_registered_read_tool

    called = []
    registry = Registry()
    registry.register(ToolDef(
        name="lookup_weather",
        description="Look up current weather and forecast for a location.",
        parameters={"type": "object", "properties": {}},
        handler=lambda *_args, **_kwargs: called.append(True) or {"ok": True},
    ))
    registry._handlers["lookup_weather"] = None
    adapter = ToolRegistryAdapter(registry)
    selected = select_registered_tool(
        "Look up the weather forecast in Cairo?", catalog=adapter.catalog(),
    )
    assert selected and selected["capability"] == "lookup_weather"
    events = []
    out = _execute_registered_read_tool(
        None, project_id="p1", tool_name="lookup_weather", args={},
        on_event=lambda *event: events.append(event),
        registry=registry,
    )
    assert out["status"] == "blocked"
    assert "unavailable" in out["answer"].lower()
    assert called == []
    assert events[-1][0] == "tool_failed"
    assert events[-1][1]["status"] == "BLOCKED"


def test_selected_tool_missing_required_argument_is_blocked_before_execution(
    tmp_path, monkeypatch
):
    from app.graphs import intent as intent_module

    conn = _db(tmp_path)
    monkeypatch.setattr(intent_module, "classify_intent", lambda _text: {})
    monkeypatch.setattr(
        "app.services.tools.selector.select_registered_tool",
        lambda *_args, **_kwargs: {
            "intent_type": "TOOL_CAPABILITY", "capability": "lookup_weather",
            "arguments": {}, "missing": ["location"],
        },
    )
    events = []
    out = _graph(conn).invoke(
        _state("Check the weather forecast."),
        config={"configurable": {
            "thread_id": "t1",
            "event_callback": lambda *event: events.append(event),
        }},
    )

    assert "lookup_weather — Blocked" in out["final_answer"]
    assert "provide: location" in out["final_answer"]
    assert not repos.ToolRuns.for_tool(conn, "lookup_weather", "p1")
    assert events[-1][0] == "tool_failed"
    assert events[-1][1]["status"] == "BLOCKED"


def test_existing_website_capability_keeps_project_url_autofill(tmp_path, monkeypatch):
    from app.graphs import tool_capability

    conn = _db(tmp_path)
    executed = []
    monkeypatch.setattr(
        tool_capability,
        "resolve_and_execute",
        lambda _conn, **kwargs: executed.append(kwargs) or {
            "answer": "Tool execution: website_marketing_audit — Completed\n\n{}",
            "tool_run_id": "", "social_results": [],
        },
    )
    _graph(conn).invoke(
        _state("Analyze my website SEO."),
        config={"configurable": {"thread_id": "t1"}},
    )

    assert len(executed) == 1
    assert executed[0]["capability"] == "website_marketing_audit"


def test_provider_completion_does_not_change_campaign_write_semantics(tmp_path):
    from app.graphs.account_manager_graph import build_account_manager_graph

    conn = _db(tmp_path)
    for provider_name in ("local", "openrouter"):
        graph = build_account_manager_graph(
            conn_factory=lambda: conn,
            project_lookup=lambda _pid: {"id": "p1"},
            complete_fn=lambda provider=provider_name, **_kwargs: f"{provider} response",
            employee_fn=lambda _task, _role: {"findings": [], "sources": []},
        )
        turn_id = f"t-{provider_name}"
        state = initial_state(project_id="p1", conversation_id="c1", turn_id=turn_id,
                              user_request="Create a marketing campaign for my new product.")
        out = graph.invoke(state, config={"configurable": {"thread_id": turn_id}})
        rows = repos.Campaigns.list(conn, "p1")
        assert len(rows) == (1 if provider_name == "local" else 2)
        assert out["write_results"]["campaign"]["ok"] is True
        assert "Campaign draft created successfully." in out["final_answer"]
