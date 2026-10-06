"""DEV-012 focused coverage for current-turn write constraints.

All database-backed cases use temporary SQLite files and isolated projects.
These tests are authored for the W3 packet; execution belongs to QA.
"""

import pytest

from app.database import repos
from app.database.sqlite import connect
from app.graphs.account_manager_graph import (
    _current_turn_write_constraints,
    _explicit_write_intents,
    _filter_forbidden_write_intents,
    build_account_manager_graph,
)
from app.graphs.state import initial_state
from app.services.tools.propose_tools import register_propose_tools
from app.services.tools.registry import Registry


def _db(tmp_path):
    conn = connect(tmp_path / "dev012.sqlite")
    repos.Companies.upsert(conn, {
        "id": "co1", "name": "DEV-012 test", "website": "",
        "created_at": "now",
    })
    for project_id in ("p1", "p2"):
        repos.Projects.upsert(conn, {
            "id": project_id, "name": project_id,
            "website": "https://example.com", "goal": "Improve conversion",
            "status": "active", "settings_json": "{}",
            "created_at": "now", "updated_at": "now",
        })
    for suffix, project_id in (("1", "p1"), ("2", "p2")):
        repos.Conversations.upsert(conn, {
            "id": f"c{suffix}", "company_id": "co1", "project_id": project_id,
            "title": f"Conversation {suffix}", "created_at": "now",
            "updated_at": "now",
        })
        repos.Turns.upsert(conn, {
            "id": f"t{suffix}", "conversation_id": f"c{suffix}",
            "project_id": project_id, "status": "running",
            "created_at": "now", "updated_at": "now",
        })
    return conn


def _graph(conn, *, completion=None):
    return build_account_manager_graph(
        conn_factory=lambda: conn,
        project_lookup=lambda project_id: ({"id": project_id}
                                           if project_id in {"p1", "p2"} else None),
        complete_fn=completion or (lambda **_kwargs: "Prepared a reviewable response."),
        employee_fn=lambda _task, _role: {"findings": [], "sources": []},
    )


def _invoke(graph, text, *, project_id="p1", conversation_id="c1", turn_id="t1"):
    return graph.invoke(
        initial_state(project_id=project_id, conversation_id=conversation_id,
                      turn_id=turn_id, user_request=text),
        config={"configurable": {"thread_id": turn_id}},
    )


def test_explicit_campaign_prohibition_leaves_campaign_rows_unchanged(tmp_path):
    conn = _db(tmp_path)
    before = repos.Campaigns.list(conn, "p1")
    out = _invoke(
        _graph(conn),
        "Create a small campaign for the website, but do not create any campaign.",
    )
    assert repos.Campaigns.list(conn, "p1") == before == []
    assert out["write_results"]["campaign"] == {
        "ok": False, "status": "blocked_by_user_constraint", "action": "campaign"
    }
    assert "campaign" in out["final_answer"].lower()
    assert "skipped saving" in out["final_answer"].lower()


def test_dont_create_any_campaign_is_blocked_without_persisting(tmp_path):
    conn = _db(tmp_path)
    out = _invoke(_graph(conn), "Don't create any campaign.")
    assert repos.Campaigns.list(conn, "p1") == []
    assert out["write_results"]["campaign"] == {
        "ok": False, "status": "blocked_by_user_constraint", "action": "campaign"
    }


def test_campaign_suggestion_without_creation_does_not_call_proposal_tool(tmp_path):
    conn = _db(tmp_path)
    out = _invoke(_graph(conn), "Suggest a campaign without creating one.")
    assert repos.Campaigns.list(conn, "p1") == []
    assert out.get("write_results", {}).get("campaign") is None
    assert repos.ToolRuns.for_tool(conn, "propose_campaign", "p1") == []


def test_allowed_website_read_runs_with_campaign_suggestion_and_no_write(tmp_path):
    conn = _db(tmp_path)
    out = _invoke(
        _graph(conn),
        "Analyze https://example.com and search the web for ideas. "
        "Suggest what campaign could work. "
        "Do not create any campaign.",
    )
    audit_runs = repos.ToolRuns.for_tool(conn, "website_marketing_audit", "p1")
    assert out.get("capability_answer")
    assert any(row.get("status") == "success" for row in audit_runs)
    assert repos.Campaigns.list(conn, "p1") == []
    assert repos.ToolRuns.for_tool(conn, "propose_campaign", "p1") == []
    assert "campaign" in out["write_constraints"]["forbidden_actions"]


def test_analysis_only_blocks_persistence_without_blocking_analysis_route():
    constraints = _current_turn_write_constraints(
        "Analyze the website; do not save anything."
    )
    assert constraints["analysis_only"] is True
    intents, blocked = _filter_forbidden_write_intents(
        _explicit_write_intents("Create a campaign and prepare an experiment."),
        constraints,
    )
    assert intents["campaign"] is None
    assert intents["experiment"] is None
    assert set(blocked) == {"campaign", "experiment"}


@pytest.mark.parametrize(
    "restriction_text",
    ["Do not save this.", "Do not make changes."],
)
def test_plain_language_read_only_requests_set_analysis_only(restriction_text):
    assert _current_turn_write_constraints(restriction_text)["analysis_only"] is True


@pytest.mark.parametrize(
    ("constraint_text", "action"),
    [
        ("Do not create any campaign.", "campaign"),
        ("Do not create tasks.", "task"),
        ("Do not create an experiment.", "experiment"),
        ("ما تعملش حملة", "campaign"),
        ("متعملش أي حملة", "campaign"),
        ("اقترح حملة بس متعملهاش فعليًا", "campaign"),
        ("متعملش تاسكات", "task"),
        ("متعملش تجربة", "experiment"),
    ],
)
def test_requested_english_and_arabic_action_prohibitions(constraint_text, action):
    assert action in _current_turn_write_constraints(constraint_text)["forbidden_actions"]


def test_analysis_only_arabic_and_campaign_suggestion_without_persistence():
    analysis = _current_turn_write_constraints("حلل بس من غير ما تحفظ حاجة")
    assert analysis["analysis_only"] is True

    request = "اقترح حملة بس متعملهاش فعليًا"
    constraints = _current_turn_write_constraints(request)
    intents, blocked = _filter_forbidden_write_intents(
        _explicit_write_intents(request), constraints,
    )
    assert intents.get("campaign") is None
    assert "campaign" in blocked


def test_task_prohibition_suppresses_task_write_intent():
    constraints = _current_turn_write_constraints("Create a task, but do not create tasks.")
    intents, blocked = _filter_forbidden_write_intents(
        _explicit_write_intents("Create a task for the website."), constraints,
    )
    assert intents["task"] is None
    assert "task" in blocked


def test_mixed_allowed_task_is_saved_without_campaign_or_dependent_writes(tmp_path):
    conn = _db(tmp_path)
    out = _invoke(
        _graph(conn),
        "Analyze the website, create tasks, but do not create a campaign.",
    )
    campaigns = repos.Campaigns.list(conn, "p1")
    tasks = repos.Tasks.list(conn, "p1")
    assert campaigns == []
    assert len(tasks) == 1
    assert not tasks[0].get("campaign_id")
    assert repos.Experiments.list(conn, "p1") == []
    assert repos.Approvals.list(conn, project_id="p1") == []
    assert out["write_results"].get("campaign", {}).get("status") == "blocked_by_user_constraint"
    assert out["write_results"]["task"]["ok"] is True


def test_campaign_prohibition_creates_no_phantom_linked_writes_or_approval(tmp_path):
    conn = _db(tmp_path)
    out = _invoke(
        _graph(conn),
        "Create a campaign and its required work, but do not create any campaign.",
    )
    assert repos.Campaigns.list(conn, "p1") == []
    assert repos.Tasks.list(conn, "p1") == []
    assert repos.Experiments.list(conn, "p1") == []
    assert repos.Approvals.list(conn, project_id="p1") == []
    assert out["write_results"]["campaign"]["status"] == "blocked_by_user_constraint"
    assert "task" in out["write_results"]
    assert out["write_results"].get("approval") is None
    assert "write_error" not in out["write_results"]


def test_direct_registered_campaign_write_is_refused_before_handler(tmp_path):
    conn = _db(tmp_path)
    registry = Registry()
    register_propose_tools(registry)
    invocations = []
    registry._handlers["propose_campaign"] = (
        lambda *_args, **_kwargs: invocations.append("called") or {"ok": True}
    )
    events = []
    result = registry.execute(
        conn, project_id="p1", root=None, name="propose_campaign",
        args={"title": "Must not persist"},
        execution_context={"forbidden_actions": ["campaign"], "analysis_only": False},
        on_event=lambda event, payload: events.append((event, payload)),
    )
    assert result == {
        "ok": False, "status": "blocked_by_user_constraint", "action": "campaign"
    }
    assert invocations == []
    assert repos.Campaigns.list(conn, "p1") == []
    assert repos.ToolRuns.for_tool(conn, "propose_campaign", "p1")[0]["status"] == "blocked"
    assert [(event, payload.get("status")) for event, payload in events] == [
        ("tool_completed", "BLOCKED_BY_USER_CONSTRAINT")
    ]
    assert "obs" not in events[0][1]


def test_positive_dev010_campaign_write_and_project_isolation_remain_intact(tmp_path):
    conn = _db(tmp_path)
    out = _invoke(
        _graph(conn),
        "Create a small marketing campaign for this website.",
        project_id="p1", conversation_id="c1", turn_id="t1-positive",
    )
    own = repos.Campaigns.list(conn, "p1")
    foreign = repos.Campaigns.list(conn, "p2")
    assert out["write_results"]["campaign"]["ok"] is True
    assert len(own) == 1
    assert foreign == []
    assert repos.Campaigns.get(conn, own[0]["id"], "p1")
    with pytest.raises(ValueError, match="belongs to project"):
        repos.Campaigns.get(conn, own[0]["id"], "p2")
