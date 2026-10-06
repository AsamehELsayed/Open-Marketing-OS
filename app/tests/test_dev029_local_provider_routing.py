from __future__ import annotations

from types import SimpleNamespace

from app.services import config_service
from app.services.config_service import ConfigService
from app.services.llm.base import LLMResponse
from app.services.llm import model_router


class FakeProvider:
    def __init__(self, name, model):
        self.name = name
        self.model = model
        self.calls = 0

    def complete(self, **_kwargs):
        self.calls += 1
        return LLMResponse("synthetic local answer", [], {
            "provider": self.name, "model": self.model,
            "input_tokens": 2, "output_tokens": 3, "total_tokens": 5,
        })


def _setting(monkeypatch, key, value):
    monkeypatch.setitem(config_service._TEST_OVERRIDES, key, value)


def test_auto_falls_back_to_connected_openrouter_if_local_disabled(monkeypatch):
    _setting(monkeypatch, "local_model_enabled", "off")
    _setting(monkeypatch, "manager_provider", "openrouter")
    _setting(monkeypatch, "cloud_escalation", "on")
    _setting(monkeypatch, "openrouter_default_model", "fixture/openrouter")
    monkeypatch.setattr(model_router, "openai_configured", lambda: False)
    monkeypatch.setattr(model_router, "openrouter_configured_default", lambda: True)

    local = FakeProvider("local", "fixture-local")
    router = model_router.ModelRouter(
        local_provider=local, openrouter_provider=FakeProvider("openrouter", "fixture/openrouter"))
    route = router._route_decision("AUTO", None)

    assert route.provider == "openrouter"
    assert route.reason == "local unavailable"


def test_auto_falls_back_to_openrouter_if_managed_local_is_unavailable(monkeypatch):
    _setting(monkeypatch, "local_model_enabled", "on")
    _setting(monkeypatch, "manager_provider", "openrouter")
    _setting(monkeypatch, "cloud_escalation", "on")
    monkeypatch.setattr(model_router, "openai_configured", lambda: False)
    monkeypatch.setattr(model_router, "openrouter_configured_default", lambda: True)

    router = model_router.ModelRouter(openrouter_provider=FakeProvider("openrouter", "fixture/openrouter"))
    route = router._route_decision("AUTO", None)

    assert route.provider == "openrouter"
    assert route.reason == "local unavailable"


def test_auto_uses_selected_openrouter_when_both_cloud_providers_are_connected(monkeypatch):
    _setting(monkeypatch, "local_model_enabled", "off")
    _setting(monkeypatch, "manager_provider", "auto")
    _setting(monkeypatch, "cloud_escalation", "on")
    _setting(monkeypatch, "openrouter_default_model", "fixture/selected-model")
    monkeypatch.setattr(model_router, "openai_configured", lambda: True)
    monkeypatch.setattr(model_router, "openrouter_configured_default", lambda: True)

    router = model_router.ModelRouter(openrouter_provider=FakeProvider("openrouter", "fixture/selected-model"))
    route = router._route_decision("AUTO", None)

    assert route.provider == "openrouter"
    assert route.model == "fixture/selected-model"


def test_auto_local_turn_uses_only_local_provider_when_cloud_escalation_off(
        tmp_path, monkeypatch):
    from app.database.sqlite import connect

    _setting(monkeypatch, "local_model_enabled", "on")
    _setting(monkeypatch, "manager_provider", "auto")
    _setting(monkeypatch, "cloud_escalation", "off")
    monkeypatch.setattr(model_router, "openai_configured", lambda: False)
    monkeypatch.setattr(model_router, "openrouter_configured_default", lambda: True)
    local = FakeProvider("local", "fixture-local")
    cloud = FakeProvider("openrouter", "fixture-cloud")
    conn = connect(tmp_path / "dev029-local-routing.db")
    try:
        router = model_router.ModelRouter(local_provider=local, openrouter_provider=cloud)
        response, call = router.complete(
            conn, turn_id="turn-local-fixture", project_id="starter", system="",
            messages=[{"role": "user", "content": "fixture prompt"}], tools=[],
            mode="AUTO", call_id="call-local-fixture")
        assert response.text == "synthetic local answer"
        assert call.provider == "local"
        assert local.calls == 1
        assert cloud.calls == 0
    finally:
        conn.close()


def test_explicit_local_fails_before_invocation_when_managed_provider_is_absent(
        tmp_path, monkeypatch):
    import pytest
    from app.database.sqlite import connect
    from app.services.llm.model_router import ModelCallFailure

    _setting(monkeypatch, "local_model_enabled", "on")
    conn = connect(tmp_path / "dev029-local-unavailable.db")
    try:
        router = model_router.ModelRouter()
        with pytest.raises(ModelCallFailure) as caught:
            router.complete(conn, turn_id="turn-no-local", project_id="starter",
                            system="", messages=[], tools=[], mode="LOCAL",
                            call_id="call-no-local")
        assert caught.value.provider == "local"
        assert caught.value.invocation_started is False
    finally:
        conn.close()


def test_graph_wires_local_only_when_managed_status_is_verified_ready(monkeypatch):
    from app.routes import graph_runtime

    class Manager:
        base_url = "http://127.0.0.1:8181"
        catalog = SimpleNamespace(model=SimpleNamespace(quantization="Q4_K_M"))

        def __init__(self, ready):
            self.ready = ready

        def rediscover(self):
            return {
                "ready": self.ready,
                "verification_state": "verified" if self.ready else "not_verified",
                "activation_state": "active" if self.ready else "inactive",
                "expected_model_id": "fixture-model",
                "observed_model_id": "fixture-model" if self.ready else None,
            }

    captured = []
    monkeypatch.setattr(model_router, "get_managed_local_runtime_manager", lambda: Manager(True))
    _setting(monkeypatch, "local_model_enabled", "on")
    monkeypatch.setattr(graph_runtime, "_make_model_router",
                        lambda *, local_provider, local_model: captured.append(
                            (local_provider, local_model)) or SimpleNamespace(
                                local_provider=local_provider, local_model=local_model))

    router = graph_runtime._get_model_router()

    assert router.local_provider is not None
    assert router.local_provider.config.base_url == "http://127.0.0.1:8181"
    assert router.local_provider.config.model == "fixture-model"
    assert router.local_model == "fixture-model"


def test_graph_does_not_wire_configured_but_unready_local(monkeypatch):
    from app.routes import graph_runtime

    manager = SimpleNamespace(
        base_url="http://127.0.0.1:8181",
        catalog=SimpleNamespace(model=SimpleNamespace(quantization="Q4_K_M")),
        rediscover=lambda: {"ready": False, "verification_state": "verified",
                            "activation_state": "active", "expected_model_id": "fixture-model",
                            "observed_model_id": None})
    monkeypatch.setattr(model_router, "get_managed_local_runtime_manager", lambda: manager)
    _setting(monkeypatch, "local_model_enabled", "on")
    captured = []
    monkeypatch.setattr(graph_runtime, "_make_model_router",
                        lambda *, local_provider, local_model: captured.append(
                            (local_provider, local_model)) or SimpleNamespace(
                                local_provider=local_provider, local_model=local_model))

    router = graph_runtime._get_model_router()

    assert router.local_provider is None
    assert router.local_model is None


def test_graph_does_not_wire_local_when_disabled(monkeypatch):
    from app.routes import graph_runtime

    manager = SimpleNamespace(
        base_url="http://127.0.0.1:8181",
        catalog=SimpleNamespace(model=SimpleNamespace(quantization="Q4_K_M")),
        rediscover=lambda: {"ready": True, "verification_state": "verified",
                            "activation_state": "active", "expected_model_id": "fixture-model",
                            "observed_model_id": "fixture-model"})
    monkeypatch.setattr(model_router, "get_managed_local_runtime_manager", lambda: manager)
    _setting(monkeypatch, "local_model_enabled", "off")
    monkeypatch.setattr(graph_runtime, "_make_model_router",
                        lambda *, local_provider, local_model: SimpleNamespace(
                            local_provider=local_provider, local_model=local_model))

    router = graph_runtime._get_model_router()

    assert router.local_provider is None
    assert router.local_model is None
