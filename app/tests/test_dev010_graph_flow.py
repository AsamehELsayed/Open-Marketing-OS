"""DEV-010 W2 graph proposal and approval continuation coverage."""
import json

from app.database import repos
from app.database.sqlite import connect
from app.graphs.account_manager_graph import _explicit_write_intents
from app.graphs.state import initial_state


def test_governed_campaign_send_requires_explicit_approval():
    intents = _explicit_write_intents("please approve and send the launch campaign")
    assert intents["campaign"]
    assert intents["requires_approval"] is True

    conditional = _explicit_write_intents(
        "Create a small marketing campaign. If an action requires approval, pause for approval."
    )
    assert conditional["campaign"]
    assert conditional["requires_approval"] is False


def test_exact_r3_prompt_extracts_objective_without_approval_clause():
    intents = _explicit_write_intents(
        "Create a small marketing campaign to improve website conversion. "
        "Analyze the current situation, create the required work, and prepare a "
        "proposed experiment. If an action requires approval, pause for approval."
    )
    assert intents["campaign"]["title"] == "Campaign: improve website conversion"
    assert intents["task"]["title"] == "Improve conversion: improve website conversion"
    assert "pause for approval" not in intents["experiment"]["hypothesis"].lower()


def _db(tmp_path):
    conn = connect(tmp_path / "graph-flow.sqlite")
    repos.Companies.upsert(conn, {"id": "co1", "name": "Test", "website": "", "created_at": "now"})
    repos.Projects.upsert(conn, {
        "id": "p1", "name": "Test", "website": "https://example.test",
        "goal": "Improve conversion", "status": "active", "settings_json": "{}",
        "created_at": "now", "updated_at": "now",
    })
    repos.Conversations.upsert(conn, {
        "id": "c1", "company_id": "co1", "project_id": "p1", "title": "Test", "created_at": "now",
        "updated_at": "now",
    })
    repos.Turns.upsert(conn, {
        "id": "t1", "conversation_id": "c1", "project_id": "p1",
        "status": "running", "created_at": "now", "updated_at": "now",
    })
    return conn


def _graph(conn, *, employee_fn=None):
    from app.graphs.account_manager_graph import build_account_manager_graph
    return build_account_manager_graph(
        conn_factory=lambda: conn, project_lookup=lambda _pid: {"id": "p1"},
        complete_fn=lambda **_kwargs: "Prepared the requested work.",
        employee_fn=employee_fn or (lambda _task, _role: {"findings": [], "sources": []}),
    )


def _state(text):
    return initial_state(project_id="p1", conversation_id="c1", turn_id="t1",
                         user_request=text)


def test_explicit_graph_request_persists_campaign_task_and_experiment(tmp_path):
    conn = _db(tmp_path)
    graph = _graph(conn)
    out = graph.invoke(
        _state("Create a small marketing campaign to improve website conversion. Analyze the current situation, create the required work, and prepare a proposed experiment. If an action requires approval, pause for approval."),
        config={"configurable": {"thread_id": "t1"}},
    )
    campaigns = repos.Campaigns.list(conn, "p1")
    tasks = repos.Tasks.list(conn, "p1")
    experiments = repos.Experiments.list(conn, "p1")
    assert len(campaigns) == len(tasks) == len(experiments) == 1
    assert tasks[0]["campaign_id"] == campaigns[0]["id"]
    assert experiments[0]["campaign_id"] == campaigns[0]["id"]
    assert experiments[0]["status"] == "proposed"
    assert repos.Approvals.list(conn, project_id="p1") == []
    assert repos.Measurements.for_experiment(conn, experiments[0]["id"]) == []
    task_wf = json.loads(tasks[0]["workflow_json"])
    assert task_wf["turn_id"] == task_wf["thread_id"] == "t1"
    assert out["write_results"]["campaign"]["campaign_id"] == campaigns[0]["id"]


def test_approval_interrupt_resumes_same_thread_and_rejection_cancels_task(tmp_path):
    conn = _db(tmp_path)
    graph = _graph(conn)
    out = graph.invoke(
        _state("Create a campaign to improve website conversion and create the required work. Submit this work for approval and pause."),
        config={"configurable": {"thread_id": "t1"}},
    )
    assert "__interrupt__" in out
    approval_id = out["__interrupt__"][0].value["approval_id"]
    approval = repos.Approvals.get(conn, approval_id, "p1")
    workflow = json.loads(approval["fields_json"])["workflow"]
    assert workflow["thread_id"] == workflow["turn_id"] == "t1"
    task_id = workflow["task_id"]

    from langgraph.types import Command
    resumed = graph.invoke(Command(resume={"approved": False}),
                           config={"configurable": {"thread_id": "t1"}})
    assert "rejected" in resumed["final_answer"].lower()
    assert repos.Tasks.get(conn, task_id, "p1")["status"] == "rejected"


def test_approval_route_resumes_its_recorded_graph_thread(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from app.routes import graph_runtime

    conn = _db(tmp_path)
    graph = _graph(conn)
    paused = graph.invoke(
        _state("Create a campaign to improve conversion and create the required work. Submit this work for approval."),
        config={"configurable": {"thread_id": "t1"}},
    )
    aid = paused["__interrupt__"][0].value["approval_id"]
    approval = repos.Approvals.get(conn, aid, "p1")
    task_id = json.loads(approval["fields_json"])["workflow"]["task_id"]

    @contextmanager
    def db_context():
        yield conn

    monkeypatch.setattr(graph_runtime.deps, "get_db", db_context)
    monkeypatch.setattr(graph_runtime, "_get_graph", lambda: graph)
    response = graph_runtime.compat_approval_resume(
        aid, graph_runtime.CompatResumeRequest(
            decision="rejected", decided_by="reviewer", idempotency_key="decision-1"))
    payload = json.loads(response.body)
    payload = payload.get("data", payload)
    assert payload["thread_id"] == "t1"
    assert repos.Approvals.get(conn, aid, "p1")["status"] == "rejected"
    assert repos.Tasks.get(conn, task_id, "p1")["status"] == "rejected"


def test_graph_replay_uses_stable_proposal_keys_without_duplicate_rows(tmp_path):
    conn = _db(tmp_path)
    graph = _graph(conn)
    state = _state("Create a campaign to improve conversion and prepare an experiment.")
    cfg = {"configurable": {"thread_id": "t1"}}
    graph.invoke(state, config=cfg)
    graph.invoke(state, config=cfg)
    assert len(repos.Campaigns.list(conn, "p1")) == 1
    assert len(repos.Experiments.list(conn, "p1")) == 1


def test_graph_keeps_brainstorm_as_prose_without_persisting_records(tmp_path):
    conn = _db(tmp_path)
    graph = _graph(conn)
    graph.invoke(_state("Brainstorm ideas for a conversion campaign and experiment."),
                 config={"configurable": {"thread_id": "t1"}})
    assert repos.Campaigns.list(conn, "p1") == []
    assert repos.Tasks.list(conn, "p1") == []
    assert repos.Experiments.list(conn, "p1") == []


def test_graph_write_failure_is_reported_without_claiming_saved_ids(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    from app.graphs.adapters import ToolRegistryAdapter
    monkeypatch.setattr(ToolRegistryAdapter, "execute", lambda *a, **k: {
        "ok": False, "status": "failed", "error": "unavailable",
    })
    out = _graph(conn).invoke(
        _state("Create a campaign to improve conversion."),
        config={"configurable": {"thread_id": "t1"}},
    )
    assert "could not be saved" in out["final_answer"].lower()
    assert "Saved:" not in out["final_answer"]
    assert repos.Campaigns.list(conn, "p1") == []
