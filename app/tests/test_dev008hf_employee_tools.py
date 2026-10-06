"""DEV-008-SKILLS-OPS-HOTFIX W3 — employee-owned tool execution.

Covers contract sections 4, 5, 6 and 8 for
``app.graphs.employee_tools``. The registry and the tree are injected, so no
test here touches the network: tool execution is proved against a fake
registry, while every event and every ``tool_runs`` row is proved against a
real ``TreeEmitter`` writing to a real SQLite database. Both the emitted event
order and the persisted row ids / timestamps are asserted, because the contract
is measured from persisted rows.
"""
from __future__ import annotations

import ast
import inspect
import json
import time
from datetime import datetime

import pytest

from app.database import repos
from app.database.sqlite import connect
from app.graphs import employee_tools as et
from app.services.skills.registry import load_registry
from app.services.skills.tree import TreeEmitter

PROJECT = "starter"
TURN = "turn1234567890"


# ----------------------------------------------------------------- fixtures

class FakeRegistry:
    """Stands in for ``ToolRegistryAdapter``. Records every call verbatim."""

    def __init__(self, results: dict):
        self.results = dict(results)
        self.calls: list[dict] = []

    def execute(self, conn, *, project_id, root, name, args):
        self.calls.append({
            "project_id": project_id, "root": root, "name": name,
            "args": dict(args or {}), "at": time.time(),
        })
        payload = self.results.get(name)
        if payload is None:
            return {"ok": False, "error": f"no fake result for {name}",
                    "status": "failed"}
        return dict(payload)

    @property
    def names_called(self) -> list[str]:
        return [call["name"] for call in self.calls]


class FakeTree:
    """The minimum ``TreeEmitter`` surface, recording calls in order."""

    def __init__(self, task_planned_id: int | None = 1):
        self.task_planned_id = task_planned_id
        self.events: list[tuple[str, dict]] = []

    def _record(self, event_type: str, **kwargs) -> int:
        self.events.append((event_type, kwargs))
        return len(self.events)

    def employee_queued(self, **kwargs):
        return self._record("employee_queued", **kwargs)

    def employee_started(self, **kwargs):
        return self._record("employee_started", **kwargs)

    def employee_completed(self, **kwargs):
        return self._record("employee_completed", **kwargs)

    def employee_failed(self, **kwargs):
        return self._record("employee_failed", **kwargs)

    def skill_selected(self, **kwargs):
        return self._record("skill_selected", **kwargs)

    def skill_loaded(self, **kwargs):
        return self._record("skill_loaded", **kwargs)

    def evidence_added(self, **kwargs):
        return self._record("evidence_added", **kwargs)

    def tool_event(self, **kwargs):
        return self._record(kwargs.get("event_type", "tool_event"), **kwargs)

    @property
    def types(self) -> list[str]:
        return [event_type for event_type, _ in self.events]

    def payloads(self, event_type: str) -> list[dict]:
        return [payload for kind, payload in self.events if kind == event_type]


def _db(tmp_path, name="emp.db") -> str:
    path = str(tmp_path / name)
    conn = connect(path)
    conn.close()
    return path


def _tree(db_path: str, turn_id: str = TURN) -> TreeEmitter:
    return TreeEmitter(db_path=db_path, project_id=PROJECT, conversation_id="convo-1",
                       turn_id=turn_id, thread_id=turn_id)


def _rows(db_path: str, turn_id: str = TURN) -> list[dict]:
    conn = connect(db_path)
    try:
        return [dict(row) for row in repos.ExecutionEvents.for_turn(conn, turn_id)]
    finally:
        conn.close()


def _meta(row: dict) -> dict:
    return json.loads(row.get("metadata_json") or "{}")


def _first(rows: list[dict], event_type: str) -> dict:
    for row in rows:
        if row["event_type"] == event_type:
            return row
    raise AssertionError(f"no {event_type} row in {[r['event_type'] for r in rows]}")


def _tool_runs(conn) -> list[dict]:
    from app.services.tools.registry import ensure_tool_runs

    ensure_tool_runs(conn)
    cur = conn.execute(
        "SELECT tool_run_id, project_id, tool_id, provider, started_at, "
        "completed_at, status, latency_ms, turn_id, employee_id, "
        "employee_role, evidence_count FROM tool_runs ORDER BY started_at")
    return [dict(row) for row in cur.fetchall()]


def _iso(value: str) -> datetime:
    return datetime.fromisoformat(str(value))


# ----------------------------------------------------------- fake payloads

_WEBSITE_OK = {
    "ok": True,
    "status": "PARTIALLY VERIFIED",
    "url": "https://example.com",
    "pages": [{
        "url": "https://example.com", "final_url": "https://example.com",
        "status": "fetched", "http_status": 200, "title": "Example",
        "meta_description": "", "h1": "Example", "text_head": "hello",
        "links": [{"href": "https://instagram.com/example", "text": "Instagram"}],
        "fetched_at": "2026-01-01T00:00:00+00:00",
    }],
    "audit": {
        "checklist": {
            "has_title": True, "has_meta_description": False, "has_h1": True,
            "has_viewport": True, "is_https": True, "has_cta_keywords": True,
            "has_email": True, "has_phone": False,
            "social_links": {"instagram": True, "tiktok": False},
            "pages_checked": 1,
        },
        "verdict": ["title: 'Example'"],
        "unknowns": ["meta description not observed"],
    },
    "query": "",
}

_INSTAGRAM_OK = {
    "ok": True,
    "status": "VERIFIED",
    "provider": "Apify",
    "audit": {
        "account": {"username": "example", "display_name": "Example",
                    "followers": 1234, "following": 12, "media_count": 40},
        "posts": [{"caption": "launch post"}],
        "evidence": {"source": "apify", "status": "verified",
                     "collected_at": "2026-01-01T00:00:00+00:00"},
        "unknowns": [],
        "attempts": [{"provider": "apify", "status": "SUCCEEDED"}],
        "source_chain": ["apify"],
    },
}

# The graceful provider-unavailable shape ``t_instagram_audit`` returns: ok is
# True, nothing was observed, and the frozen contract calls that a FAILED run.
_INSTAGRAM_UNAVAILABLE = {
    "ok": True,
    "status": "NOT ACCESSIBLE",
    "provider": "Apify",
    "audit": {
        "account": None, "posts": [], "insights": {},
        "evidence": {"source": "none", "status": "unavailable",
                     "collected_at": ""},
        "unknowns": ["no Instagram provider configured"],
        "attempts": [
            {"provider": "apify", "status": "skipped_unconfigured",
             "state": "NOT_CONFIGURED", "detail": "no token"},
            {"provider": "brightdata", "status": "skipped_unconfigured",
             "state": "NOT_CONFIGURED", "detail": "no token"},
        ],
        "source_chain": ["apify", "brightdata"],
        "has_configured_provider": False,
        "primary_failure": None,
    },
    "note": "Instagram research couldn't run because no Instagram provider is connected.",
}

_WEB_OK = {
    "ok": True,
    "status": "PARTIALLY VERIFIED",
    "confidence": "MEDIUM",
    "query": "competitors",
    "results": [
        {"url": "https://rival.example/a", "title": "Rival A",
         "snippet": "we sell similar"},
        {"url": "https://rival.example/b", "title": "Rival B", "snippet": "also"},
    ],
}

_WEB_EMPTY = {
    "ok": True, "status": "NOT ACCESSIBLE", "confidence": "LOW",
    "query": "competitors", "results": [],
}

_ALL_OK = {
    "website_marketing_audit": _WEBSITE_OK,
    "instagram_audit": _INSTAGRAM_OK,
    "web_search": _WEB_OK,
}


def _assignment(index: int, domains, *, task="analyse it", tools=None, role="",
                args=None) -> dict:
    return {
        "employee_id": f"{TURN}-emp{index}",
        "role": role,
        "task": task,
        "domains": list(domains),
        "skills": [],
        "tools": list(tools or []),
        "depends_on": [],
        "args": dict(args or {}),
    }


def _plan(*assignments) -> dict:
    return {
        "plan_id": "plan-1",
        "intent_class": "COMPOUND_MARKETING_TASK",
        "domains": sorted({d for a in assignments for d in a["domains"]}),
        "employees": [
            {k: v for k, v in a.items() if k != "args"} for a in assignments
        ],
    }


# ------------------------------------------------------- §5 registered tools

def test_tool_map_is_the_contract_registered_tools_only():
    assert set(et.TOOL_MAP) == {"website", "instagram", "social", "competitor"}
    assert et.DOMAINS_WITHOUT_EXTERNAL_TOOL == (
        "seo", "cro", "positioning", "experiment")
    by_domain = {domain: spec for domain, spec in et.TOOL_MAP.items()}
    assert by_domain["website"].capability == "website_marketing_audit"
    assert by_domain["website"].tool_id == "website_marketing_audit"
    assert by_domain["website"].provider == "website_fetcher"
    assert by_domain["instagram"].tool_id == "instagram_audit"
    assert by_domain["instagram"].capability == "instagram_public_profile"
    assert not hasattr(by_domain["instagram"], "provider_chain")
    assert by_domain["social"].tool_id == "instagram_audit"
    assert by_domain["competitor"].tool_id == "web_search"

    from app.services.tools import build_default_registry

    registered = set(build_default_registry().names())
    assert {spec.tool_id for spec in et.TOOL_MAP.values()} <= registered, (
        "the tool map names a tool the registry does not register")


def test_planned_tools_honours_the_plan_tool_list():
    specs = et.planned_tools(_assignment(0, ["website"], tools=[]))
    assert [s.tool_id for s in specs] == ["website_marketing_audit"]
    specs = et.planned_tools(_assignment(0, ["website", "seo"],
                                         tools=["website_marketing_audit"]))
    assert [s.tool_id for s in specs] == ["website_marketing_audit"]
    specs = et.planned_tools(_assignment(0, ["website", "seo", "cro"],
                                         tools=["website_crawl"]))
    assert specs == ()
    assert et.planned_tools(_assignment(0, ["seo", "cro", "positioning",
                                            "experiment"])) == ()


# ------------------------------------------------- §4 result shape / values

def test_result_shape_is_exactly_contract_section_4(tmp_path):
    db = _db(tmp_path)
    conn = connect(db)
    try:
        assignment = _assignment(0, ["website"], role="seo")
        result = et.run_employee(
            conn, project_id=PROJECT, assignment=assignment,
            plan=_plan(assignment), registry=FakeRegistry(_ALL_OK))
    finally:
        conn.close()

    assert tuple(result) == et.RESULT_KEYS
    assert result["status"] in et.STATUSES
    assert result["employee_id"] == f"{TURN}-emp0"
    assert result["employee_role"] == "seo"
    assert result["task"] == "analyse it"
    for entry in result["tools"]:
        assert tuple(entry) == ("tool_id", "tool_run_id", "provider",
                                "capability", "status", "duration_ms")
        assert entry["status"] in et.TOOL_STATUSES
        assert isinstance(entry["duration_ms"], int)
        assert entry["tool_run_id"]
    for item in result["evidence"]:
        assert tuple(item) == ("kind", "ref", "detail")
    for entry in result["skills"]:
        assert tuple(entry) == ("skill_id", "reason_code", "score")
    assert _iso(result["started_at"]) <= _iso(result["completed_at"])
    assert result["duration_ms"] >= 0


# ------------------------------------------------- §6 plan precedes any tool

def test_plan_precedes_any_tool_execution(tmp_path):
    """Persisted event order, persisted row ids, and persisted timestamps."""
    db = _db(tmp_path)
    tree = _tree(db)
    registry = FakeRegistry(_ALL_OK)
    assignment = _assignment(0, ["website"])
    plan = _plan(assignment)

    tree.account_manager_started(intent="compound", route="compound_marketing_task")
    tree.task_planned(tasks=[{"id": "q0", "query": "analyse it"}], employee_count=1)
    planned_wall = time.time()
    # execution_events.created_at is second-resolution, so a real delay is what
    # makes the persisted timestamp comparison strict rather than "<=".
    time.sleep(1.05)

    conn = connect(db)
    try:
        result = et.run_employee(
            conn, project_id=PROJECT, assignment=assignment, plan=plan,
            tree=tree, registry=registry)
    finally:
        conn.close()

    rows = _rows(db)
    types = [row["event_type"] for row in rows]
    assert types[0] == "account_manager_started"
    assert types[1] == "task_planned"
    assert types.index("task_planned") < types.index("employee_started")
    assert types.index("employee_started") < types.index("tool_started")
    assert types.index("tool_started") < types.index("tool_completed")
    assert types.index("tool_completed") < types.index("employee_completed")
    employee_start = _first(rows, "employee_started")
    employee_end = _first(rows, "employee_completed")
    assert _meta(employee_start)["started_at"] == result["started_at"]
    employee_span_ms = int((datetime.fromisoformat(_meta(employee_end)["completed_at"])
                            - datetime.fromisoformat(_meta(employee_start)["started_at"])
                            ).total_seconds() * 1000)
    assert abs(employee_span_ms - _meta(employee_end)["duration_ms"]) <= 25

    # The row ids are the persisted emission order.
    ids = {row["event_type"]: row["id"] for row in rows}
    assert ids["task_planned"] < ids["employee_started"] < ids["tool_started"] \
        < ids["tool_completed"] < ids["employee_completed"]

    # The persisted timestamps agree, strictly.
    planned_row = _first(rows, "task_planned")
    assert _iso(planned_row["created_at"]) < _iso(
        _first(rows, "tool_started")["created_at"])

    conn = connect(db)
    try:
        runs = _tool_runs(conn)
    finally:
        conn.close()
    assert len(runs) == 1
    run = runs[0]
    assert run["tool_run_id"] == result["tools"][0]["tool_run_id"]
    assert run["status"] == "success"
    assert run["turn_id"] == TURN
    assert run["employee_id"] == assignment["employee_id"]
    assert (run["employee_role"] or "") == result["employee_role"]
    assert run["evidence_count"] == len(result["evidence"])
    assert int((_iso(run["completed_at"]) - _iso(run["started_at"])).total_seconds() * 1000) == run["latency_ms"]
    assert _iso(planned_row["created_at"]) < _iso(run["started_at"])
    assert registry.calls[0]["at"] > planned_wall
    assert registry.names_called == ["website_marketing_audit"]


@pytest.mark.parametrize(
    ("outcomes", "expected_status", "attempts"),
    [([dict(_WEBSITE_OK)], "success", 1),
     ([{"ok": True, "status": "NOT ACCESSIBLE"}], "failed", 1),
     ([{"ok": False, "status": "failed"}, dict(_WEBSITE_OK)], "success", 2)],
)
def test_real_registry_employee_call_has_one_contextual_row_and_consistent_timing(
        tmp_path, outcomes, expected_status, attempts):
    """The actual Registry telemetry is suppressed; employee owns final row."""
    from app.graphs.adapters import ToolRegistryAdapter
    from app.services.tools.registry import Registry, ToolRecord

    db = _db(tmp_path)
    tree = _tree(db)
    tree.task_planned(tasks=[{"id": "q0", "query": "analyse it"}], employee_count=1)
    assignment = _assignment(0, ["website"])
    plan = _plan(assignment)
    raw_registry = Registry()
    raw_registry.register_tool(ToolRecord(
        tool_id="website_marketing_audit", source_type="integration",
        description="test website fetch", parameters={}, side_effect="read",
        retry_policy="once"))
    remaining = list(outcomes)
    calls = []

    def handler(conn, *, project_id, root, args):
        calls.append(args)
        value = remaining.pop(0)
        if isinstance(value, dict):
            value = dict(value)
            value.setdefault("provider", "website_fetcher")
        return value

    raw_registry.bind_handler("website_marketing_audit", handler)
    conn = connect(db)
    try:
        result = et.run_employee(
            conn, project_id=PROJECT, assignment=assignment, plan=plan,
            tree=tree, registry=ToolRegistryAdapter(raw_registry))
        runs = _tool_runs(conn)
    finally:
        conn.close()

    assert len(calls) == attempts
    assert len(runs) == 1
    row = runs[0]
    assert row["tool_run_id"] == result["tools"][0]["tool_run_id"]
    assert row["turn_id"] == TURN
    assert row["employee_id"] == assignment["employee_id"]
    assert (row["employee_role"] or "") == result["employee_role"]
    assert row["evidence_count"] == len(result["evidence"])
    assert row["status"] == expected_status
    assert int((_iso(row["completed_at"]) - _iso(row["started_at"])).total_seconds() * 1000) == row["latency_ms"]


def test_tool_is_refused_before_the_plan_exists(tmp_path):
    """The precondition is enforced here, not trusted to the caller."""
    db = _db(tmp_path)
    tree = _tree(db)
    registry = FakeRegistry(_ALL_OK)
    assignment = _assignment(0, ["website"])
    conn = connect(db)
    try:
        with pytest.raises(et.PlanRequiredError):
            et.run_employee(conn, project_id=PROJECT, assignment=assignment,
                            plan={}, tree=tree, registry=registry)
        with pytest.raises(et.PlanRequiredError):
            et.run_employee(conn, project_id=PROJECT, assignment=assignment,
                            plan=_plan(), tree=tree, registry=registry)
        # A plan that exists but has not been persisted to the event stream.
        with pytest.raises(et.PlanRequiredError):
            et.run_employee(conn, project_id=PROJECT, assignment=assignment,
                            plan=_plan(assignment), tree=FakeTree(None),
                            registry=registry)
        runs = _tool_runs(conn)
    finally:
        conn.close()

    assert registry.calls == []
    assert runs == []
    assert _rows(db) == []


# ------------------------------------------------- §6 per-employee ownership

def test_every_tool_event_carries_its_owning_employee_and_nests_under_it(tmp_path):
    db = _db(tmp_path)
    tree = _tree(db)
    registry = FakeRegistry(_ALL_OK)
    website = _assignment(0, ["website"], role="seo")
    social = _assignment(1, ["instagram"], role="content")
    plan = _plan(website, social)

    tree.account_manager_started(intent="compound", route="compound_marketing_task")
    tree.task_planned(tasks=[{"id": "q0", "query": "analyse it"}], employee_count=2)

    conn = connect(db)
    try:
        results = et.run_employees(
            conn, project_id=PROJECT, assignments=[website, social], plan=plan,
            tree=tree, registry=registry)
    finally:
        conn.close()

    rows = _rows(db)
    started = [row for row in rows if row["event_type"] == "employee_started"]
    assert len(started) == 2
    owners = {row["id"]: _meta(row)["employee_id"] for row in started}
    assert set(owners.values()) == {website["employee_id"], social["employee_id"]}

    per_employee: dict[str, list[str]] = {}
    for row in rows:
        meta = _meta(row)
        employee_id = str(meta.get("employee_id") or "")
        if not employee_id:
            continue
        per_employee.setdefault(employee_id, []).append(row["event_type"])
        if row["event_type"] in ("tool_started", "tool_completed", "tool_failed",
                                "skill_selected", "evidence_added"):
            parent = int(meta["parent_id"])
            assert owners[parent] == employee_id, (
                f"{row['event_type']} for {employee_id} is nested under "
                f"{owners.get(parent)!r}")

    for employee_id, events in per_employee.items():
        assert "employee_started" in events
        assert "employee_completed" in events
        assert [e for e in events if e.startswith("tool_")] == [
            "tool_started", "tool_completed"]

    assert [result["employee_id"] for result in results] == [
        website["employee_id"], social["employee_id"]]
    assert all(result["status"] == "completed" for result in results)
    assert [call["name"] for call in registry.calls] == [
        "website_marketing_audit", "instagram_audit"]


# ------------------------------------------- §4 tool_run_id + provider attach

def test_tool_run_id_and_provider_reach_the_result_and_the_events(tmp_path):
    db = _db(tmp_path)
    tree = _tree(db)
    assignment = _assignment(0, ["instagram", "competitor"])
    plan = _plan(assignment)
    tree.task_planned(tasks=[{"id": "q0", "query": "analyse it"}], employee_count=1)

    conn = connect(db)
    try:
        result = et.run_employee(
            conn, project_id=PROJECT, assignment=assignment, plan=plan,
            tree=tree, registry=FakeRegistry(_ALL_OK),
            skill_registry=load_registry(disabled=""))
    finally:
        conn.close()

    entries = {entry["tool_id"]: entry for entry in result["tools"]}
    assert set(entries) == {"instagram_audit", "web_search"}
    assert entries["instagram_audit"]["provider"] == "Apify"
    assert entries["instagram_audit"]["capability"] == "instagram_public_profile"
    assert entries["web_search"]["provider"] == "web_transport"
    assert entries["web_search"]["capability"] == ""

    rows = _rows(db)
    completed = [row for row in rows if row["event_type"] == "tool_completed"]
    assert len(completed) == 2
    for tool_id, entry in entries.items():
        matched = [
            _meta(row) for row in rows
            if row["event_type"] in ("tool_started", "tool_completed")
            and _meta(row)["tool_id"] == tool_id
        ]
        assert {item["tool_run_id"] for item in matched} == {entry["tool_run_id"]}
        assert all(item["employee_id"] == result["employee_id"] for item in matched)
        assert all(item["status"] in ("RUNNING", "COMPLETE") for item in matched)

    conn = connect(db)
    try:
        runs = {row["tool_run_id"]: row for row in _tool_runs(conn)}
    finally:
        conn.close()
    for entry in result["tools"]:
        row = runs[entry["tool_run_id"]]
        assert row["tool_id"] == entry["tool_id"]
        assert row["provider"] == entry["provider"]
        assert row["status"] == "success"
        assert row["project_id"] == PROJECT


# ------------------------------------------------------- skills from the router

def test_skills_come_from_the_real_router_with_reason_and_score(tmp_path):
    """The selection is the router's own output, not a list written here."""
    from app.services.skills import router as skill_router

    conn = connect(_db(tmp_path))
    skills = load_registry(disabled="")
    try:
        seo = _assignment(0, ["seo"], task="seo audit of our website", role="seo")
        competitors = _assignment(
            1, ["competitor"], task="improve our onboarding and activation",
            role="cro")
        plan = _plan(seo, competitors)
        seo_result = et.run_employee(
            conn, project_id=PROJECT, assignment=seo, plan=plan,
            registry=FakeRegistry(_ALL_OK), skill_registry=skills)
        other_result = et.run_employee(
            conn, project_id=PROJECT, assignment=competitors, plan=plan,
            registry=FakeRegistry(_ALL_OK), skill_registry=skills)
    finally:
        conn.close()

    def router_selected(task: str) -> list[tuple[str, str, float]]:
        decision = skill_router.route(skill_router.SkillRouteRequest(
            intent=task, project_state={"project_id": PROJECT}, task={},
            available_skills=skills.enabled_records()))
        return [(item.skill_id, item.reason_code, float(item.score))
                for item in decision.selected]

    assert seo_result["skills"], "the router selected nothing for an SEO audit"
    assert [
        (entry["skill_id"], entry["reason_code"], entry["score"])
        for entry in seo_result["skills"]
    ] == router_selected("seo audit of our website")
    assert [
        (entry["skill_id"], entry["reason_code"], entry["score"])
        for entry in other_result["skills"]
    ] == router_selected("improve our onboarding and activation")

    for entry in seo_result["skills"] + other_result["skills"]:
        assert entry["reason_code"] in skill_router.REASON_CODES
        assert entry["score"] > 0.0
    assert len(seo_result["skills"]) <= skill_router.MAX_SKILLS_PER_TURN
    assert {entry["skill_id"] for entry in seo_result["skills"]} != {
        entry["skill_id"] for entry in other_result["skills"]}


def test_skill_events_carry_the_router_decision(tmp_path):
    tree = FakeTree()
    conn = connect(_db(tmp_path))
    try:
        assignment = _assignment(0, ["seo"], task="seo audit of our website",
                                 role="seo")
        result = et.run_employee(
            conn, project_id=PROJECT, assignment=assignment,
            plan=_plan(assignment), tree=tree, registry=FakeRegistry(_ALL_OK),
            skill_registry=load_registry(disabled=""))
    finally:
        conn.close()

    selected = tree.payloads("skill_selected")
    assert [item["skill_id"] for item in selected] == [
        entry["skill_id"] for entry in result["skills"]]
    for item, entry in zip(selected, result["skills"]):
        assert item["reason_code"] == entry["reason_code"]
        assert item["score"] == entry["score"]
        assert item["employee_id"] == result["employee_id"]
    assert tree.payloads("skill_loaded")
    assert tree.types.index("employee_started") < tree.types.index("skill_selected")


# ------------------------------------------------------------- §8 failures

def test_failing_tool_fails_the_employee_with_a_real_failed_run(tmp_path):
    db = _db(tmp_path)
    tree = _tree(db)
    assignment = _assignment(0, ["instagram"], role="content")
    plan = _plan(assignment)
    tree.task_planned(tasks=[{"id": "q0", "query": "analyse it"}], employee_count=1)

    conn = connect(db)
    try:
        result = et.run_employee(
            conn, project_id=PROJECT, assignment=assignment, plan=plan,
            tree=tree,
            registry=FakeRegistry({"instagram_audit": _INSTAGRAM_UNAVAILABLE}),
            skill_registry=load_registry(disabled=""))
        runs = _tool_runs(conn)
    finally:
        conn.close()

    assert result["status"] == "failed"
    assert len(result["tools"]) == 1
    entry = result["tools"][0]
    assert entry["tool_id"] == "instagram_audit"
    assert entry["status"] == "failed"
    assert entry["tool_run_id"]
    assert entry["provider"] == "Apify"
    assert result["evidence"] == []
    assert result["limitations"], "a failed provider must leave a limitation"
    assert "instagram_audit" in result["limitations"][0]
    assert "No substitute source" in result["limitations"][0]

    types = [row["event_type"] for row in _rows(db)]
    assert "tool_failed" in types
    assert "tool_started" in types
    assert types[-1] == "employee_failed"
    assert "tool_completed" not in types
    assert "employee_completed" not in types

    assert len(runs) == 1
    run = runs[0]
    assert run["tool_run_id"] == entry["tool_run_id"]
    assert run["status"] == "failed"
    assert run["tool_id"] == "instagram_audit"
    assert run["provider"] == "Apify"
    assert _iso(run["started_at"]) <= _iso(run["completed_at"])

    # The failed run stays visible in the plan; it is not removed or replaced.
    assert [entry["employee_id"] for entry in plan["employees"]] == [
        assignment["employee_id"]]


def test_turn_level_errors_are_never_written_from_a_branch():
    """``state["errors"]`` is the turn-level fatal signal; a branch must not touch it."""
    source = inspect.getsource(et)
    tree = ast.parse(source)
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "errors" not in literals
    assert "NO_PROJECT_SCOPE" not in literals
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            key = node.slice
            if isinstance(key, ast.Constant) and key.value == "errors":
                raise AssertionError("the module reads or writes state['errors']")
        if isinstance(node, ast.Attribute) and node.attr in ("append", "extend"):
            value = node.value
            if isinstance(value, ast.Name) and value.id.endswith("errors"):
                raise AssertionError("the module appends to a turn-level error list")
    parameters = set(inspect.signature(et.run_employee).parameters)
    assert "errors" not in parameters
    assert "state" not in parameters
    assert "errors" not in et.RESULT_KEYS


def test_a_failed_employee_does_not_cancel_its_siblings(tmp_path):
    db = _db(tmp_path)
    tree = _tree(db)
    registry = FakeRegistry({
        "website_marketing_audit": _WEBSITE_OK,
        "instagram_audit": _INSTAGRAM_UNAVAILABLE,
        "web_search": _WEB_OK,
    })
    website = _assignment(0, ["website"], role="seo")
    social = _assignment(1, ["instagram"], role="content")
    competitor = _assignment(2, ["competitor"], role="research")
    plan = _plan(website, social, competitor)
    tree.task_planned(tasks=[{"id": "q0", "query": "analyse it"}], employee_count=3)

    conn = connect(db)
    try:
        results = et.run_employees(
            conn, project_id=PROJECT,
            assignments=[website, social, competitor], plan=plan, tree=tree,
            registry=registry, skill_registry=load_registry(disabled=""))
        runs = _tool_runs(conn)
    finally:
        conn.close()

    assert [result["status"] for result in results] == [
        "completed", "failed", "completed"]
    assert [result["employee_id"] for result in results] == [
        website["employee_id"], social["employee_id"], competitor["employee_id"]]
    assert results[0]["evidence"] and results[2]["evidence"]
    assert results[1]["evidence"] == []
    assert registry.names_called == [
        "website_marketing_audit", "instagram_audit", "web_search"]
    assert sorted(run["status"] for run in runs) == ["failed", "success", "success"]

    rows = _rows(db)
    by_employee: dict[str, list[str]] = {}
    for row in rows:
        meta = _meta(row)
        employee_id = str(meta.get("employee_id") or "")
        if employee_id:
            by_employee.setdefault(employee_id, []).append(row["event_type"])
    assert by_employee[website["employee_id"]][-1] == "employee_completed"
    assert by_employee[social["employee_id"]][-1] == "employee_failed"
    assert by_employee[competitor["employee_id"]][-1] == "employee_completed"
    assert len(plan["employees"]) == 3


def test_empty_web_search_is_a_failed_run_not_a_silent_pass(tmp_path):
    conn = connect(_db(tmp_path))
    try:
        assignment = _assignment(0, ["competitor"], role="research")
        result = et.run_employee(
            conn, project_id=PROJECT, assignment=assignment,
            plan=_plan(assignment), tree=FakeTree(),
            registry=FakeRegistry({"web_search": _WEB_EMPTY}),
            skill_registry=load_registry(disabled=""))
    finally:
        conn.close()
    assert result["status"] == "failed"
    assert result["tools"][0]["status"] == "failed"
    assert result["evidence"] == []
    assert result["limitations"]


def test_success_status_with_empty_web_results_is_still_unavailable(tmp_path):
    conn = connect(_db(tmp_path))
    try:
        assignment = _assignment(0, ["competitor"], role="research")
        result = et.run_employee(
            conn, project_id=PROJECT, assignment=assignment,
            plan=_plan(assignment), tree=FakeTree(),
            registry=FakeRegistry({"web_search": {
                "ok": True, "status": "success", "results": [],
            }}),
            skill_registry=load_registry(disabled=""))
    finally:
        conn.close()
    assert result["status"] == "failed"
    assert result["tools"][0]["status"] == "failed"
    assert result["evidence"] == []
    assert result["findings"] == []
    assert any("no usable results" in item for item in result["limitations"])


def test_raising_registry_is_contained_and_reported(tmp_path):
    class Boom:
        def execute(self, conn, *, project_id, root, name, args):
            raise RuntimeError("provider socket closed")

    conn = connect(_db(tmp_path))
    try:
        assignment = _assignment(0, ["website"], role="seo")
        result = et.run_employee(
            conn, project_id=PROJECT, assignment=assignment,
            plan=_plan(assignment), tree=FakeTree(), registry=Boom(),
            skill_registry=load_registry(disabled=""))
        runs = _tool_runs(conn)
    finally:
        conn.close()
    assert result["status"] == "failed"
    assert result["limitations"]
    assert runs and runs[0]["status"] == "failed"
    assert result["tools"][0]["tool_run_id"] == runs[0]["tool_run_id"]


# -------------------------------------------- domains needing no external tool

@pytest.mark.parametrize("domain", ["seo", "cro", "positioning", "experiment"])
def test_domains_without_an_external_tool_complete_with_no_tools(domain, tmp_path):
    conn = connect(_db(tmp_path))
    try:
        assignment = _assignment(0, [domain], role="seo")
        tree = FakeTree()
        registry = FakeRegistry(_ALL_OK)
        result = et.run_employee(
            conn, project_id=PROJECT, assignment=assignment,
            plan=_plan(assignment), tree=tree, registry=registry,
            skill_registry=load_registry(disabled=""))
    finally:
        conn.close()

    assert result["tools"] == []
    assert result["status"] == "completed"
    assert registry.calls == []
    assert tree.types[-1] == "employee_completed"
    assert not any(t.startswith("tool_") for t in tree.types)
    assert result["findings"], "the applied playbook should be recorded"
    assert not any("No substitute source" in item for item in result["limitations"])


def test_unknown_domain_is_a_limitation_not_an_invented_provider(tmp_path):
    conn = connect(_db(tmp_path))
    try:
        assignment = _assignment(0, ["telepathy"], role="seo")
        registry = FakeRegistry(_ALL_OK)
        result = et.run_employee(
            conn, project_id=PROJECT, assignment=assignment,
            plan=_plan(assignment), tree=FakeTree(), registry=registry,
            skill_registry=load_registry(disabled=""))
    finally:
        conn.close()
    assert registry.calls == []
    assert result["tools"] == []
    assert any("telepathy" in item for item in result["limitations"])


def test_plan_tool_outside_the_map_is_reported_not_run(tmp_path):
    conn = connect(_db(tmp_path))
    try:
        assignment = _assignment(0, ["website"], tools=["brand_watchdog"],
                                 role="seo")
        registry = FakeRegistry(_ALL_OK)
        result = et.run_employee(
            conn, project_id=PROJECT, assignment=assignment,
            plan=_plan(assignment), tree=FakeTree(), registry=registry,
            skill_registry=load_registry(disabled=""))
    finally:
        conn.close()
    assert registry.calls == []
    assert result["tools"] == []
    assert result["status"] == "completed"
    assert any("brand_watchdog" in item for item in result["limitations"])
