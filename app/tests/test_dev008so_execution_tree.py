"""DEV-008-SKILLS-OPS W4 — execution tree + graph wiring.

Owns the TreeEmitter contract (§3.5), the live-streaming proof (events present
WHILE the turn runs, identical replay — no post-hoc animation), the 4000-char
round-trip bound (§1.3.6), the FAILURE and APPROVAL states, the 7-status
producer table, the no-chain-of-thought DB/SSE proof, and the measured
concurrency statement (employee bodies vs turns._POOL).

Timeout discipline: this file never runs the full suite; it uses tmp DBs and
direct graph.invoke with a deterministic complete_fn.
"""
from __future__ import annotations

import inspect
import json
import re
import threading
import time

from app.contracts.events import (
    LEGACY_EVENT_TYPES,
    TREE_STATUSES,
    GraphExecutionEvent,
    _forbidden_meta_key,
)
from app import deps
from app.database import repos
from app.database.sqlite import connect
from app.services.skills.registry import load_registry
from app.services.skills.tree import TreeEmitter

import pytest

# The router may only inject enabled_records(); QA MEDIUM-1 shipped a wire
# regression for exactly one of those ids (`ai-seo`). The parametrisation is
# the real registry set so a future re-pin is automatically covered.
_ENABLED_SKILL_IDS = tuple(
    r.skill_id for r in load_registry(disabled="").enabled_records())
assert len(_ENABLED_SKILL_IDS) == 50, _ENABLED_SKILL_IDS


TREE_TYPES_15 = (
    "account_manager_started", "task_planned",
    "employee_queued", "employee_started", "employee_completed", "employee_failed",
    "skill_selected", "skill_loaded",
    "tool_started", "tool_completed", "tool_failed",
    "evidence_added", "synthesis_started", "synthesis_completed",
    "approval_required",
)

INTENT = "how do I improve my SEO audit readiness"


def _db(tmp_path, name="tree.db"):
    path = str(tmp_path / name)
    conn = connect(path)
    conn.close()
    return path


def _tree(db_path, turn_id="turn1234567890"):
    return TreeEmitter(
        db_path=db_path, project_id="starter", conversation_id="convo-1",
        turn_id=turn_id, thread_id=turn_id)


def _rows(db_path, turn_id):
    conn = connect(db_path)
    try:
        return repos.ExecutionEvents.for_turn(conn, turn_id)
    finally:
        conn.close()


def _meta(row):
    return json.loads(row.get("metadata_json") or "{}")


def _graph(**kwargs):
    from app.graphs.account_manager_graph import build_account_manager_graph
    kwargs.setdefault("complete_fn", lambda **kw: "deterministic test answer")
    kwargs.setdefault("known_projects", {"starter"})
    return build_account_manager_graph(**kwargs)


def _initial(**kwargs):
    from app.graphs.state import initial_state
    base = dict(project_id="starter", conversation_id="convo-1",
                turn_id="turn1234567890",
                user_request=kwargs.pop("user_request", INTENT))
    base.update(kwargs)
    return initial_state(**base)


# ---------------------------------------------------------------- interface

def test_tree_emitter_interface_matches_frozen_contract():
    expected = {
        "account_manager_started": ("intent", "route"),
        "task_planned": ("tasks", "employee_count"),
        "employee_queued": ("employee_id", "role", "task"),
        "employee_started": ("employee_id", "role", "task"),
        "employee_completed": ("employee_id", "role", "duration_ms"),
        "employee_failed": ("employee_id", "role", "exc", "duration_ms"),
        "skill_selected": ("skill_id", "reason_code", "score", "employee_id"),
        "skill_loaded": ("record", "employee_id"),
        "tool_event": ("event_type", "tool_id", "tool_run_id", "employee_id",
                       "status", "duration_ms"),
        "evidence_added": ("count", "kind", "employee_id"),
        "synthesis": ("phase", "duration_ms"),
        "approval_required": ("approval_id", "action_class", "title"),
    }
    assert set(expected) <= set(dir(TreeEmitter)), "frozen §3.5 methods missing"
    for name, params in expected.items():
        sig = inspect.signature(getattr(TreeEmitter, name))
        kinds = [p for p in sig.parameters if p != "self"]
        for required in params:
            assert required in kinds, f"{name} missing kwarg {required}"
    sig = inspect.signature(TreeEmitter.__init__)
    for required in ("db_path", "project_id", "conversation_id",
                     "turn_id", "thread_id"):
        assert required in sig.parameters, f"__init__ missing {required}"


def test_all_15_tree_types_are_registered_wire_types():
    for event_type in TREE_TYPES_15:
        assert event_type in LEGACY_EVENT_TYPES, event_type


# --------------------------- QA MEDIUM-1: every skill id survives the SSE wire

@pytest.mark.parametrize("skill_id", _ENABLED_SKILL_IDS)
def test_every_registered_skill_id_round_trips_visible_through_sse(tmp_path,
                                                                   skill_id):
    """One parametrised case over the REAL enabled_records() ids.

    QA MEDIUM-1 caught exactly one of the 50 pinned ids — `ai-seo` — being
    redacted to `Using [REDACTED]` on the wire by the env-symbol filter
    (`ai-seo` canonicalises to AI_SEO). This proves the fix for all 50: a real
    `skill_selected` event for every registered skill round-trips through
    GraphExecutionEvent -> to_row -> from_row -> to_sse with its real name
    visible on the wire.
    """
    db_path = _db(tmp_path, f"skillsse-{re.sub('[^a-z0-9]', '-', skill_id)}.db")
    tree = _tree(db_path)
    tree.skill_selected(skill_id=skill_id, reason_code="qa-remediation",
                        score=3.0)
    rows = _rows(db_path, tree.turn_id)
    selected = [r for r in rows if r["event_type"] == "skill_selected"]
    assert len(selected) == 1, selected
    row = selected[0]
    event = GraphExecutionEvent.from_row(row)
    sse = event.to_sse(id=row["id"])
    assert sse["event"] == "skill_selected"
    stored = json.loads(row.get("metadata_json") or "{}")
    assert stored["skill_id"] == skill_id, "real value is persisted"
    assert sse["data"]["label"] == f"Using {skill_id}", (
        f"{skill_id} lost its name on the SSE wire: {sse['data']['label']!r}")
    assert "[REDACTED]" not in sse["data"]["label"]
    assert sse["data"]["meta"]["skill_id"] == skill_id
    assert sse["data"]["meta"]["skill_name"] == skill_id


# ------------------------------------------------------- deterministic turn

def test_deterministic_turn_yields_planned_triple_and_zero_orphans(tmp_path):
    db_path = _db(tmp_path)
    turn_id = "turn1234567890"
    tree = _tree(db_path, turn_id)
    graph = _graph()
    out = graph.invoke(
        _initial(turn_id=turn_id),
        config={"configurable": {"thread_id": "w4-e2e-1", "tree": tree}})
    assert (out.get("final_answer") or "").strip(), "turn must answer"
    rows = _rows(db_path, turn_id)
    by_type: dict[str, list] = {}
    for row in rows:
        by_type.setdefault(row["event_type"], []).append(row)
    print(f"\nmeasured: {len(rows)} events; "
          f"task_planned={len(by_type.get('task_planned', []))}; "
          f"queued={len(by_type.get('employee_queued', []))}; "
          f"started={len(by_type.get('employee_started', []))}; "
          f"completed={len(by_type.get('employee_completed', []))}; "
          f"skill_selected={len(by_type.get('skill_selected', []))}")
    assert len(by_type.get("task_planned", [])) >= 1
    queued = {(_meta(r).get("employee_id")) for r in by_type.get("employee_queued", [])}
    started = {(_meta(r).get("employee_id")) for r in by_type.get("employee_started", [])}
    completed = {(_meta(r).get("employee_id")) for r in by_type.get("employee_completed", [])}
    triple = queued & started & completed
    assert len(triple) >= 1, f"need >=1 complete queued/started/completed triple: {queued=} {started=} {completed=}"
    ids = {r["id"] for r in rows}
    orphans = []
    for row in rows:
        parent = (_meta(row).get("parent_id") or "")
        if parent in ("", None):
            continue
        try:
            parent_int = int(parent)
        except (TypeError, ValueError):
            orphans.append((row["id"], row["event_type"], parent))
            continue
        if parent_int not in ids:
            orphans.append((row["id"], row["event_type"], parent))
    assert orphans == [], f"orphan parent_id links: {orphans}"
    # every non-root parent belongs to the same turn (no cross-turn grafts)
    for row in rows:
        meta = _meta(row)
        if meta.get("turn_id"):
            assert meta["turn_id"] == turn_id


# ------------------------------------------------- live streaming, no fake

def test_tree_events_present_during_run_and_replay_is_identical(tmp_path):
    db_path = _db(tmp_path)
    turn_id = "livedturn12345678"

    def slow_complete(**kw):
        time.sleep(1.2)
        return "slow deterministic answer"

    graph = _graph(complete_fn=slow_complete)
    tree = _tree(db_path, turn_id)
    state = _initial(turn_id=turn_id)
    outcome: dict = {}

    def _run():
        try:
            outcome["out"] = graph.invoke(
                state, config={"configurable": {"thread_id": "w4-live-1",
                                                "tree": tree}})
        except Exception as exc:  # never fail the test thread silently
            outcome["error"] = exc

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    observed_during_run: list[int] = []
    deadline = time.time() + 15
    while worker.is_alive() and time.time() < deadline:
        rows = _rows(db_path, turn_id)
        if rows:
            observed_during_run.append(len(rows))
        time.sleep(0.05)
    worker.join(timeout=20)
    assert not worker.is_alive(), "turn thread must finish"
    assert "error" not in outcome, f"invoke raised: {outcome.get('error')}"
    assert observed_during_run, (
        "NO FAKE STREAMING violated: zero tree events were visible in the "
        "database while the turn was still running")
    print(f"\nmeasured: {len(observed_during_run)} live polls saw events "
          f"(first={observed_during_run[0]}, last={observed_during_run[-1]})")
    live_ids = [r["id"] for r in _rows(db_path, turn_id)]
    assert live_ids, "expected persisted tree events after the run"
    # after= / Last-Event-ID replay returns the identical id sequence
    replay_ids = [r["id"] for r in _rows(db_path, turn_id)]
    assert replay_ids == live_ids, "replay must return the identical id sequence"
    conn = connect(db_path)
    try:
        tail = repos.ExecutionEvents.for_turn(
            conn, turn_id, after_id=live_ids[-1])
        assert tail == [], "after=<last> must return only newer rows"
        mid = repos.ExecutionEvents.for_turn(
            conn, turn_id, after_id=live_ids[0])
        assert [r["id"] for r in mid] == live_ids[1:], "after= must return the tail"
    finally:
        conn.close()


# ------------------------------------------------------- 4000-char bound

def _max_tree_rows(db_path, turn_id):
    tree = _tree(db_path, turn_id)
    long_intent = "w" * 160
    tasks = [{"id": f"q{i}", "query": "v" * 160} for i in range(4)]
    tree.account_manager_started(intent=long_intent, route="knowledge")
    tree.task_planned(tasks=tasks, employee_count=4)
    tree.employee_queued(employee_id="emp-12345678-00", role="paid_media",
                         task=tasks[0])
    started_id = tree.employee_started(employee_id="emp-12345678-00",
                                       role="paid_media", task=tasks[0])
    assert started_id is not None
    tree.employee_completed(employee_id="emp-12345678-00", role="paid_media",
                            duration_ms=10 ** 9)
    try:
        raise RuntimeError("u" * 160)
    except RuntimeError as exc:
        tree.employee_failed(employee_id="emp-12345678-01", role="content",
                             exc=exc, duration_ms=10 ** 9)
    tree.skill_selected(skill_id="k" * 64, reason_code="r" * 160,
                        score=3.0, employee_id="emp-12345678-00")
    tree.skill_loaded(
        record={"skill_id": "s" * 64, "name": "n" * 64,
                "version": "9.9.9", "category": "c" * 64},
        employee_id="emp-12345678-00")
    tree.tool_event(event_type="tool_started", tool_id="t" * 128,
                    tool_run_id="r" * 128, employee_id="emp-12345678-00",
                    status="RETRYING")
    tree.tool_event(event_type="tool_completed", tool_id="t" * 128,
                    tool_run_id="r" * 128, employee_id="emp-12345678-00",
                    status="COMPLETE", duration_ms=10 ** 9)
    tree.tool_event(event_type="tool_failed", tool_id="t" * 128,
                    tool_run_id="r" * 128, employee_id="emp-12345678-00",
                    status="FAILED")
    tree.evidence_added(count=10 ** 6, kind="k" * 64,
                        employee_id="emp-12345678-00")
    tree.synthesis(phase="started")
    tree.synthesis(phase="completed", duration_ms=10 ** 9)
    tree.approval_required(approval_id="a" * 128, action_class="yellow",
                           title="t" * 160)


def test_maximum_shape_metadata_round_trips_all_15_types(tmp_path):
    db_path = _db(tmp_path, "max.db")
    turn_id = "maxturn1234567890"
    _max_tree_rows(db_path, turn_id)
    rows = _rows(db_path, turn_id)
    seen = {r["event_type"] for r in rows}
    employee_start = next(r for r in rows if r["event_type"] == "employee_started")
    started_at = json.loads(employee_start["metadata_json"])["started_at"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}\+00:00", started_at)
    for event_type in TREE_TYPES_15:
        assert event_type in seen, f"missing max-shape row for {event_type}"
    for row in rows:
        raw_json = row.get("metadata_json") or "{}"
        assert len(raw_json) <= 4000, (
            f"{row['event_type']} hit the truncation bound: {len(raw_json)}")
        stored = json.loads(raw_json)
        back = GraphExecutionEvent.from_row(row)
        assert back.metadata == stored, (
            f"{row['event_type']} did not round-trip exactly")
        sse = back.to_sse(id=row["id"])
        assert sse["data"]["meta"] == stored, (
            f"{row['event_type']} SSE meta differs from the row")
    print(f"\nmeasured: {len(rows)} max-shape rows, all round-trip exactly, "
          f"max metadata_json bytes={max(len(r.get('metadata_json') or '') for r in rows)}")


# ------------------------------------------------- no chain-of-thought

_POISON_TOP = {
    "reasoning": "chain of thought here",
    "reasoning_trace": "trace",
    "thoughts": "thoughts",
    "scratchpad": "scratch",
    "reasoning_steps": "steps",
    "thought_chain": "chain",
    "cot_chain": "cot",
    "raw_model_output": "raw",
    "system_prompt": "you are a helpful assistant",
    "prompt": "prompt text",
    "traceback": "Traceback (most recent call last): File app/x.py",
    "llm_system": "AI_RUNTIME=langgraph",
}


def _poisoned_metadata():
    meta = dict(_POISON_TOP)
    meta["nested_a"] = {"reasoning": "x", "ok": "keep-a"}
    meta["nested_b"] = {"scratchpad": "y", "ok": "keep-b"}
    meta["nested_c"] = {"cot_notes": "z", "ok": "keep-c"}
    meta["list_a"] = ["keep-1", "Bearer abc.def.ghi", "sk-xyz12345678"]
    meta["list_b"] = ["keep-2", "Traceback (most recent call last): boom"]
    meta["exc"] = RuntimeError("provider worker failed: timeout")
    meta["status"] = "COMPLETE"
    meta["parent_id"] = ""
    return meta


def _walk_keys(value, into):
    if isinstance(value, dict):
        for key, item in value.items():
            into.append(key)
            _walk_keys(item, into)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _walk_keys(item, into)


def test_poisoned_metadata_clean_on_db_and_sse_paths(tmp_path):
    db_path = _db(tmp_path, "poison.db")
    event = GraphExecutionEvent(
        project_id="starter", conversation_id="convo-1", turn_id="poison-1",
        event_type="task_planned", label="Plan ready", detail="2 task(s) planned",
        metadata=_poisoned_metadata())
    row = event.to_row()
    stored = json.loads(row["metadata_json"])
    keys: list = []
    _walk_keys(stored, keys)
    assert not [k for k in keys if _forbidden_meta_key(k)], (
        f"forbidden keys reached the row: {keys}")
    conn = connect(db_path)
    try:
        row_id = repos.ExecutionEvents.insert(conn, {
            "project_id": "starter", "conversation_id": "convo-1",
            "turn_id": "poison-1", "job_id": "",
            "event_type": "task_planned", "label": "Plan ready",
            "detail": "2 task(s) planned",
            "metadata_json": row["metadata_json"], "created_at": ""})
        back = GraphExecutionEvent.from_row(
            repos.ExecutionEvents.for_turn(conn, "poison-1")[0])
    finally:
        conn.close()
    keys = []
    _walk_keys(back.metadata, keys)
    assert not [k for k in keys if _forbidden_meta_key(k)]
    sse = back.to_sse(id=row_id)
    keys = []
    _walk_keys(sse["data"]["meta"], keys)
    assert not [k for k in keys if _forbidden_meta_key(k)]
    blob = json.dumps(sse["data"], ensure_ascii=False)
    for needle in ("Bearer ", "chain of thought", "you are a helpful",
                   "Traceback", "AI_RUNTIME", "sk-xyz"):
        assert needle not in blob, f"forbidden value substring leaked: {needle}"
    assert back.metadata.get("status") == "COMPLETE", "safe keys must survive"


# ---------------------------------------------------------------- failure

def test_failure_state_failed_sibling_completes_turn_completes(tmp_path):
    db_path = _db(tmp_path, "fail.db")
    turn_id = "failturn12345678"

    def boom(task, role):
        if role == "seo":
            raise RuntimeError(
                "C:\\secret\\boom.py line 42: ValueError: sim failure "
                "Traceback (most recent call last): boom")
        return {"sources": ["ok"]}

    graph = _graph(employee_fn=boom)
    tree = _tree(db_path, turn_id)
    out = graph.invoke(
        _initial(turn_id=turn_id),
        config={"configurable": {"thread_id": "w4-fail-1", "tree": tree}})
    assert (out.get("final_answer") or "").strip(), "turn must still answer"
    assert out.get("errors", []) == [], "one bad employee must not poison state"
    rows = _rows(db_path, turn_id)
    by_type: dict[str, list] = {}
    for row in rows:
        by_type.setdefault(row["event_type"], []).append(row)
    failed = by_type.get("employee_failed", [])
    assert len(failed) == 1, f"expected exactly one employee_failed: {len(failed)}"
    meta = _meta(failed[0])
    assert meta.get("status") == "FAILED"
    assert meta.get("employee_role") == "seo"
    detail = failed[0].get("detail") or ""
    assert detail == "The request could not be completed.", detail
    for needle in ("Traceback", ".py", "ValueError", "secret", "boom"):
        assert needle not in detail, f"diagnostic leaked into detail: {needle}"
    assert meta.get("duration_ms", 0) >= 0
    assert meta.get("completed_at"), "FAILED must carry completed_at"
    completed_roles = {_meta(r).get("employee_role")
                       for r in by_type.get("employee_completed", [])}
    assert "research" in completed_roles, "siblings must still complete"
    print(f"\nmeasured: failed={meta.get('employee_id')} "
          f"completed={sorted(completed_roles)} errors={out.get('errors')}")


# ---------------------------------------------------------------- approval

def test_approval_state_interrupt_emits_waiting_and_resume_continues(tmp_path, monkeypatch):
    db_path = _db(tmp_path, "appr.db")
    conn = connect(db_path)
    repos.Companies.upsert(conn, {
        "id": "co1", "name": "Approval Test", "website": "", "created_at": "now",
    })
    repos.Projects.upsert(conn, {
        "id": "starter", "name": "Approval Test", "website": "",
        "goal": "Review approval flow", "status": "active", "settings_json": "{}",
        "created_at": "now", "updated_at": "now",
    })
    repos.Conversations.upsert(conn, {
        "id": "convo-1", "company_id": "co1", "project_id": "starter",
        "title": "Approval Test", "created_at": "now", "updated_at": "now",
    })
    repos.Turns.upsert(conn, {
        "id": "apprturn12345678", "conversation_id": "convo-1",
        "project_id": "starter", "status": "running",
        "created_at": "now", "updated_at": "now",
    })
    conn.close()
    # The graph's default connection factory reads deps.DB_PATH. Keep this
    # workflow fixture isolated instead of persisting test proposals in the
    # developer's repository database.
    monkeypatch.setattr(deps, "DB_PATH", db_path)
    turn_id = "apprturn12345678"
    graph = _graph()
    tree = _tree(db_path, turn_id)
    cfg = {"configurable": {"thread_id": "w4-appr-1", "tree": tree}}
    out = graph.invoke(
        _initial(turn_id=turn_id,
                 user_request="please approve and send the launch campaign"),
        config=cfg)
    assert out is not None and "__interrupt__" in out, (
        "a yellow action must reach approval_gate interrupt()")
    payload = out["__interrupt__"][0].value
    assert payload.get("action_class") == "yellow"
    rows = _rows(db_path, turn_id)
    approvals = [r for r in rows if r["event_type"] == "approval_required"]
    assert len(approvals) >= 1
    meta = _meta(approvals[0])
    assert meta.get("status") == "WAITING_FOR_APPROVAL"
    assert meta.get("approval_id"), "approval_required needs approval_id"
    assert meta.get("action_class") == "yellow"
    planned = [r["id"] for r in rows if r["event_type"] == "task_planned"]
    assert planned and str(meta.get("parent_id")) == str(planned[0])
    # the side effect has NOT happened: nothing approved yet
    assert "approved" not in (out.get("approval_state") or {}), (
        "approval_state must not carry a decision before the resume Command")
    synth_before = len([r for r in rows
                        if r["event_type"] == "synthesis_completed"])
    from langgraph.types import Command
    out2 = graph.invoke(Command(resume={"approved": True}), config=cfg)
    assert (out2.get("approval_state") or {}).get("approved") is True
    rows2 = _rows(db_path, turn_id)
    synth_after = len([r for r in rows2
                       if r["event_type"] == "synthesis_completed"])
    assert synth_after == synth_before, (
        "resume must not repeat the synthesis side effect")
    print(f"\napproval_id={meta.get('approval_id')} "
          f"synthesis_completed stable at {synth_after}")


# ------------------------------------------------------------- 7 statuses

def test_all_7_tree_statuses_have_producer_or_stated_reason(tmp_path):
    db_path = _db(tmp_path, "status.db")
    turn_id = "statusturn1234567"
    tree = _tree(db_path, turn_id)
    tree.account_manager_started(intent="probe", route="knowledge")
    tree.task_planned(tasks=[{"id": "q0", "query": "probe"}], employee_count=1)
    tree.employee_queued(employee_id="emp-x-00", role="research",
                         task={"id": "q0", "query": "probe"})
    tree.employee_started(employee_id="emp-x-00", role="research",
                          task={"id": "q0", "query": "probe"})
    tree.tool_event(event_type="tool_started", tool_id="probe_tool",
                    tool_run_id="run-1", employee_id="emp-x-00", status="RUNNING")
    tree.tool_event(event_type="tool_started", tool_id="probe_tool",
                    tool_run_id="run-1", employee_id="emp-x-00", status="RETRYING")
    tree.tool_event(event_type="tool_completed", tool_id="probe_tool",
                    tool_run_id="run-1", employee_id="emp-x-00",
                    status="COMPLETE", duration_ms=3)
    tree.tool_event(event_type="tool_failed", tool_id="probe_tool",
                    tool_run_id="run-2", employee_id="emp-x-00", status="FAILED")
    tree.skill_selected(skill_id="probe-skill",
                        reason_code="deterministic-exact-match", score=3.0)
    from app.services.skills.registry import load_registry
    record = load_registry(disabled="").get("cro")
    assert record is not None
    tree.skill_loaded(record=record)
    tree.evidence_added(count=2, kind="probe")
    tree.employee_completed(employee_id="emp-x-00", role="research",
                            duration_ms=5)
    try:
        raise RuntimeError("probe")
    except RuntimeError as exc:
        tree.employee_failed(employee_id="emp-x-01", role="seo", exc=exc,
                             duration_ms=5)
    tree.synthesis(phase="started")
    tree.synthesis(phase="completed", duration_ms=5)
    tree.approval_required(approval_id="appr-1", action_class="yellow",
                           title="probe approval")
    rows = _rows(db_path, turn_id)
    produced = {_meta(r).get("status") for r in rows}
    for expected in ("QUEUED", "RUNNING", "COMPLETE", "FAILED", "RETRYING",
                     "WAITING_FOR_APPROVAL"):
        assert expected in produced, f"{expected} has no observed producer"
    assert "CANCELLED" not in produced, (
        "CANCELLED must have no producer this run: plan §1.3.4/§8.2 — no "
        "user-facing cancel surface exists, and employee branches never write "
        "state['errors'], so no Send branch is ever aborted. The enum member "
        "remains mandatory in the UI render map (W7), which is asserted by "
        "test_dev008so_frontend_wiring.py, not here.")
    assert "CANCELLED" in TREE_STATUSES
    retrying = [r for r in rows if _meta(r).get("status") == "RETRYING"]
    assert len(retrying) == 1 and retrying[0]["event_type"] == "tool_started"


# ------------------------------------------------------- concurrency truth

def test_employee_bodies_overlap_on_langgraph_threads_not_the_pool(tmp_path):
    import app.services.turns as turnsvc

    db_path = _db(tmp_path, "conc.db")
    turn_id = "concturn1234567890"
    spans: dict = {}
    idents: dict = {}

    def recorder(task, role):
        idents[role] = threading.get_ident()
        start = time.perf_counter()
        time.sleep(0.6)
        spans[role] = (start, time.perf_counter())
        return {}

    graph = _graph(employee_fn=recorder)
    tree = _tree(db_path, turn_id)
    invoker = threading.get_ident()
    graph.invoke(
        _initial(turn_id=turn_id),
        config={"configurable": {"thread_id": "w4-conc-1", "tree": tree}})
    assert len(spans) >= 2, f"need >=2 employee bodies, saw {sorted(spans)}"
    roles = sorted(spans)
    overlap = max(0.0, min(spans[r][1] for r in roles)
                  - max(spans[r][0] for r in roles))
    print(f"\nmeasured: roles={roles} overlap_s={overlap:.3f} "
          f"invoker={invoker} employee_threads={sorted(set(idents.values()))} "
          f"pool_max_workers={turnsvc._POOL._max_workers}")
    assert overlap > 0, (
        "NO FAKE PARALLELISM violated: employee bodies did not overlap in time")
    assert all(t != invoker for t in idents.values()), (
        "employees run on LangGraph-managed Send-branch threads, not the "
        "invoking turn thread")
    import inspect as _inspect
    import app.graphs.account_manager_graph as amg
    source = _inspect.getsource(amg.build_account_manager_graph)
    for needle in ("_POOL.submit", "ThreadPoolExecutor", "from app.services.turns import"):
        assert needle not in source, (
            f"graph code must never submit to turns._POOL ({needle})")
    assert turnsvc._POOL._max_workers == 4
    rows = _rows(db_path, turn_id)
    started = [r for r in rows if r["event_type"] == "employee_started"]
    completed = [r for r in rows if r["event_type"] == "employee_completed"]
    assert len(started) >= 2 and len(completed) >= 2


# ------------------------------------------------------- loop-event bridge

def test_on_loop_event_delegates_tree_types_and_keeps_generic_path(tmp_path):
    from app.services import turns as turnsvc

    db_path = _db(tmp_path, "bridge.db")
    turn_id = "bridgeturn1234567"
    tree = _tree(db_path, turn_id)
    turnsvc._on_loop_event(
        db_path, "starter", "convo-1", turn_id,
        "account_manager_started", {"intent": "bridge probe", "route": "r"},
        tree=tree)
    turnsvc._on_loop_event(
        db_path, "starter", "convo-1", turn_id,
        "task_planned", {"tasks": [{"id": "q0", "query": "q"}], "employee_count": 1},
        tree=tree)
    turnsvc._on_loop_event(
        db_path, "starter", "convo-1", turn_id,
        "context_started", {}, tree=tree)
    rows = _rows(db_path, turn_id)
    types = [r["event_type"] for r in rows]
    assert "account_manager_started" in types
    assert "task_planned" in types
    assert "context_started" in types, "generic branches must keep working"
    planned = next(r["id"] for r in rows if r["event_type"] == "task_planned")
    assert tree.task_planned_id == planned
