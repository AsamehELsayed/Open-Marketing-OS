"""DEV-023 focused event metadata durability regressions (offline only)."""
import json

from app.contracts.events import GraphExecutionEvent, project_event_boundary


def test_compact_retrieval_telemetry_survives_oversized_event_metadata():
    event = GraphExecutionEvent(
        project_id="project-safe",
        conversation_id="conversation-safe",
        turn_id="turn-safe",
        event_type="model_completed",
        metadata={
            "turn_id": "turn-safe",
            "text": "answer " * 3000,
            "telemetry": {"provider": "offline", "usage": {"total_tokens": 17}},
            "retrieval_telemetry": {
                "retrieval_mode": "hybrid",
                "lexical_hits": 0,
                "vector_hits": 1,
                "fused_hits": 1,
                "selected_chunk_ids": ["chunk-1"],
                "source_file_ids": ["file-1"],
            },
        },
    )

    row = event.to_row()
    assert len(row["metadata_json"]) <= 4000
    stored = json.loads(row["metadata_json"])
    assert stored["retrieval_telemetry"] == {
        "retrieval_mode": "hybrid",
        "lexical_hits": 0,
        "vector_hits": 1,
        "fused_hits": 1,
        "selected_chunk_ids": ["chunk-1"],
        "source_file_ids": ["file-1"],
    }
    assert stored["telemetry_truncated"] is True

    replayed = GraphExecutionEvent.from_row(row)
    wire = replayed.to_sse(id=19)
    assert wire["data"]["meta"]["retrieval_telemetry"] == stored["retrieval_telemetry"]
    assert wire["data"]["meta"]["telemetry_truncated"] is True


def test_malformed_legacy_metadata_row_is_marked_truncated():
    event = GraphExecutionEvent.from_row({
        "project_id": "project-safe",
        "event_type": "model_completed",
        "metadata_json": '{"retrieval_telemetry":{"vector_hits":1}'[:20],
    })
    assert event.metadata == {"telemetry_truncated": True}
    assert event.to_sse(id=1)["data"]["meta"]["telemetry_truncated"] is True
    assert project_event_boundary({
        "event_type": "model_completed",
        "metadata_json": '{"retrieval_telemetry":{"vector_hits":1}'[:20],
    })["meta"]["telemetry_truncated"] is True


def test_unknown_retrieval_counters_stay_unknown():
    event = GraphExecutionEvent(
        project_id="project-safe",
        event_type="model_completed",
        metadata={"retrieval_telemetry": {
            "retrieval_mode": "hybrid",
            "vector_hits": None,
            "selected_chunk_ids": ["chunk-1"],
        }},
    )
    replayed = GraphExecutionEvent.from_row(event.to_row())
    telemetry = replayed.to_sse(id=1)["data"]["meta"]["retrieval_telemetry"]
    assert telemetry == {
        "retrieval_mode": "hybrid",
        "selected_chunk_ids": ["chunk-1"],
    }
