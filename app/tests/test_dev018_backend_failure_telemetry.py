"""DEV-018 backend failed-attempt and per-turn retrieval telemetry tests."""
import json


def _row(kind, metadata):
    return {"event_type": kind, "metadata_json": json.dumps(metadata),
            "label": "", "detail": ""}


def test_failed_attempt_identity_survives_without_model_call_row(monkeypatch):
    from app.routes import graph_runtime

    events = [_row("synthesis_completed", {
        "provider": "openrouter", "requested_model": "stealth/space-bunny-alpha",
        "route_mode": "OPENROUTER", "route_reason": "explicit user selection",
        "generation_source": "none", "generation_status": "failed",
        "invocation_started": True, "error_type": "ModelCallFailure",
        "attempt_latency_ms": 842, "http_status": 401,
        "failure_code": "authentication",
        "failure_reason": "The provider rejected the credential.",
    })]
    monkeypatch.setattr(graph_runtime, "_events_for", lambda *_: events)
    monkeypatch.setattr(graph_runtime.repos.ModelCalls, "for_turn", lambda *_: [])
    monkeypatch.setattr(graph_runtime.repos.ModelCalls, "turn_totals", lambda *_: {})

    visible = graph_runtime._visibility(object(), "turn-a", "project-a", "knowledge")

    assert visible["provider"] == "openrouter"
    assert visible["model"] == ""  # requested model is not an actual model
    assert visible["route_mode"] == "OPENROUTER"
    assert visible["route_reason"] == "explicit user selection"
    assert visible["generation_attempt"]["requested_model"] == "stealth/space-bunny-alpha"
    assert visible["generation_attempt"]["http_status"] == 401
    assert visible["generation_attempt"]["attempt_latency_ms"] == 842
    assert visible["generation_attempt"]["failure_code"] == "authentication"
    assert visible["generation_attempt"]["failure_reason"] == "The provider rejected the credential."


def test_model_router_projects_adapter_failure_without_exception_text():
    from app.services.llm.model_router import _provider_failure_details

    class AdapterFailure(Exception):
        failure_kind = "rate_limit"
        status = 429

        def __str__(self):
            return "Bearer should-never-be-copied"

    assert _provider_failure_details(AdapterFailure()) == (
        "rate_limit", 429, "The provider rate limit was reached.")


def test_failed_generation_keeps_citation_evidence_snapshot():
    from app.routes.graph_runtime import _citations_for_result

    citation = {"source": "project_rag", "file_id": "synthetic-file",
                "chunk_id": "c000", "scope": "project"}
    assert _citations_for_result({
        "errors": ["provider failed"], "citation_evidence": [citation]},
        project_id="project-a") == [citation]


def test_retrieval_telemetry_round_trips_without_evidence_body():
    from app.contracts.events import GraphExecutionEvent
    from app.routes.graph_runtime import _retrieval_telemetry_projection

    telemetry = {
        "mode": "HYBRID", "search_mode": "HYBRID", "lexical_hits": 4,
        "vector_hits": 3, "fused_hits": 5,
        "selected_chunks": [
            {"chunk_id": "c000", "file_id": "file-a"},
            {"chunk_id": "c001", "file_id": "file-b"},
        ],
        "text": "PRIVATE RETRIEVED BODY", "chain_of_thought": "PRIVATE REASONING",
    }
    from app.routes.graph_runtime import _safe_retrieval_telemetry
    evt = GraphExecutionEvent(
        project_id="project-a", conversation_id="conversation-a", turn_id="turn-a",
        event_type="synthesis_completed", metadata={
            "retrieval_telemetry": _safe_retrieval_telemetry(telemetry)},
    )
    row = evt.to_row()
    row["event_type"] = evt.event_type
    projected = _retrieval_telemetry_projection([row])

    assert projected["mode"] == "HYBRID"
    assert (projected["lexical_hits"], projected["vector_hits"],
            projected["fused_hits"]) == (4, 3, 5)
    assert projected["selected_chunks"] == telemetry["selected_chunks"]
    serialized = row["metadata_json"]
    assert "PRIVATE RETRIEVED BODY" not in serialized
    assert "PRIVATE REASONING" not in serialized


def test_retrieval_projection_is_bound_to_the_exact_turn_event():
    from app.routes.graph_runtime import _retrieval_telemetry_projection

    first = _row("synthesis_completed", {"retrieval_telemetry": {
        "mode": "HYBRID", "lexical_hits": 1, "vector_hits": 2,
        "fused_hits": 2, "selected_chunks": [{"chunk_id": "turn-one"}],
    }})
    second = _row("synthesis_completed", {"generation_status": "failed"})
    assert _retrieval_telemetry_projection([first, second])["selected_chunks"][0]["chunk_id"] == "turn-one"
    assert _retrieval_telemetry_projection([second]) == {}


def test_knowledge_graph_passes_exact_retrieval_counts_to_completion(tmp_path, monkeypatch):
    import pytest
    from types import SimpleNamespace

    pytest.importorskip("langgraph", reason="graph runtime dependency unavailable")
    from app.database.sqlite import connect
    from app.graphs.adapters import RetrievalAdapter
    from app.graphs.account_manager_graph import build_account_manager_graph
    from app.graphs.state import initial_state

    conn = connect(tmp_path / "retrieval-counts.db")
    monkeypatch.setattr(RetrievalAdapter, "retrieve", lambda *_: SimpleNamespace(
        mode="HYBRID", search_mode="HYBRID", lexical_hits=4, vector_hits=3,
        fused_hits=5, hits=[{
            "path": "project-files/file-a", "chunk_id": "c000",
            "text": "test evidence", "score": 0.9,
            "sources": ["fts", "semantic"], "project_id": "dev018-synthetic",
            "file_id": "file-a",
        }]))
    contexts = []
    graph = build_account_manager_graph(
        known_projects={"dev018-synthetic"}, conn_factory=lambda: conn,
        complete_fn=lambda **kwargs: (contexts.append(kwargs["context"]) or "fake answer"))
    result = graph.invoke(initial_state(
        project_id="dev018-synthetic", conversation_id="dev018-conversation",
        turn_id="dev018-turn", user_request="What is in the project evidence?"),
        config={"configurable": {"thread_id": "dev018-thread"}})
    telemetry = result["retrieval_telemetry"]
    assert telemetry["retrieval_mode"] == "HYBRID"
    assert (telemetry["lexical_hits"], telemetry["vector_hits"],
            telemetry["fused_hits"]) == (4, 3, 5)
    assert telemetry["selected_chunk_ids"] == ["c000"]
    assert telemetry["source_file_ids"] == ["file-a"]
    assert contexts[0]["retrieval_telemetry"] == telemetry
    assert result["final_answer"] == "fake answer"
    conn.close()
