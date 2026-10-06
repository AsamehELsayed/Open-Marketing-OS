import json
import threading

from fastapi.testclient import TestClient

from app import deps
from app.database import repos
from app.main import create_app
from app.services import jobs as jobsvc
from app.services import state as store
from app.services import turns as turnsvc


RAW_SENTINEL = "QA-RAW-9F31"
PROVIDER_URL = "https://provider.example/v1/chat?tenant=qa"
TRACEBACK_MARKER = "Traceback (most recent call last):"
ENV_SYMBOL = "OPENAI_API_KEY=qa-secret-7F2"
RAW_DIAGNOSTIC = (
    f"RuntimeError: {RAW_SENTINEL} {PROVIDER_URL} "
    f"{TRACEBACK_MARKER} {ENV_SYMBOL}"
)


def _client(tmp_path, monkeypatch):
    db = tmp_path / "s1.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setattr(deps, "ROOT", tmp_path)
    deps.init_db(db)
    return TestClient(create_app()), db


def _sse_payloads(body):
    payloads = {}
    event_type = ""
    for line in body.splitlines():
        if line.startswith("event: "):
            event_type = line[7:].strip()
        elif line.startswith("data: ") and event_type:
            payloads[event_type] = json.loads(line[6:])
            event_type = ""
    return payloads


def test_sanitizer_preserves_product_text_and_metadata():
    from app.contracts.events import sanitize_metadata, sanitize_user_text

    answer = "Campaign plan: launch at https://example.com/campaign with 3 tasks."
    assert sanitize_user_text(answer) == answer
    assert sanitize_user_text(f"{answer} sk-1234567890") == (
        f"{answer} [REDACTED]"
    )
    assert "api.openai.com" not in sanitize_user_text(
        "Provider request failed: https://api.openai.com/v1/chat"
    )
    assert sanitize_metadata({
        "provider": "openrouter",
        "latency_ms": 125,
        "latency_display": "took 0.1s",
        "note": answer,
    }) == {
        "provider": "openrouter",
        "latency_ms": 125,
        "latency_display": "took 0.1s",
        "note": answer,
    }


def test_event_sanitizer_preserves_source_urls_and_stream_metadata():
    from app.contracts.events import GraphExecutionEvent

    source = GraphExecutionEvent(
        project_id="starter",
        event_type="web_source",
        detail="Source https://example.com/campaign?id=7",
        metadata={"url": "https://example.com/campaign?id=7"},
    ).to_sse(1)
    assert source["data"]["detail"] == "Source https://example.com/campaign?id=7"
    assert source["data"]["meta"]["url"] == "https://example.com/campaign?id=7"

    delta = GraphExecutionEvent(
        project_id="starter",
        turn_id="turn-stream",
        event_type="model_delta",
        detail=" Alpha",
        metadata={
            "turn_id": "turn-stream",
            "call_id": "call-stream",
            "sequence": 2,
            "delta": " Alpha",
        },
    ).to_sse(2)
    assert delta["data"]["delta"] == " Alpha"
    assert delta["data"]["meta"]["sequence"] == 2
    assert delta["data"]["meta"]["call_id"] == "call-stream"


def test_provider_exception_is_sanitized_in_persisted_message_and_actual_sse(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    from app.services import manager_loop
    from app.services.llm import base as llm_base
    from app.services.tools import build_default_registry

    with deps.get_db(db) as conn:
        conversation = store.create_conversation(conn, project_id="starter")
        made = turnsvc.create_turn(
            conn,
            str(db),
            conversation_id=conversation["id"],
            project_id="starter",
            text="Give me the current state",
            client_message_id="q001-sse",
        )

    def fail_provider(*args, **kwargs):
        raise RuntimeError(RAW_DIAGNOSTIC)

    monkeypatch.setattr(llm_base, "stream_complete", fail_provider)
    with deps.get_db(db) as conn:
        result = manager_loop.run_manager_turn(
            conn,
            project_id="starter",
            conversation_id=conversation["id"],
            user_text="Give me the current state",
            provider=object(),
            registry=build_default_registry(),
            root=tmp_path,
        )

    turnsvc._finish_turn(
        str(db),
        str(tmp_path),
        made["turn"],
        result,
        "openrouter",
        "Give me the current state",
        elapsed_ms=125,
    )
    response = client.get(f"/chat/turns/{made['turn']['id']}/events")
    assert response.status_code == 200
    payloads = _sse_payloads(response.text)
    detail = payloads["assistant_completed"]["detail"]
    metadata = payloads["assistant_completed"]["meta"]
    assert "Current project snapshot" in detail
    assert metadata["provider"] == "openrouter"
    assert metadata["latency_ms"] == 125
    for banned in (RAW_SENTINEL, PROVIDER_URL, TRACEBACK_MARKER, "OPENAI_API_KEY", "qa-secret-7F2"):
        assert banned not in detail
    with deps.get_db(db) as conn:
        assistants = [
            row for row in repos.Messages.for_conversation(conn, conversation["id"])
            if row["role"] == "assistant"
        ]
    assert len(assistants) == 1
    assert "Current project snapshot" in assistants[0]["body_md"]
    for banned in (RAW_SENTINEL, PROVIDER_URL, TRACEBACK_MARKER, "OPENAI_API_KEY", "qa-secret-7F2"):
        assert banned not in assistants[0]["body_md"]


def test_job_result_writer_omits_stderr_and_sanitizes_output(tmp_path):
    job = {
        "id": "job-q002-writer",
        "project_id": "starter",
        "kind": "probe",
        "job_type": "probe",
        "status": "failed",
        "finished_at": "2026-09-24T00:00:00+00:00",
    }
    jobsvc._write_result_json(
        str(tmp_path),
        job,
        {
            "stdout": f"Useful campaign result. {RAW_DIAGNOSTIC}",
            "stderr": RAW_DIAGNOSTIC,
            "exit_code": 1,
        },
    )
    path = jobsvc.result_path_for(tmp_path, "starter", job["id"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "Useful campaign result" in payload["stdout_tail"]
    assert "stderr_tail" not in payload
    for banned in (RAW_SENTINEL, PROVIDER_URL, TRACEBACK_MARKER, "OPENAI_API_KEY", "qa-secret-7F2"):
        assert banned not in json.dumps(payload)


def test_downloadable_job_result_drops_legacy_diagnostics(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    job_id = "job-q002-route"
    now = "2026-09-24T00:00:00+00:00"
    with deps.get_db(db) as conn:
        repos.BackgroundJobs.upsert(conn, {
            "id": job_id,
            "project_id": "starter",
            "conversation_id": "",
            "kind": "probe",
            "job_type": "probe",
            "brief_md": "",
            "status": "failed",
            "cmd_json": "{}",
            "cwd": str(tmp_path),
            "exit_code": 1,
            "log_path": "",
            "started_at": now,
            "finished_at": now,
            "created_at": now,
            "updated_at": now,
        })
        store.set_active_project(conn, "starter")
    path = jobsvc.result_path_for(tmp_path, "starter", job_id)
    path.write_text(json.dumps({
        "job_id": job_id,
        "kind": "probe",
        "job_type": "probe",
        "status": "failed",
        "exit_code": 1,
        "stdout_tail": f"Useful campaign result. {RAW_DIAGNOSTIC}",
        "stderr_tail": RAW_DIAGNOSTIC,
        "exception": RAW_DIAGNOSTIC,
        "provider_url": PROVIDER_URL,
        "env": {"OPENAI_API_KEY": "qa-secret-7F2"},
        "finished_at": now,
        "raw": RAW_DIAGNOSTIC,
    }), encoding="utf-8")

    response = client.get(f"/jobs/{job_id}/result")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert payload["stdout_tail"].startswith("Useful campaign result")
    assert set(payload) == {
        "job_id", "kind", "job_type", "status", "exit_code",
        "stdout_tail", "finished_at",
    }
    for banned in (RAW_SENTINEL, PROVIDER_URL, TRACEBACK_MARKER, "OPENAI_API_KEY", "qa-secret-7F2", RAW_DIAGNOSTIC):
        assert banned not in json.dumps(payload)


def test_typed_graph_capability_node_sanitizes_direct_fallback(monkeypatch):
    from langgraph.graph import StateGraph

    from app.graphs.account_manager_graph import build_account_manager_graph
    from app.graphs import tool_capability

    captured = {}
    original_add_node = StateGraph.add_node

    def capture_node(self, name, action):
        captured[name] = action
        return original_add_node(self, name, action)

    class FakeConnection:
        def close(self):
            return None

    monkeypatch.setattr(StateGraph, "add_node", capture_node)
    build_account_manager_graph(
        project_lookup=lambda project_id: {"id": project_id},
        conn_factory=FakeConnection,
    )

    def fail_capability(*args, **kwargs):
        raise RuntimeError(RAW_DIAGNOSTIC)

    monkeypatch.setattr(tool_capability, "resolve_and_execute", fail_capability)
    output = captured["tool_capability"]({
        "project_id": "starter",
        "turn_id": "q010-node",
        "intent": {
            "capability": "instagram_public_profile",
            "arguments": {"username": "starter"},
        },
    })
    answer = output["final_answer"]
    assert "couldn't run" in answer
    for banned in (RAW_SENTINEL, PROVIDER_URL, TRACEBACK_MARKER, "OPENAI_API_KEY", "qa-secret-7F2"):
        assert banned not in json.dumps(output)


def test_legacy_graph_project_fallback_omits_exception_details(monkeypatch):
    from app.graphs.account_manager import run_graph
    from app.graphs.adapters import ProjectAdapter

    def fail_project(conn, project_id):
        raise LookupError(RAW_DIAGNOSTIC)

    monkeypatch.setattr(ProjectAdapter, "get_project", staticmethod(fail_project))
    output = run_graph(
        object(),
        root=".",
        project_id="starter",
        conversation_id="q010-project",
        turn_id="q010-project",
        user_text="what needs my attention?",
    )
    blob = json.dumps(output, default=str)
    assert "Project could not be loaded" in blob
    for banned in (RAW_SENTINEL, PROVIDER_URL, TRACEBACK_MARKER, "OPENAI_API_KEY", "qa-secret-7F2"):
        assert banned not in blob


def test_legacy_graph_branch_fallback_omits_exception_details(monkeypatch):
    from app.graphs import account_manager as graph_manager
    from app.graphs.adapters import ConversationAdapter, ProjectAdapter

    monkeypatch.setattr(
        ProjectAdapter,
        "get_project",
        staticmethod(lambda conn, project_id: {"id": project_id}),
    )
    monkeypatch.setattr(
        ConversationAdapter,
        "load_messages",
        staticmethod(lambda conn, conversation_id, limit=10: []),
    )
    monkeypatch.setattr(
        ConversationAdapter,
        "load_memories",
        staticmethod(lambda conn, project_id, limit=5: []),
    )

    def fail_branch(*args, **kwargs):
        raise RuntimeError(RAW_DIAGNOSTIC)

    monkeypatch.setattr(graph_manager, "_legacy_reply", fail_branch)
    output = graph_manager.run_graph(
        object(),
        root=".",
        project_id="starter",
        conversation_id="q010-branch",
        turn_id="q010-branch",
        user_text="what needs my attention?",
    )
    blob = json.dumps(output, default=str)
    assert "branch state_only failed" in blob
    for banned in (RAW_SENTINEL, PROVIDER_URL, TRACEBACK_MARKER, "OPENAI_API_KEY", "qa-secret-7F2"):
        assert banned not in blob


def test_assistant_message_and_terminal_event_are_atomically_visible(tmp_path, monkeypatch):
    from app.database.seed import ensure_seed
    from app.database.sqlite import connect

    db = tmp_path / "q011.db"
    deps.init_db(db)
    conn = connect(db)
    ensure_seed(conn)
    conversation = store.create_conversation(conn, project_id="starter")
    made = turnsvc.create_turn(
        conn,
        str(db),
        conversation_id=conversation["id"],
        project_id="starter",
        text="what needs my attention?",
        client_message_id="q011-atomic",
    )
    conn.close()

    terminal_emit_started = threading.Event()
    release_terminal_emit = threading.Event()
    original_emit = turnsvc.emit_event

    def blocked_emit(db_path, **kwargs):
        if kwargs.get("event_type") == "turn_completed":
            terminal_emit_started.set()
            release_terminal_emit.wait(5)
        return original_emit(db_path, **kwargs)

    monkeypatch.setattr(turnsvc, "emit_event", blocked_emit)
    finished = threading.Event()

    def finish():
        turnsvc._finish_turn(
            str(db),
            str(tmp_path),
            made["turn"],
            {
                "reply_md": "One campaign needs attention.",
                "provenance": [],
                "job_ids": [],
                "approval_ids": [],
            },
            "deterministic",
            "what needs my attention?",
            elapsed_ms=10,
        )
        finished.set()

    writer = threading.Thread(target=finish, daemon=True)
    writer.start()
    try:
        finished.wait(0.75)
        reader = connect(db)
        assistants = [
            row for row in repos.Messages.for_conversation(reader, conversation["id"])
            if row["role"] == "assistant"
        ]
        event_types = {
            row["event_type"]
            for row in repos.ExecutionEvents.for_turn(reader, made["turn"]["id"])
        }
        reader.close()
        assert not assistants or "turn_completed" in event_types
    finally:
        release_terminal_emit.set()
        writer.join(5)
    assert not writer.is_alive()
    assert terminal_emit_started.is_set() or finished.is_set()
    reader = connect(db)
    assistants = [
        row for row in repos.Messages.for_conversation(reader, conversation["id"])
        if row["role"] == "assistant"
    ]
    event_types = {
        row["event_type"]
        for row in repos.ExecutionEvents.for_turn(reader, made["turn"]["id"])
    }
    reader.close()
    assert len(assistants) == 1
    assert "assistant_completed" in event_types
    assert "turn_completed" in event_types
