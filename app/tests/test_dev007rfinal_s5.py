from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.database import repos
from app.database.sqlite import connect
from app.main import create_app
from app.services import state as store
from app.services import turns as turnsvc


RAW_SENTINEL = "S5-RAW-7C21"
PROVIDER_URL = "https://provider.example/v1/chat?tenant=s5"
# DEV-008-PUBLISH-GATE: this used to hardcode a real developer's home directory
# (a literal `C:` + `Users` + username path). In the development checkout that
# silently leaked the machine owner's username into an exported test file; on any
# other machine the path simply pointed somewhere meaningless. A fabricated,
# obviously non-existent path tests the same Windows-path branch deterministically.
WINDOWS_PATH = r"C:\Users\omos-test-user\private\s5\trace.log"
VAULT_REF = "vault://installation/0123456789abcdef0123456789abcdef"
TRACEBACK = "Traceback (most recent call last):"
RAW_DIAGNOSTIC = (
    f"Product copy stays. Provider request failed: {RAW_SENTINEL} {PROVIDER_URL} "
    f"{WINDOWS_PATH} OPENAI_API_KEY=s5-secret-42 {VAULT_REF} "
    f"{TRACEBACK} File {WINDOWS_PATH!r}, line 99"
)
BANNED = (
    RAW_SENTINEL,
    PROVIDER_URL,
    WINDOWS_PATH,
    "OPENAI_API_KEY",
    "s5-secret-42",
    VAULT_REF,
    "Traceback",
)


def _sse_payloads(body: str) -> dict[str, dict]:
    payloads: dict[str, dict] = {}
    event_type = ""
    data_lines: list[str] = []
    for line in body.splitlines() + [""]:
        if line.startswith("event: "):
            event_type = line[7:].strip()
        elif line.startswith("data: ") and event_type:
            data_lines.append(line[6:])
        elif not line and event_type:
            try:
                value = json.loads("\n".join(data_lines) or "{}")
            except json.JSONDecodeError:
                value = {"raw": "\n".join(data_lines)}
            payloads[event_type] = value if isinstance(value, dict) else {"value": value}
            event_type = ""
            data_lines = []
    return payloads


@pytest.fixture()
def boundary_client(tmp_path, monkeypatch):
    db = tmp_path / "s5.db"
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setattr(deps, "ROOT", root)
    monkeypatch.setenv("AI_RUNTIME", "langgraph")
    monkeypatch.setenv("OMOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OMOS_CREDENTIALS_DIR", str(tmp_path / "vault"))
    deps.init_db(db)
    with deps.get_db(db) as conn:
        conversation = store.create_conversation(conn, project_id="starter")
    from app.routes import graph_runtime
    monkeypatch.setattr(graph_runtime, "_GRAPH", None, raising=False)
    with TestClient(create_app()) as client:
        yield client, db, root, conversation["id"]


def _turn(db, conversation_id, key):
    with deps.get_db(db) as conn:
        return turnsvc.create_turn(
            conn,
            str(db),
            conversation_id=conversation_id,
            project_id="starter",
            text="show the project",
            client_message_id=key,
        )


def _event(client, db, conversation_id, turn_id, *, event_type="worker_failed", label="", detail="", metadata=None, job_id="job-s5"):
    with deps.get_db(db) as conn:
        return repos.ExecutionEvents.insert(conn, {
            "project_id": "starter",
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "job_id": job_id,
            "event_type": event_type,
            "label": label,
            "detail": detail,
            "metadata_json": json.dumps(metadata or {}),
            "created_at": "2026-09-25T00:00:00+00:00",
        })


def test_chat_assistant_and_sse_use_the_shared_safe_projection(boundary_client):
    client, db, _root, conversation_id = boundary_client
    made = _turn(db, conversation_id, "s5-chat")
    turnsvc._finish_turn(
        str(db),
        str(Path(db).parent),
        made["turn"],
        {"reply_md": RAW_DIAGNOSTIC, "provenance": [], "job_ids": [], "approval_ids": []},
        "openrouter",
        "show the project",
        elapsed_ms=8,
    )
    response = client.get(f"/chat/turns/{made['turn']['id']}/events")
    assert response.status_code == 200
    payload = _sse_payloads(response.text)["assistant_completed"]
    assert "Product copy stays." in payload["detail"]
    for banned in BANNED:
        assert banned not in json.dumps(payload)
    transcript = client.get(
        f"/api/chats/{conversation_id}/messages", params={"project_id": "starter"}
    )
    assistant = next(row for row in transcript.json()["data"] if row["role"] == "assistant")
    assert "Product copy stays." in assistant["body_md"]
    for banned in BANNED:
        assert banned not in json.dumps(assistant)


def test_activity_api_projects_persisted_label_and_detail(boundary_client):
    client, db, _root, conversation_id = boundary_client
    _event(
        client,
        db,
        conversation_id,
        "activity-turn",
        label=f"Activity {RAW_DIAGNOSTIC}",
        detail=f"Detail {RAW_DIAGNOSTIC}",
    )
    response = client.get(
        "/api/activity", params={"conversation_id": conversation_id, "project_id": "starter"}
    )
    assert response.status_code == 200
    row = response.json()["data"][0]
    assert "Activity Product copy stays." in row["label"]
    assert "Detail Product copy stays." in row["detail"]
    for banned in BANNED:
        assert banned not in json.dumps(row)


@pytest.mark.parametrize("endpoint", ["/files/upload", "/files/upload/image"])
@pytest.mark.parametrize("failure_kind", ["value", "ingest"])
def test_files_upload_failures_return_stable_safe_messages(
    boundary_client, monkeypatch, endpoint, failure_kind
):
    client, _db, _root, _conversation_id = boundary_client
    from app.routes import files as files_route
    from app.services.files import IngestError

    def fail_ingest(*args, **kwargs):
        if failure_kind == "ingest":
            raise IngestError("ingest_rejected", RAW_DIAGNOSTIC)
        raise ValueError(RAW_DIAGNOSTIC)

    monkeypatch.setattr(files_route, "ingest", fail_ingest)
    response = client.post(
        endpoint,
        files={"file": ("notes.txt", b"hello", "text/plain")},
        data={"project_id": "starter", "attach_scope": "project"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "ingest_rejected"
    assert body["error"]["message"] == "The file could not be accepted."
    for banned in BANNED:
        assert banned not in json.dumps(body)


def _create_graph_thread(client, conversation_id, key):
    response = client.post(
        "/api/graph/threads",
        json={
            "project_id": "starter",
            "conversation_id": conversation_id,
            "text": "run a safe answer",
            "client_message_id": key,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["thread_id"]


def test_graph_execute_sanitizes_final_answer_and_transcript(boundary_client, monkeypatch):
    client, db, _root, conversation_id = boundary_client
    thread_id = _create_graph_thread(client, conversation_id, "s5-graph")

    class FakeGraph:
        def invoke(self, state, config):
            return {"final_answer": RAW_DIAGNOSTIC, "route": "state_only", "errors": []}

    from app.routes import graph_runtime
    monkeypatch.setattr(graph_runtime, "_get_graph", lambda: FakeGraph())
    response = client.post(f"/api/graph/threads/{thread_id}/execute")
    assert response.status_code == 200, response.text
    answer = response.json()["data"]["final_answer"]
    assert "Product copy stays." in answer
    for banned in BANNED:
        assert banned not in json.dumps(response.json())
    transcript = client.get(
        f"/api/chats/{conversation_id}/messages", params={"project_id": "starter"}
    )
    assistant = next(row for row in transcript.json()["data"] if row["role"] == "assistant")
    assert "Product copy stays." in assistant["body_md"]
    for banned in BANNED:
        assert banned not in json.dumps(assistant)


def test_graph_sse_projects_nested_and_top_level_telemetry(boundary_client):
    client, db, _root, conversation_id = boundary_client
    thread_id = _create_graph_thread(client, conversation_id, "s5-graph-sse")
    with deps.get_db(db) as conn:
        repos.ExecutionEvents.insert(conn, {
            "project_id": "starter",
            "conversation_id": conversation_id,
            "turn_id": thread_id,
            "job_id": "",
            "event_type": "model_completed",
            "label": "Response ready",
            "detail": "Model completed",
            "metadata_json": json.dumps({
                "provider": "openrouter",
                "model": "vendor/model",
                "telemetry": {
                    "provider": "openrouter",
                    "latency_ms": 12,
                    "diagnostic": RAW_DIAGNOSTIC,
                    "provider_url": PROVIDER_URL,
                },
            }),
            "created_at": "2026-09-25T00:00:00+00:00",
        })
        repos.Turns.set_status(conn, thread_id, "completed", {"provider": "graph"})
    response = client.get(f"/api/graph/threads/{thread_id}/events")
    assert response.status_code == 200
    payload = _sse_payloads(response.text)["model_completed"]
    assert payload["meta"]["telemetry"]["provider"] == "openrouter"
    assert payload["telemetry"]["latency_ms"] == 12
    assert "Product copy stays." in payload["telemetry"]["diagnostic"]
    for banned in BANNED:
        assert banned not in json.dumps(payload)


def test_model_completed_sse_filters_nested_environment_keys(boundary_client):
    client, db, _root, conversation_id = boundary_client
    thread_id = _create_graph_thread(client, conversation_id, "s5-env-keys")
    metadata = {
        "provider": "openrouter",
        "model": "vendor/model",
        "safe_note": "keep this product metadata",
        "OPENAI_API_KEY": "root-openai-value",
        "OPENROUTER_API_KEY": "root-openrouter-value",
        "telemetry": {
            "provider": "openrouter",
            "model": "vendor/model",
            "latency_ms": 12,
            "safe_note": "keep this telemetry",
            "OPENAI_API_KEY": "nested-openai-value",
            "OPENROUTER_API_KEY": "nested-openrouter-value",
            "nested": {
                "OPENAI_API_KEY": "deep-openai-value",
                "OPENROUTER_API_KEY": "deep-openrouter-value",
                "safe": "deep-safe",
            },
            "items": [
                {"OPENAI_API_KEY": "list-openai-value", "safe": "list-safe"},
                {"OPENROUTER_API_KEY": "list-openrouter-value", "safe": "list-safe"},
            ],
        },
    }
    with deps.get_db(db) as conn:
        repos.ExecutionEvents.insert(conn, {
            "project_id": "starter",
            "conversation_id": conversation_id,
            "turn_id": thread_id,
            "job_id": "",
            "event_type": "model_completed",
            "label": "Response ready",
            "detail": "Model completed",
            "metadata_json": json.dumps(metadata),
            "created_at": "2026-09-25T00:00:00+00:00",
        })
        repos.ExecutionEvents.insert(conn, {
            "project_id": "starter",
            "conversation_id": conversation_id,
            "turn_id": thread_id,
            "job_id": "",
            "event_type": "turn_completed",
            "label": "Response ready",
            "detail": "",
            "metadata_json": "{}",
            "created_at": "2026-09-25T00:00:00+00:00",
        })
        repos.Turns.set_status(conn, thread_id, "completed", {"provider": "graph"})

    graph_response = client.get(f"/api/graph/threads/{thread_id}/events")
    assert graph_response.status_code == 200
    graph_payload = _sse_payloads(graph_response.text)["model_completed"]
    chat_response = client.get(f"/chat/turns/{thread_id}/events")
    assert chat_response.status_code == 200
    chat_payload = _sse_payloads(chat_response.text)["model_completed"]

    for response, payload in (
        (graph_response, graph_payload),
        (chat_response, chat_payload),
    ):
        body = json.dumps(payload)
        assert "OPENAI_API_KEY" not in body
        assert "OPENROUTER_API_KEY" not in body
        for value in (
            "root-openai-value",
            "root-openrouter-value",
            "nested-openai-value",
            "nested-openrouter-value",
            "deep-openai-value",
            "deep-openrouter-value",
            "list-openai-value",
            "list-openrouter-value",
        ):
            assert value not in body
        assert payload["meta"]["provider"] == "openrouter"
        assert payload["meta"]["model"] == "vendor/model"
        assert payload["meta"]["telemetry"]["provider"] == "openrouter"
        assert payload["meta"]["telemetry"]["model"] == "vendor/model"
        assert payload["meta"]["telemetry"]["latency_ms"] == 12
        assert payload["meta"]["telemetry"]["safe_note"] == "keep this telemetry"
        if "telemetry" in payload:
            assert payload["telemetry"]["provider"] == "openrouter"
            assert payload["telemetry"]["latency_ms"] == 12


def _job_row(db, job_id, *, status="failed"):
    with deps.get_db(db) as conn:
        repos.BackgroundJobs.upsert(conn, {
            "id": job_id,
            "project_id": "starter",
            "conversation_id": "",
            "kind": "probe",
            "job_type": "probe",
            "brief_md": "",
            "status": status,
            "cmd_json": "{}",
            "cwd": str(Path(db).parent),
            "exit_code": 1,
            "log_path": "",
            "started_at": "2026-09-25T00:00:00+00:00",
            "finished_at": "2026-09-25T00:00:00+00:00",
            "created_at": "2026-09-25T00:00:00+00:00",
            "updated_at": "2026-09-25T00:00:00+00:00",
        })
        store.set_active_project(conn, "starter")


def test_job_sse_projects_label_and_detail(boundary_client):
    client, db, _root, conversation_id = boundary_client
    _job_row(db, "job-sse")
    _event(
        client,
        db,
        conversation_id,
        "job-turn",
        label=f"Job {RAW_DIAGNOSTIC}",
        detail=f"Failure {RAW_DIAGNOSTIC}",
        job_id="job-sse",
    )
    response = client.get("/jobs/job-sse/events")
    assert response.status_code == 200
    payload = _sse_payloads(response.text)["worker_failed"]
    assert "Job Product copy stays." in payload["label"]
    assert "Failure Product copy stays." in payload["detail"]
    for banned in BANNED:
        assert banned not in json.dumps(payload)


def test_downloadable_job_result_preserves_copy_and_drops_diagnostics(boundary_client):
    client, db, root, _conversation_id = boundary_client
    _job_row(db, "job-result")
    from app.services import jobs as jobsvc
    path = jobsvc.result_path_for(root, "starter", "job-result")
    path.write_text(json.dumps({
        "job_id": "job-result",
        "kind": "probe",
        "job_type": "probe",
        "status": "failed",
        "exit_code": 1,
        "stdout_tail": f"Useful campaign result. {RAW_DIAGNOSTIC}",
        "stderr_tail": RAW_DIAGNOSTIC,
        "exception": RAW_DIAGNOSTIC,
        "provider_url": PROVIDER_URL,
        "env": {"OPENAI_API_KEY": "s5-secret-42"},
        "finished_at": "2026-09-25T00:00:00+00:00",
    }), encoding="utf-8")
    response = client.get("/jobs/job-result/result")
    assert response.status_code == 200
    body = response.json()
    assert body["stdout_tail"].startswith("Useful campaign result.")
    for key in ("stderr_tail", "exception", "provider_url", "env"):
        assert key not in body
    for banned in BANNED:
        assert banned not in json.dumps(body)


def test_deep_research_and_tool_error_sinks_are_generic():
    from app.graphs.adapters import ToolRegistryAdapter
    from app.graphs.deep_research import fan_out_and_collect, fuse_results
    from app.graphs.deep_research_graph import build_deep_research_graph
    from app.services.tools.registry import Registry, ToolDef

    def failing_worker(*args, **kwargs):
        raise RuntimeError(RAW_DIAGNOSTIC)

    collected = fan_out_and_collect(
        [{"id": "q0", "query": "research"}], failing_worker
    )
    fused = fuse_results(collected)
    for value in (collected, fused):
        for banned in BANNED:
            assert banned not in json.dumps(value)

    graph = build_deep_research_graph(worker_fn=failing_worker)
    graph_result = graph.invoke(
        {"question": "research"},
        config={"configurable": {"thread_id": "s5-deep"}},
    )
    for banned in BANNED:
        assert banned not in json.dumps(graph_result, default=str)

    registry = Registry()
    registry.register(ToolDef(
        name="probe",
        description="probe",
        parameters={"type": "object"},
        side_effect="green",
        handler=failing_worker,
    ))
    conn = sqlite3.connect(":memory:")
    result = registry.execute(conn, project_id="starter", root=".", name="probe", args={})
    assert result["ok"] is False
    for banned in BANNED:
        assert banned not in json.dumps(result)

    def opaque_failure(*args, **kwargs):
        raise RuntimeError("opaque diagnostic detail")

    opaque_registry = Registry()
    opaque_registry.register(ToolDef(
        name="opaque",
        description="opaque",
        parameters={"type": "object"},
        side_effect="green",
        handler=opaque_failure,
    ))
    opaque_result = opaque_registry.execute(
        conn, project_id="starter", root=".", name="opaque", args={}
    )
    assert opaque_result["error"] == "The tool could not be completed."
    conn.close()

    class RawRegistry:
        def execute(self, *args, **kwargs):
            return {"ok": False, "error": RAW_DIAGNOSTIC}

    adapter_result = ToolRegistryAdapter(RawRegistry()).execute(
        object(), project_id="starter", root=".", name="probe", args={}
    )
    for banned in BANNED:
        assert banned not in json.dumps(adapter_result)


def test_normal_product_copy_urls_and_event_semantics_survive():
    from app.contracts.events import GraphExecutionEvent, sanitize_metadata, sanitize_user_text

    copy = "See the campaign source at https://example.com/campaign?id=7"
    assert sanitize_user_text(copy) == copy
    assert sanitize_metadata({"provider": "openrouter", "latency_ms": 12}) == {
        "provider": "openrouter",
        "latency_ms": 12,
    }
    event = GraphExecutionEvent(
        project_id="starter",
        turn_id="turn-safe",
        event_type="model_completed",
        label="Response ready",
        detail="Done",
        metadata={"provider": "openrouter", "telemetry": {"model": "vendor/model"}},
    ).to_sse(3)
    assert event["data"]["meta"]["provider"] == "openrouter"
    assert event["data"]["telemetry"]["model"] == "vendor/model"
    assert event["id"] == 3
