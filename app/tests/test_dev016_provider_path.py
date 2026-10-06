"""Offline DEV-016 provider-boundary provenance regressions."""
from types import SimpleNamespace
import json

import pytest


class FakeProvider:
    model = "fake-model-v1"

    def __init__(self, *, fail=False, usage=None):
        self.fail = fail
        self.calls = 0
        self.usage = usage

    def complete(self, **_kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("fixture failure")
        usage = self.usage if self.usage is not None else {
            "input_tokens": 7, "output_tokens": 4, "total_tokens": 11}
        return SimpleNamespace(text="FAKE_GENERATED_ANSWER", usage=usage)


def test_fake_provider_records_one_authoritative_model_call(tmp_path):
    from app.database.sqlite import connect
    from app.database import repos
    from app.services.llm.model_router import ModelRouter

    conn = connect(tmp_path / "provider-success.db")
    fake = FakeProvider()
    router = ModelRouter(local_provider=fake, default_local_model="fake-model-v1")
    response, call = router.complete(
        conn, turn_id="turn-success", project_id="starter", system="",
        messages=[], tools=[], mode="LOCAL", call_id="call-success")
    rows = repos.ModelCalls.for_turn(conn, "turn-success", "starter")
    assert response.text == "FAKE_GENERATED_ANSWER"
    assert fake.calls == 1
    assert len(rows) == 1
    assert rows[0]["call_id"] == call.call_id == "call-success"
    assert rows[0]["provider"] == "local"
    assert rows[0]["model"] == "fake-model-v1"
    assert rows[0]["input_tokens"] == 7
    assert rows[0]["output_tokens"] == 4
    conn.close()


def test_provider_exception_has_truthful_invocation_identity(tmp_path):
    from app.database.sqlite import connect
    from app.services.llm.model_router import ModelCallFailure, ModelRouter

    conn = connect(tmp_path / "provider-failure.db")
    fake = FakeProvider(fail=True)
    router = ModelRouter(local_provider=fake, default_local_model="fake-model-v1")
    with pytest.raises(ModelCallFailure) as caught:
        router.complete(conn, turn_id="turn-failure", project_id="starter",
                        system="", messages=[], tools=[], mode="LOCAL",
                        call_id="call-failure")
    error = caught.value
    assert error.invocation_started is True
    assert error.provider == "local"
    assert error.model == "fake-model-v1"
    assert fake.calls == 1
    assert conn.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 0
    conn.close()


def test_unavailable_provider_fails_before_invocation(tmp_path):
    from app.database.sqlite import connect
    from app.services.llm.model_router import ModelCallFailure, ModelRouter

    conn = connect(tmp_path / "provider-unavailable.db")
    router = ModelRouter(default_local_model="fake-model-v1")
    with pytest.raises(ModelCallFailure) as caught:
        router.complete(conn, turn_id="turn-unavailable", project_id="starter",
                        system="", messages=[], tools=[], mode="LOCAL",
                        call_id="call-unavailable")
    assert caught.value.invocation_started is False
    assert caught.value.provider == "local"
    assert caught.value.model == "fake-model-v1"
    assert conn.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 0
    conn.close()


def _knowledge_graph(conn, complete_fn):
    pytest.importorskip("langgraph", reason="offline environment lacks graph runtime dependency")
    from app.graphs.account_manager_graph import build_account_manager_graph
    from app.graphs.state import initial_state

    graph = build_account_manager_graph(
        known_projects={"dev016-synthetic"},
        conn_factory=lambda: conn,
        complete_fn=complete_fn,
    )
    state = initial_state(
        project_id="dev016-synthetic", conversation_id="dev016-conversation",
        turn_id="dev016-turn", user_request=(
            "Why is the renewal window thirty days under ORANGE-POLICY-927?"),
    )
    return graph.invoke(state, config={"configurable": {"thread_id": "dev016-thread"}})


def test_full_knowledge_graph_uses_fake_answer_and_aligned_call_telemetry(tmp_path):
    from app.contracts.events import GraphExecutionEvent
    from app.database import repos
    from app.database.sqlite import connect
    from app.services.llm.model_router import ModelRouter

    conn = connect(tmp_path / "knowledge-graph.db")
    fake = FakeProvider()
    router = ModelRouter(local_provider=fake, default_local_model="fake-model-v1")

    def complete_fn(*, turn_id, project_id, user_request, route, conversation_id, context):
        response, call = router.complete(
            conn, turn_id=turn_id, project_id=project_id, system="",
            messages=[{"role": "user", "content": user_request}], tools=[],
            mode="LOCAL", call_id="dev016-call-1")
        repos.ExecutionEvents.insert(conn, GraphExecutionEvent(
            project_id=project_id, conversation_id=conversation_id, turn_id=turn_id,
            event_type="model_completed", detail=response.text,
            metadata={"call_id": call.call_id, "route": route,
                      "generation_source": "provider",
                      "generation_status": "completed",
                      "telemetry": call.model_dump()},
        ).to_row())
        return response.text

    result = _knowledge_graph(conn, complete_fn)
    rows = repos.ModelCalls.for_turn(conn, "dev016-turn", "dev016-synthetic")
    events = repos.ExecutionEvents.for_turn(conn, "dev016-turn")
    model_event = next(row for row in events if row["event_type"] == "model_completed")
    event_meta = json.loads(model_event["metadata_json"])
    sse = GraphExecutionEvent.from_row(model_event).to_sse(id=model_event["id"])
    assert result["route"] == "knowledge"
    assert result["final_answer"] == "FAKE_GENERATED_ANSWER"
    assert result["model_usage"] == {"generation_source": "provider",
                                     "generation_status": "completed"}
    assert fake.calls == 1
    assert len(rows) == 1
    assert rows[0]["call_id"] == "dev016-call-1"
    assert event_meta["call_id"] == rows[0]["call_id"]
    assert event_meta["telemetry"]["provider"] == rows[0]["provider"] == "local"
    assert event_meta["telemetry"]["model"] == rows[0]["model"] == "fake-model-v1"
    assert event_meta["route"] == "knowledge"
    assert rows[0]["turn_id"] == model_event["turn_id"] == "dev016-turn"
    assert rows[0]["project_id"] == model_event["project_id"] == "dev016-synthetic"
    conn.close()


@pytest.mark.parametrize("provider", [FakeProvider(fail=True), None], ids=["failure", "unavailable"])
def test_full_knowledge_graph_never_returns_branch_template_on_provider_failure(tmp_path, provider):
    from app.database import repos
    from app.database.sqlite import connect
    from app.services.llm.model_router import ModelCallFailure, ModelRouter

    conn = connect(tmp_path / f"knowledge-{id(provider)}.db")
    router = ModelRouter(local_provider=provider, default_local_model="fake-model-v1")

    def complete_fn(*, turn_id, project_id, user_request, route, conversation_id, context):
        try:
            response, _ = router.complete(
                conn, turn_id=turn_id, project_id=project_id, system="",
                messages=[], tools=[], mode="LOCAL", call_id="dev016-call-fail")
            return response.text
        except ModelCallFailure as exc:
            raise exc

    result = _knowledge_graph(conn, complete_fn)
    assert result["route"] == "knowledge"
    assert result["final_answer"] == (
        "I couldn't generate an answer for this turn. Please retry when the selected provider is available.")
    assert "recorded knowledge" not in result["final_answer"]
    assert result["model_usage"]["generation_source"] == "none"
    assert result["model_usage"]["generation_status"] == "failed"
    assert result["model_usage"]["invocation_started"] is (provider is not None)
    assert repos.ModelCalls.for_turn(conn, "dev016-turn", "dev016-synthetic") == []
    conn.close()


def _configure_offline_graph_route(monkeypatch, db_path):
    from app import deps
    from app.services.config_service import ConfigService

    monkeypatch.setattr(deps, "DB_PATH", db_path)
    monkeypatch.setattr(ConfigService, "get_setting", classmethod(
        lambda cls, key, default=None, conn=None:
        "LOCAL" if key == "ai_mode" else default))
    monkeypatch.setattr(ConfigService, "is_openai_configured", classmethod(
        lambda cls, conn=None: False))
    monkeypatch.setattr(ConfigService, "is_openrouter_configured", classmethod(
        lambda cls, conn=None: False))


def test_production_completion_callback_persists_and_projects_unknown_usage(tmp_path, monkeypatch):
    from app.contracts.events import GraphExecutionEvent
    from app.database import repos
    from app.database.sqlite import connect
    from app.routes.graph_runtime import _make_complete_fn, _visibility
    from app.services.llm.model_router import ModelRouter

    db_path = tmp_path / "runtime-success.db"
    _configure_offline_graph_route(monkeypatch, db_path)
    fake = FakeProvider(usage={})
    complete = _make_complete_fn(ModelRouter(
        local_provider=fake, default_local_model="fake-model-v1"))
    answer = complete(
        turn_id="runtime-turn-success", project_id="runtime-project-success",
        user_request="Why is the renewal window 30 days?", route="knowledge",
        conversation_id="runtime-conversation-success", context={})

    conn = connect(db_path)
    rows = repos.ModelCalls.for_turn(
        conn, "runtime-turn-success", "runtime-project-success")
    events = repos.ExecutionEvents.for_turn(conn, "runtime-turn-success")
    model_event = next(row for row in events if row["event_type"] == "model_completed")
    event_meta = json.loads(model_event["metadata_json"])
    sse = GraphExecutionEvent.from_row(model_event).to_sse(id=model_event["id"])
    visibility = _visibility(conn, "runtime-turn-success", "runtime-project-success")
    assert answer == "FAKE_GENERATED_ANSWER"
    assert fake.calls == 1
    assert len(rows) == 1
    row = rows[0]
    assert event_meta["call_id"] == row["call_id"]
    assert event_meta["telemetry"]["call_id"] == row["call_id"]
    assert sse["event"] == "model_completed"
    assert sse["data"]["turn_id"] == row["turn_id"]
    assert sse["data"]["call_id"] == row["call_id"]
    assert sse["data"]["telemetry"]["project_id"] == row["project_id"]
    assert sse["data"]["telemetry"]["input_tokens"] is None
    assert sse["data"]["telemetry"]["output_tokens"] is None
    assert sse["data"]["telemetry"]["total_tokens"] is None
    assert row["turn_id"] == model_event["turn_id"] == "runtime-turn-success"
    assert row["project_id"] == model_event["project_id"] == "runtime-project-success"
    assert visibility["generation_source"] == "provider"
    assert visibility["generation_status"] == "completed"
    assert visibility["generation_attempt"]["call_id"] == row["call_id"]
    assert visibility["generation_attempt"]["provider"] == row["provider"] == "local"
    assert visibility["generation_attempt"]["actual_model"] == row["model"]
    assert visibility["calls"][0]["input_tokens"] is None
    assert visibility["calls"][0]["output_tokens"] is None
    assert visibility["calls"][0]["total_tokens"] is None
    assert visibility["calls"][0]["estimated_cost_usd"] == 0.0
    conn.close()


@pytest.mark.parametrize("provider, invocation_started", [
    (FakeProvider(fail=True), True),
    (None, False),
], ids=["provider-error", "unavailable-before-call"])
def test_production_completion_callback_persists_and_projects_failure(
        tmp_path, monkeypatch, provider, invocation_started):
    from app.database import repos
    from app.database.sqlite import connect
    from app.routes.graph_runtime import _make_complete_fn, _visibility
    from app.services.llm.model_router import ModelCallFailure, ModelRouter

    db_path = tmp_path / f"runtime-failure-{invocation_started}.db"
    _configure_offline_graph_route(monkeypatch, db_path)
    fake_calls = provider.calls if provider is not None else 0
    complete = _make_complete_fn(ModelRouter(
        local_provider=provider, default_local_model="fake-model-v1"))
    with pytest.raises(ModelCallFailure):
        complete(
            turn_id="runtime-turn-failure", project_id="runtime-project-failure",
            user_request="Why is the renewal window 30 days?", route="knowledge",
            conversation_id="runtime-conversation-failure", context={})

    conn = connect(db_path)
    events = repos.ExecutionEvents.for_turn(conn, "runtime-turn-failure")
    failed = next(row for row in events
                  if row["event_type"] == "synthesis_completed")
    metadata = json.loads(failed["metadata_json"])
    from app.contracts.events import GraphExecutionEvent
    sse = GraphExecutionEvent.from_row(failed).to_sse(id=failed["id"])
    visibility = _visibility(conn, "runtime-turn-failure", "runtime-project-failure")
    assert metadata["generation_source"] == "none"
    assert metadata["generation_status"] == "failed"
    assert metadata["invocation_started"] is invocation_started
    assert metadata["call_id"]
    assert metadata["provider"] == "local"
    assert sse["event"] == "synthesis_completed"
    assert sse["data"]["meta"]["generation_source"] == "none"
    assert sse["data"]["meta"]["call_id"] == metadata["call_id"]
    assert sse["data"]["meta"]["provider"] == "local"
    assert sse["data"]["meta"]["invocation_started"] is invocation_started
    assert repos.ModelCalls.for_turn(
        conn, "runtime-turn-failure", "runtime-project-failure") == []
    assert visibility["provider"] == "local"
    assert visibility["model"] == ""
    assert visibility["generation_source"] == "none"
    assert visibility["generation_status"] == "failed"
    assert visibility["generation_attempt"]["invocation_started"] is invocation_started
    assert visibility["generation_attempt"]["call_id"] == metadata["call_id"]
    if provider is not None:
        assert provider.calls == fake_calls + 1
    conn.close()
