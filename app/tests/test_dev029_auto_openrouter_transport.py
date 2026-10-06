"""Resolved AUTO provider transport telemetry stays tied to the real route."""
import json


def _run_turn(tmp_path, monkeypatch, *, ai_mode, local_provider, openrouter_provider):
    from app import deps
    from app.database import repos
    from app.database.sqlite import connect
    from app.routes import graph_runtime
    from app.services import config_service
    from app.services.llm import model_router as router_module
    from app.services.llm.model_router import ModelRouter

    db_path = tmp_path / f"dev029-{ai_mode.lower()}-transport.db"
    monkeypatch.setattr(deps, "DB_PATH", db_path)
    _settings = {
        "ai_mode": ai_mode,
        "manager_provider": "openrouter",
        "cloud_escalation": "on",
        "local_model_enabled": "off" if local_provider is None else "on",
        "openrouter_default_model": "fixture/or-alpha",
        "marketing_adapter": "",
        "llama_quantization": "",
    }
    monkeypatch.setattr(graph_runtime, "_get_setting",
                        lambda key, default=None: _settings.get(key, default))
    monkeypatch.setattr(router_module, "openrouter_configured_default", lambda: True)
    monkeypatch.setattr(router_module, "openai_configured", lambda: False)
    monkeypatch.setitem(config_service._TEST_OVERRIDES, "manager_provider", "openrouter")
    monkeypatch.setitem(config_service._TEST_OVERRIDES, "cloud_escalation", "on")
    monkeypatch.setitem(config_service._TEST_OVERRIDES, "local_model_enabled",
                        _settings["local_model_enabled"])
    monkeypatch.setitem(config_service._TEST_OVERRIDES, "openrouter_default_model",
                        "fixture/or-alpha")

    router = ModelRouter(
        local_provider=local_provider, openrouter_provider=openrouter_provider,
        default_openrouter_model="fixture/or-alpha")
    answer = graph_runtime._make_complete_fn(router)(
        turn_id=f"turn-{ai_mode.lower()}-transport", project_id="starter",
        user_request="synthetic fixture prompt", route="knowledge",
        conversation_id="conversation-fixture", context={})

    conn = connect(db_path)
    events = repos.ExecutionEvents.for_turn(conn, f"turn-{ai_mode.lower()}-transport")
    model_event = next(row for row in events if row["event_type"] == "model_completed")
    metadata = json.loads(model_event["metadata_json"])
    conn.close()
    return answer, metadata


def test_auto_openrouter_transport_observer_is_passed_and_persisted(tmp_path, monkeypatch):
    from app.services.llm.base import LLMResponse

    class ObservedOpenRouter:
        name = "openrouter"
        model = "fixture/or-alpha"

        def complete_streaming(self, *, system, messages, tools, opts=None, on_event=None):
            observer = opts["_transport_observer"]
            common = {"provider": "openrouter", "requested_model": self.model,
                      "endpoint_host": "127.0.0.1", "endpoint_path": "/fixture",
                      "http_method": "POST"}
            observer({**common, "state": "provider_request_started",
                      "attempt_index": 1, "network_phase": "transport_dispatch"})
            observer({**common, "state": "provider_response_headers",
                      "attempt_index": 1, "http_status": 200,
                      "network_phase": "http_response"})
            observer({**common, "state": "provider_response_completed",
                      "attempt_index": 1, "actual_model": self.model,
                      "usage": {"input": 2, "output": 3}})
            return LLMResponse("synthetic answer", [], {"actual_model": self.model})

    answer, metadata = _run_turn(
        tmp_path, monkeypatch, ai_mode="AUTO", local_provider=None,
        openrouter_provider=ObservedOpenRouter())

    transport = metadata["transport_telemetry"]
    assert answer == "synthetic answer"
    assert metadata["telemetry"]["provider"] == "openrouter"
    assert transport["provider"] == "openrouter"
    assert transport["requested_model"] == "fixture/or-alpha"
    assert (transport["app_invocation_count"], transport["http_attempt_count"],
            transport["http_response_count"]) == (1, 1, 1)
    assert transport["http_status"] == 200


def test_auto_local_call_is_not_mislabeled_as_openrouter_transport(tmp_path, monkeypatch):
    from app.services.llm.base import LLMResponse

    class FakeLocal:
        name = "local"
        model = "fixture/local-model"

        def complete(self, **_kwargs):
            return LLMResponse("local answer", [], {"provider": "local"})

    class UnusedOpenRouter:
        name = "openrouter"
        model = "fixture/or-alpha"

        def complete(self, **_kwargs):
            raise AssertionError("AUTO-local must not invoke OpenRouter")

    answer, metadata = _run_turn(
        tmp_path, monkeypatch, ai_mode="AUTO", local_provider=FakeLocal(),
        openrouter_provider=UnusedOpenRouter())

    assert answer == "local answer"
    assert metadata["telemetry"]["provider"] == "local"
    assert "transport_telemetry" not in metadata
    assert "http_attempt_count" not in metadata
