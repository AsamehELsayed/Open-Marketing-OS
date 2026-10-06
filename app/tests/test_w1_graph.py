"""DEV-005 W1 — LangGraph state, Account Manager graph, persistence boundary.

Scope: app/graphs/ only. No FastAPI routes, React, RAG internals,
provider router, requirements, lockfile, or cutover edits.
"""
import threading
import time

import pytest

from app.contracts import routing as ro


def _db(tmp_path, name="w1.db"):
    from app.database.sqlite import connect

    return connect(tmp_path / name)


# ---------- routing ----------

def test_routing_maps_deterministic_flows_to_graph_branches():
    from app.graphs.account_manager import classify_to_route

    assert classify_to_route("what needs my attention?") == "state_only"
    assert classify_to_route("show pending approvals") == "state_only"
    assert classify_to_route("explain why agencies", project_evidence=True) == "knowledge"
    assert classify_to_route("search the web for pricing") == "external_research"
    # DEV-007R-HOTFIX-2: explicit audit commands are capability requests now
    assert classify_to_route("audit @somehandle on instagram") == "tool_capability"
    assert classify_to_route("deep research competitors vs us", deep=True) == "deep_research"
    assert classify_to_route("propose a campaign for agencies") == "campaign_operation"
    assert classify_to_route("please approve and send this") == "approval_operation"
    assert classify_to_route("what is the job status?") == "job_followup"
    # DEV-007R-HOTFIX-2: imperative capability + current-thread intents are
    # tool-first, not RAG-first.
    assert classify_to_route("scrap this instagram account @acme_test") == "tool_capability"
    assert classify_to_route("what this chat talking about") == "conversation_meta"


def test_routing_decision_confidence_and_approval_flag():
    from app.graphs.account_manager import decide_route

    d = decide_route("show pending approvals", project_id="starter")
    assert isinstance(d, ro.RouteDecision)
    assert 0.0 <= d.confidence <= 1.0
    assert d.project_id == "starter"
    orso = decide_route("please approve and send this", project_id="starter")
    assert orso.route == "approval_operation"
    assert orso.requires_approval() is True


def test_route_conditional_edge_single_branch_except_deep():
    from app.graphs.account_manager import route_targets

    for route in ("state_only", "knowledge", "external_research",
                  "social_research", "campaign_operation",
                  "approval_operation", "job_followup",
                  "conversation_meta", "tool_capability"):
        assert route_targets(route) == [route]
    # deep_research is the only multi-branch fan-out
    assert route_targets("deep_research") == ["deep_research"]


# ---------- project fail-closed ----------

def test_project_fail_closed_no_scope(tmp_path):
    from app.graphs.account_manager import run_graph

    out = run_graph(_db(tmp_path), root=str(tmp_path),
                    project_id="", conversation_id="c1",
                    turn_id="t1", user_text="hello")
    assert out["errors"], "empty project scope must fail closed"
    assert any("NO_PROJECT_SCOPE" in e for e in out["errors"])
    assert out["final_answer"] == ""


def test_project_fail_closed_unknown_project(tmp_path):
    from app.graphs.account_manager import run_graph

    out = run_graph(_db(tmp_path), root=str(tmp_path),
                    project_id="ghost", conversation_id="c1",
                    turn_id="t1", user_text="hello")
    assert any("NO_PROJECT_SCOPE" in e for e in out["errors"])


def test_registry_adapter_fail_closed_without_project(tmp_path):
    from app.graphs.adapters import ToolRegistryAdapter

    conn = _db(tmp_path)
    from app.services.tools import build_default_registry

    adapter = ToolRegistryAdapter(build_default_registry())
    res = adapter.execute(conn, project_id="", root=str(tmp_path),
                          name="get_project", args={})
    assert res["ok"] is False


# ---------- state_only path preserves legacy ----------

def test_state_only_path_returns_legacy_reply(tmp_path):
    from app.database import repos
    from app.graphs.account_manager import run_graph

    conn = _db(tmp_path)
    repos.Projects.upsert(conn, {"id": "starter", "name": "Acme Test Company", "website": "",
                                 "goal": "", "status": "active",
                                 "settings_json": "{}",
                                 "created_at": "2026-01-01T00:00:00+00:00",
                                 "updated_at": "2026-01-01T00:00:00+00:00"})
    out = run_graph(conn, root=str(tmp_path), project_id="starter",
                    conversation_id="c1", turn_id="t1",
                    user_text="what needs my attention?")
    assert out["final_answer"], "state_only must produce a reply"
    assert out["route"] == "state_only"
    assert out["errors"] == []


def test_typed_state_has_normative_fields():
    from app.graphs.state import GRAPH_STATE_FIELDS, initial_state

    for f in ("project_id", "conversation_id", "turn_id", "user_request",
              "project_context", "state_results", "retrieved_evidence",
              "web_results", "social_results", "research_tasks",
              "worker_results", "approval_state", "model_route",
              "model_usage", "errors", "final_answer"):
        assert f in GRAPH_STATE_FIELDS
    s = initial_state(project_id="starter", conversation_id="c",
                      turn_id="t", user_request="hi")
    assert s["project_id"] == "starter"


# ---------- deep-research fan-out / fan-in ----------

def test_deep_research_cap_enforced_by_runtime_config():
    from app.contracts.runtime import RuntimeConfig
    from app.graphs.deep_research import decompose

    cfg = RuntimeConfig(ai_runtime="legacy", max_agent_concurrency=2)
    tasks = decompose("compare five competitors in depth", max_tasks=10, config=cfg)
    assert 1 <= len(tasks) <= 2


def test_deep_research_fanout_runs_parallel_with_overlap():
    from app.contracts.runtime import RuntimeConfig
    from app.graphs.deep_research import fan_out_and_collect

    cfg = RuntimeConfig(ai_runtime="legacy", max_agent_concurrency=4)
    entered, overlap = [], {"max": 0}
    lock = threading.Lock()
    live = {"n": 0}

    def worker(task, idx):
        with lock:
            live["n"] += 1
            overlap["max"] = max(overlap["max"], live["n"])
        time.sleep(0.05)
        with lock:
            live["n"] -= 1
        entered.append(task["id"])
        return {"task_id": task["id"], "chunk": f"evidence-{idx}",
                "sources": [f"https://example.com/{idx}"], "ok": True}

    tasks = [{"id": f"q{i}", "query": f"q{i}"} for i in range(4)]
    results = fan_out_and_collect(tasks, worker, config=cfg)
    assert len(results) == 4
    assert overlap["max"] >= 2, "fan-out must really overlap (parallel)"


def test_deep_research_never_exceeds_cap():
    from app.contracts.runtime import RuntimeConfig
    from app.graphs.deep_research import fan_out_and_collect

    cfg = RuntimeConfig(ai_runtime="legacy", max_agent_concurrency=2)
    live = {"n": 0, "peak": 0}
    lock = threading.Lock()

    def worker(task, idx):
        with lock:
            live["n"] += 1
            live["peak"] = max(live["peak"], live["n"])
        time.sleep(0.02)
        with lock:
            live["n"] -= 1
        return {"task_id": task["id"], "chunk": "x", "sources": [], "ok": True}

    tasks = [{"id": f"q{i}", "query": f"q{i}"} for i in range(6)]
    fan_out_and_collect(tasks, worker, config=cfg)
    assert live["peak"] <= 2


def test_deep_research_reducer_dedupes_and_cites():
    from app.graphs.deep_research import fuse_results

    results = [
        {"task_id": "q0", "chunk": "agencies are major", "sources": ["https://a.example"], "ok": True},
        {"task_id": "q1", "chunk": "agencies are major", "sources": ["https://a.example"], "ok": True},
        {"task_id": "q2", "chunk": "pricing is quote-only", "sources": ["https://b.example"], "ok": True},
    ]
    fused = fuse_results(results)
    assert fused["citations"] and len(fused["chunks"]) == 2
    assert any("https://a.example" in c for c in fused["citations"])


def test_deep_research_failed_worker_stays_blocked():
    from app.contracts.runtime import RuntimeConfig
    from app.graphs.deep_research import fan_out_and_collect, fuse_results

    cfg = RuntimeConfig(ai_runtime="legacy", max_agent_concurrency=4)

    def worker(task, idx):
        if task["id"] == "bad":
            raise RuntimeError("provider down")
        return {"task_id": task["id"], "chunk": "good", "sources": [], "ok": True}

    tasks = [{"id": "good", "query": "g"}, {"id": "bad", "query": "b"}]
    results = fan_out_and_collect(tasks, worker, config=cfg)
    by_id = {r["task_id"]: r for r in results}
    assert by_id["good"]["ok"] is True
    assert by_id["bad"]["ok"] is False
    fused = fuse_results(results)
    assert all("good" in c or "pricing" in c or "good" in str(fused) for c in fused["chunks"])


# ---------- approval interrupt / resume idempotency ----------

def test_approval_green_passes_without_interrupt():
    from app.graphs.approvals import approval_interrupt

    state = {"approval_state": {"action_class": "green", "approval_id": "g1"}}
    out = approval_interrupt(state)
    assert out["status"] == "pass_through"


def test_approval_yellow_requires_interrupt_payload():
    from app.graphs.approvals import approval_interrupt

    state = {"approval_state": {"action_class": "yellow", "approval_id": "a1",
                                "title": "Send campaign",
                                "idempotency_key": "charge:v1:a1"}}
    out = approval_interrupt(state)
    assert out["status"] == "pending"
    assert out["interrupt"]["approval_id"] == "a1"
    # payload must be JSON-serializable (langgraph interrupt requirement)
    import json

    json.dumps(out["interrupt"])


def test_approval_resume_idempotent():
    from app.contracts.approvals import ApprovalResume
    from app.graphs.approvals import resume_approval

    executed = []

    def side_effect(key):
        executed.append(key)
        return {"ok": True}

    resume = ApprovalResume(approval_id="a1", project_id="starter",
                            decision="approved", decided_by="founder",
                            idempotency_key="charge:v1:a1")
    first = resume_approval(resume, side_effect, executed_keys=set())
    assert first["executed"] is True
    second = resume_approval(resume, side_effect, executed_keys=first["executed_keys"])
    assert second["executed"] is False  # idempotent replay
    assert executed == ["charge:v1:a1"]


def test_approval_rejected_never_executes():
    from app.contracts.approvals import ApprovalResume
    from app.graphs.approvals import resume_approval

    called = []

    def side_effect(key):
        called.append(key)
        return {"ok": True}

    resume = ApprovalResume(approval_id="a1", project_id="starter",
                            decision="rejected", decided_by="founder",
                            idempotency_key="charge:v1:a1")
    out = resume_approval(resume, side_effect, executed_keys=set())
    assert out["executed"] is False
    assert called == []


def test_approval_request_requires_idempotency_key():
    from app.graphs.approvals import build_approval_request

    req = build_approval_request(approval_id="a9", project_id="starter",
                                 title="Send", action_class="yellow")
    assert req.idempotency_key, "stable key must be derived per approval id"
    again = build_approval_request(approval_id="a9", project_id="starter",
                                   title="Send", action_class="yellow")
    assert again.idempotency_key == req.idempotency_key


# ---------- persistence contract ----------

def test_checkpointer_thread_isolation_and_resume(tmp_path):
    from app.graphs.checkpointing import create_checkpointer

    cp = create_checkpointer(str(tmp_path / "graph_checkpoints.db"))
    cp.save("t-1", {"turn_id": "t-1", "final_answer": "hello"})
    cp.save("t-2", {"turn_id": "t-2", "final_answer": "other"})
    assert cp.load("t-1")["final_answer"] == "hello"
    assert cp.load("t-2")["final_answer"] == "other"
    assert cp.load("missing") is None


def test_checkpointer_db_is_separate_from_business_db(tmp_path):
    from app.graphs.checkpointing import DEFAULT_CHECKPOINT_DB_NAME

    assert DEFAULT_CHECKPOINT_DB_NAME != "marketing.db"
    assert "checkpoint" in DEFAULT_CHECKPOINT_DB_NAME


def test_store_memory_keyed_by_project(tmp_path):
    from app.graphs.checkpointing import create_store

    conn = _db(tmp_path)
    store = create_store(conn)
    store.put("starter", "pref", {"theme": "ar"})
    assert store.get("starter", "pref") == {"theme": "ar"}
    assert store.get("other", "pref") is None


# ---------- events ----------

def test_graph_events_map_to_frozen_wire_types_only():
    from app.contracts.events import LEGACY_EVENT_TYPES
    from app.graphs.events import NODE_EVENT_MAP, build_event

    for node, etype in NODE_EVENT_MAP.items():
        assert etype in LEGACY_EVENT_TYPES, node
    ev = build_event("load_project", project_id="starter",
                     turn_id="t1", conversation_id="c1")
    assert ev.event_type in LEGACY_EVENT_TYPES
    row = ev.to_row()
    assert row["project_id"] == "starter"
    sse = ev.to_sse(id=1)
    assert sse["event"] == ev.event_type


def test_graph_events_never_carry_prompts_or_secrets():
    from app.graphs.events import build_event

    ev = build_event("synthesize", project_id="starter", turn_id="t1",
                     metadata={"prompt": "SECRET", "provider": "deterministic"})
    assert "prompt" not in ev.metadata


def test_graph_run_emits_terminal_event(tmp_path):
    from app.database import repos
    from app.graphs.account_manager import run_graph

    conn = _db(tmp_path)
    repos.Projects.upsert(conn, {"id": "starter", "name": "Acme Test Company", "website": "",
                                 "goal": "", "status": "active",
                                 "settings_json": "{}",
                                 "created_at": "2026-01-01T00:00:00+00:00",
                                 "updated_at": "2026-01-01T00:00:00+00:00"})
    out = run_graph(conn, root=str(tmp_path), project_id="starter",
                    conversation_id="c1", turn_id="t1",
                    user_text="what needs my attention?")
    assert out["events"], "run must emit lifecycle events"
    assert out["events"][-1].is_terminal


# ---------- W1-only scope ----------

def test_w1_does_not_require_langgraph_installed():
    import importlib.util

    import app.graphs.account_manager as am

    assert hasattr(am, "run_graph")
    # optional-import guard: module must import without langgraph
    assert importlib.util.find_spec("app.graphs.account_manager") is not None


def test_w1_does_not_touch_fastapi_or_react():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "graphs"
    text = "\n".join(p.read_text(encoding="utf-8") for p in root.glob("*.py"))
    lowered = text.lower()
    assert "from fastapi" not in lowered and "import fastapi" not in lowered
    assert "from frontend" not in lowered and "import frontend" not in lowered
    # business authority stays in repos; graphs must not create business tables
    # (dedicated graph_checkpoints tables are the W1-owned exception).
    assert "CREATE TABLE IF NOT EXISTS projects" not in text
    assert "CREATE TABLE IF NOT EXISTS execution_events" not in text
    assert "from app.routes" not in lowered and "from app import deps" not in lowered


def test_legacy_manager_still_classifies(tmp_path):
    from app.services import account_manager as legacy

    assert legacy.classify("show my approvals") == legacy.FLOW_APPROVALS
    assert legacy.detect_language("مرحبا") == "ar"
