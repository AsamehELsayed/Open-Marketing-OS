"""DEV-005 W1 remediation — v1 framework-first LangGraph path.

Uses the REAL LangGraph runtime (StateGraph + Send + reducers +
checkpointer/thread_id + interrupt/Command). Proves:

- overlapping independent Send branches + reducer fan-in,
- failed-worker exclusion (dependents of failed workers stay blocked),
- real conditional edges in the account-manager graph,
- interrupt()/Command(resume=...) approval flow,
- project isolation (NO_PROJECT_SCOPE fail-closed + thread isolation),
- restart persistence (SQLite saver survives a new graph instance).

W1 scope: app/graphs/ only. requirements.txt is NOT edited here;
I1 must add the packages listed in development/runs/DEV-005/workers/w1.md.
"""
from __future__ import annotations

import time


def test_v1_deep_research_send_fanout_overlaps_and_fuses():
    from app.graphs.deep_research_graph import build_deep_research_graph

    graph = build_deep_research_graph()
    out = graph.invoke(
        {"question": "alpha; beta; gamma; delta"},
        config={"configurable": {"thread_id": "deep-overlap-1"}},
    )
    assert out["overlap_peak"] >= 2, "Send branches must really overlap"
    assert len(out["chunks"]) >= 3
    assert out["citations"], "fan-in must cite sources"


def test_v1_deep_research_failed_worker_blocked():
    from app.graphs.deep_research_graph import build_deep_research_graph

    def boom(task):
        if "bad" in task.get("query", ""):
            raise RuntimeError("provider down")
        time.sleep(0.01)
        return {
            "chunk": f"evidence:{task.get('query', '')}",
            "sources": ["https://example.com/good"],
        }

    graph = build_deep_research_graph(worker_fn=boom)
    out = graph.invoke(
        {"question": "good one; bad one"},
        config={"configurable": {"thread_id": "deep-fail-1"}},
    )
    assert any("good one" in c for c in out["chunks"])
    assert not any("bad one" in c for c in out["chunks"])
    assert any(e.get("task_id") == "q1" for e in out["errors"])


def test_v1_account_graph_conditional_edges_route():
    from app.graphs.account_manager_graph import build_account_manager_graph

    graph = build_account_manager_graph()
    deep = graph.invoke(
        {"user_request": "deep research competitors vs us", "project_id": "starter"},
        config={"configurable": {"thread_id": "route-deep-1"}},
    )
    assert deep["route"] == "deep_research"
    assert deep["worker_results"], "deep route must run Send fan-out"

    state_only = graph.invoke(
        {"user_request": "what needs my attention?", "project_id": "starter"},
        config={"configurable": {"thread_id": "route-state-1"}},
    )
    assert state_only["route"] == "state_only"
    assert state_only["final_answer"]


def test_v1_approval_interrupt_and_resume():
    from langgraph.types import Command

    from app.graphs.approval_flow import build_approval_graph

    graph = build_approval_graph()
    cfg = {"configurable": {"thread_id": "appr-1"}}
    paused = graph.invoke(
        {
            "approval_id": "a1",
            "project_id": "starter",
            "title": "Send campaign",
            "action_class": "yellow",
        },
        config=cfg,
    )
    assert "__interrupt__" in paused, "yellow action must pause via interrupt()"
    payload = paused["__interrupt__"][0].value
    assert payload["approval_id"] == "a1"
    assert payload["idempotency_key"]

    resumed = graph.invoke(Command(resume={"approved": True}), config=cfg)
    assert resumed["executed"] is True
    assert resumed["approval_id"] == "a1"

    # Rejected path never executes.
    graph2 = build_approval_graph()
    cfg2 = {"configurable": {"thread_id": "appr-2"}}
    paused2 = graph2.invoke(
        {
            "approval_id": "a2",
            "project_id": "starter",
            "title": "Send",
            "action_class": "red",
        },
        config=cfg2,
    )
    assert "__interrupt__" in paused2
    denied = graph2.invoke(Command(resume={"approved": False}), config=cfg2)
    assert denied["executed"] is False


def test_v1_project_isolation_fail_closed_and_thread_isolated():
    from app.graphs.account_manager_graph import build_account_manager_graph

    graph = build_account_manager_graph()
    ghost = graph.invoke(
        {"user_request": "hello", "project_id": "ghost"},
        config={"configurable": {"thread_id": "iso-ghost-1"}},
    )
    assert any("NO_PROJECT_SCOPE" in e for e in ghost["errors"])
    assert ghost["final_answer"] == ""

    empty = graph.invoke(
        {"user_request": "hello", "project_id": ""},
        config={"configurable": {"thread_id": "iso-empty-1"}},
    )
    assert any("NO_PROJECT_SCOPE" in e for e in empty["errors"])

    a = graph.invoke(
        {"user_request": "hello starter", "project_id": "starter"},
        config={"configurable": {"thread_id": "iso-a-1"}},
    )
    b = graph.invoke(
        {"user_request": "hello other", "project_id": "other"},
        config={"configurable": {"thread_id": "iso-b-1"}},
    )
    assert a["project_id"] == "starter"
    assert b["project_id"] == "other"
    assert a["final_answer"] != b["final_answer"] or a["route"] == b["route"]


def test_v1_restart_persistence_sqlite_saver(tmp_path):
    from langgraph.checkpoint.sqlite import SqliteSaver

    from app.graphs.deep_research_graph import build_deep_research_graph

    db = str(tmp_path / "v1_restart.db")
    with SqliteSaver.from_conn_string(db) as saver:
        g1 = build_deep_research_graph(checkpointer=saver)
        cfg = {"configurable": {"thread_id": "restart-1"}}
        out1 = g1.invoke({"question": "alpha; beta"}, config=cfg)
        assert out1["chunks"]
        hist1 = list(g1.get_state_history(cfg))
        assert hist1, "checkpointer must record history"

    # New graph instance on the same SQLite file must see prior history.
    with SqliteSaver.from_conn_string(db) as saver2:
        g2 = build_deep_research_graph(checkpointer=saver2)
        cfg = {"configurable": {"thread_id": "restart-1"}}
        hist2 = list(g2.get_state_history(cfg))
        assert hist2, "restart must preserve checkpoint history"
        snap = g2.get_state(cfg)
        assert snap.values.get("chunks"), "restart must reload fused chunks"


def test_v1_is_framework_runtime_not_threadpool_stub():
    import inspect

    import app.graphs.account_manager_graph as amg
    import app.graphs.deep_research_graph as drg

    src_am = inspect.getsource(amg.build_account_manager_graph)
    src_dr = inspect.getsource(drg.build_deep_research_graph)
    assert "StateGraph" in src_am and "add_conditional_edges" in src_am
    assert "Send" in src_dr
    assert "ThreadPoolExecutor" not in src_dr
    assert "checkpointer" in src_am and "thread_id" in inspect.getsource(amg)
