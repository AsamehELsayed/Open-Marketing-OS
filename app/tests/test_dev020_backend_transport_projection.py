"""DEV-020 backend event aggregation and ID-correlation checks."""
import json
from types import SimpleNamespace


def _event(state, *, attempt_index=0, **extra):
    return {
        "state": state, "call_id": "call-safe-01", "turn_id": "turn-safe-01",
        "provider": "openrouter", "requested_model": "stealth/space-bunny-alpha",
        "endpoint_host": "127.0.0.1", "endpoint_path": "/api/v1/chat/completions",
        "http_method": "POST", "attempt_index": attempt_index, "elapsed_ms": 3,
        **extra,
    }


def test_pretransport_invocation_is_distinct_from_http_attempt():
    from app.routes.graph_runtime import _openrouter_transport_telemetry

    telemetry = _openrouter_transport_telemetry([
        _event("provider_request_failed", reason="credential_unavailable",
               network_phase="credential_vault", credential_reference_resolved=False,
               credential_decrypted="not_reached", client_initialized="not_reached",
               request_serialized="not_reached", transport_started=False),
    ], call_id="call-safe-01", turn_id="turn-safe-01",
       requested_model="stealth/space-bunny-alpha", app_invocation_count=1)

    assert telemetry["app_invocation_count"] == 1
    assert telemetry["http_attempt_count"] == 0
    assert telemetry["http_response_count"] == 0
    assert telemetry["network_attempted"] is False
    assert telemetry["network_phase"] == "vault"
    assert telemetry["checkpoints"]["credential_decrypted"] == "not_reached"


def test_401_status_and_provider_error_codes_survive_safe_projection():
    from app.routes.graph_runtime import _openrouter_transport_telemetry

    telemetry = _openrouter_transport_telemetry([
        _event("provider_request_started", attempt_index=1,
               request_serialized=True, transport_started=True),
        _event("provider_response_headers", attempt_index=1, http_status=401,
               content_type="application/json", provider_request_id="req-1",
               network_phase="http_response"),
        _event("provider_request_failed", attempt_index=1, http_status=401,
               exception_class="AuthenticationError", reason="http_error",
               network_phase="http_response", provider_error_code="invalid_key",
               provider_error_type="auth_error", raw_body="PRIVATE", headers={
                   "Authorization": "Bearer PRIVATE"}),
    ], call_id="call-safe-01", turn_id="turn-safe-01",
       requested_model="stealth/space-bunny-alpha", app_invocation_count=1)

    assert telemetry["http_attempt_count"] == 1
    assert telemetry["http_response_count"] == 1
    assert telemetry["http_status"] == 401
    assert telemetry["failure_code"] == "http_401"
    assert telemetry["provider_error_code"] == "invalid_key"
    assert telemetry["provider_error_type"] == "auth_error"
    encoded = json.dumps(telemetry)
    assert "PRIVATE" not in encoded and "Authorization" not in encoded


def test_http_status_families_have_specific_safe_failure_codes():
    from app.routes.graph_runtime import _openrouter_transport_telemetry

    for status, expected in ((401, "http_401"), (402, "http_402"),
                             (403, "http_403"), (404, "http_404"),
                             (408, "http_408"), (429, "http_429"),
                             (500, "http_5xx"), (503, "http_5xx")):
        telemetry = _openrouter_transport_telemetry([
            _event("provider_request_started", attempt_index=1),
            _event("provider_response_headers", attempt_index=1, http_status=status),
            _event("provider_request_failed", attempt_index=1, http_status=status,
                   reason="http_error", network_phase="http_response"),
        ], call_id="call-safe-01", turn_id="turn-safe-01",
           requested_model="stealth/space-bunny-alpha", app_invocation_count=1)
        assert telemetry["http_status"] == status
        assert telemetry["failure_code"] == expected
        assert telemetry["provider_safe_error_message"] == f"OpenRouter returned HTTP {status}."


def test_429_retry_then_success_counts_both_physical_attempts():
    from app.routes.graph_runtime import _openrouter_transport_telemetry

    events = [
        _event("provider_request_started", attempt_index=1),
        _event("provider_response_headers", attempt_index=1, http_status=429),
        _event("provider_request_failed", attempt_index=1, http_status=429,
               reason="http_error", network_phase="http_response"),
        _event("provider_request_started", attempt_index=2),
        _event("provider_response_headers", attempt_index=2, http_status=200),
        _event("provider_response_completed", attempt_index=2,
               actual_model="stealth/space-bunny-alpha",
               usage={"input": 7, "output": 4, "api_key": "PRIVATE"}),
    ]
    telemetry = _openrouter_transport_telemetry(
        events, call_id="call-safe-01", turn_id="turn-safe-01",
        requested_model="stealth/space-bunny-alpha", app_invocation_count=1)

    assert telemetry["http_attempt_count"] == 2
    assert telemetry["http_response_count"] == 2
    assert telemetry["http_status"] == 200
    assert [item["attempt_index"] for item in telemetry["attempts"]] == [1, 2]
    assert telemetry["attempts"][0]["http_status"] == 429
    assert telemetry["attempts"][1]["http_status"] == 200
    assert "failure_code" not in telemetry
    assert "PRIVATE" not in json.dumps(telemetry)


def test_failure_telemetry_projects_through_existing_sse_event_type():
    from app.contracts.events import GraphExecutionEvent
    from app.routes.graph_runtime import _generation_attempt_projection

    telemetry = {
        "call_id": "call-safe-01", "turn_id": "turn-safe-01",
        "provider": "openrouter", "requested_model": "stealth/space-bunny-alpha",
        "app_invocation_count": 1, "http_attempt_count": 1,
        "http_response_count": 1, "network_attempted": True,
        "http_status": 404, "network_phase": "http_response",
        "failure_code": "http_404", "failure_reason": "http_404",
        "events": [_event("provider_response_headers", attempt_index=1,
                           http_status=404)],
        "attempts": [{"attempt_index": 1, "http_status": 404}],
    }
    row = GraphExecutionEvent(
        project_id="project-safe", conversation_id="conversation-safe",
        turn_id="turn-safe-01", event_type="synthesis_completed",
        metadata={"call_id": "call-safe-01", "provider": "openrouter",
                  "generation_source": "none", "generation_status": "failed",
                  "transport_telemetry": telemetry,
                  **{key: telemetry[key] for key in (
                      "app_invocation_count", "http_attempt_count", "http_response_count",
                      "network_attempted", "http_status", "network_phase",
                      "failure_code", "failure_reason")}}).to_row()
    projected = _generation_attempt_projection([row])
    wire = GraphExecutionEvent.from_row(row).to_sse(id="event-safe")

    assert projected["call_id"] == wire["data"]["meta"]["call_id"] == "call-safe-01"
    assert projected["http_attempt_count"] == 1
    assert projected["http_status"] == wire["data"]["meta"]["http_status"] == 404
    assert wire["event"] == "synthesis_completed"


def test_model_router_passes_call_and_turn_ids_to_adapter_observer(tmp_path):
    from app.database.sqlite import connect
    from app.services.llm.base import LLMResponse
    from app.services.llm.model_router import ModelRouter

    class ObservedProvider:
        model = "stealth/space-bunny-alpha"

        def complete(self, *, system, messages, tools, opts=None):
            assert opts["call_id"] == "call-safe-01"
            assert opts["turn_id"] == "turn-safe-01"
            opts["_transport_observer"]({
                "state": "provider_request_started", "attempt_index": 1,
                "call_id": opts["call_id"], "turn_id": opts["turn_id"],
                "provider": "openrouter", "requested_model": self.model,
            })
            return LLMResponse(text="ok", tool_calls=[], usage={})

    events = []
    conn = connect(tmp_path / "dev020-router.db")
    router = ModelRouter(openrouter_provider=ObservedProvider(),
                         default_openrouter_model="stealth/space-bunny-alpha")
    router.complete(
        conn, turn_id="turn-safe-01", project_id="starter", system="s",
        messages=[], tools=[], mode="OPENROUTER", call_id="call-safe-01",
        on_transport_event=events.append)

    assert events[0]["call_id"] == "call-safe-01"
    assert events[0]["turn_id"] == "turn-safe-01"
    conn.close()


def test_account_manager_router_persistence_and_sse_keep_one_call_id(tmp_path, monkeypatch):
    from app import deps
    from app.contracts.events import GraphExecutionEvent
    from app.database import repos
    from app.database.sqlite import connect
    from app.routes import graph_runtime
    from app.services.llm.base import LLMResponse
    from app.services.llm import model_router as model_router_module
    from app.services.llm.model_router import ModelRouter

    db_path = tmp_path / "dev020-end-to-end.db"
    monkeypatch.setattr(deps, "DB_PATH", db_path)
    configured = {
        "ai_mode": "OPENROUTER", "openrouter_default_model": "stealth/space-bunny-alpha",
        "marketing_adapter": "", "llama_quantization": "",
    }
    monkeypatch.setattr(graph_runtime, "_get_setting",
                        lambda key, default=None: configured.get(key, default))
    monkeypatch.setattr(model_router_module, "openrouter_configured_default", lambda: True)

    class ObservedProvider:
        model = "stealth/space-bunny-alpha"

        def complete_streaming(self, *, system, messages, tools, opts=None, on_event=None):
            callback = opts["_transport_observer"]
            call_id = opts["call_id"]
            turn_id = opts["turn_id"]
            common = {"call_id": call_id, "turn_id": turn_id,
                      "provider": "openrouter", "requested_model": self.model,
                      "endpoint_host": "127.0.0.1", "endpoint_path": "/api/v1/chat/completions",
                      "http_method": "POST"}
            callback({**common, "state": "provider_request_prepared", "attempt_index": 0,
                      "checkpoints": {"credential_reference_resolved": True,
                                      "credential_decrypted": True,
                                      "client_initialized": True}})
            callback({**common, "state": "provider_request_started", "attempt_index": 1,
                      "elapsed_ms": 1, "network_phase": "transport_dispatch",
                      "checkpoints": {"request_serialized": True, "transport_started": True}})
            callback({**common, "state": "provider_response_headers", "attempt_index": 1,
                      "elapsed_ms": 2, "http_status": 200, "content_type": "application/json",
                      "provider_request_id": "req-e2e-1", "network_phase": "http_response"})
            callback({**common, "state": "provider_response_completed", "attempt_index": 1,
                      "elapsed_ms": 3, "actual_model": self.model,
                      "usage": {"input": 4, "output": 2, "total_tokens": 6}})
            return LLMResponse(text="synthetic answer", tool_calls=[], usage={
                "input_tokens": 4, "output_tokens": 2, "total_tokens": 6,
                "actual_model": self.model, "requested_model": self.model})

    router = ModelRouter(openrouter_provider=ObservedProvider(),
                         default_openrouter_model="stealth/space-bunny-alpha")
    complete = graph_runtime._make_complete_fn(router)
    answer = complete(
        turn_id="turn-e2e-01", project_id="starter", user_request="private prompt",
        route="knowledge", conversation_id="conversation-e2e-01", context={})

    conn = connect(db_path)
    calls = repos.ModelCalls.for_turn(conn, "turn-e2e-01", "starter")
    events = repos.ExecutionEvents.for_turn(conn, "turn-e2e-01")
    model_event = next(row for row in events if row["event_type"] == "model_completed")
    metadata = json.loads(model_event["metadata_json"])
    transport = metadata["transport_telemetry"]
    visibility = graph_runtime._visibility(conn, "turn-e2e-01", "starter")
    sse = GraphExecutionEvent.from_row(model_event).to_sse(id=model_event["id"])

    assert answer == "synthetic answer"
    assert len(calls) == 1
    assert transport["call_id"] == calls[0]["call_id"] == metadata["call_id"]
    assert transport["turn_id"] == calls[0]["turn_id"] == model_event["turn_id"]
    assert (transport["app_invocation_count"], transport["http_attempt_count"],
            transport["http_response_count"]) == (1, 1, 1)
    assert visibility["generation_attempt"]["call_id"] == calls[0]["call_id"]
    assert visibility["generation_attempt"]["http_status"] == 200
    assert sse["event"] == "model_completed"
    assert sse["data"]["call_id"] == calls[0]["call_id"]
    assert "private prompt" not in json.dumps(transport)
    conn.close()
