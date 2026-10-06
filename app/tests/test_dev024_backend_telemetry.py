from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import deps
from app.contracts.events import GraphExecutionEvent
from app.database import repos
from app.database.sqlite import connect
from app.graphs.adapters import RetrievalAdapter
from app.main import create_app
from app.services import state as store
from app.services.rag.indexing_service import IndexingService


def _sse_events(body: str) -> dict[str, dict]:
    events: dict[str, dict] = {}
    event_type = ""
    data = []
    for line in body.splitlines() + [""]:
        if line.startswith("event: "):
            event_type = line[7:]
        elif line.startswith("data: ") and event_type:
            data.append(line[6:])
        elif not line and event_type:
            events[event_type] = json.loads("\n".join(data) or "{}")
            event_type, data = "", []
    return events


def test_oversized_model_completed_metadata_is_compact_valid_json_and_roundtrips():
    event = GraphExecutionEvent(
        project_id="starter", conversation_id="c1", turn_id="t1",
        event_type="model_completed", detail="final answer",
        metadata={
            "turn_id": "t1", "call_id": "mc-1", "route": "knowledge",
            "text": "long response " * 1200,
            "telemetry": {
                "provider": "openrouter", "requested_model": "vendor/requested",
                "actual_model": "vendor/actual", "attempts": 1,
                "status_code": 200, "latency_ms": 26708,
                "input_tokens": 755, "output_tokens": 1102,
                "diagnostic": "extra " * 600,
            },
            "retrieval_telemetry": {
                "retrieval_mode": "HYBRID", "lexical_hits": 2,
                "vector_hits": 3, "fused_hits": 4,
                "selected_chunk_ids": ["c000", "c001"],
                "source_file_ids": ["doc-synthetic-001"],
            },
        },
    )

    row = event.to_row()
    encoded = row["metadata_json"]
    decoded = json.loads(encoded)
    assert len(encoded.encode("utf-8")) <= 4000
    assert decoded["telemetry_truncated"] is True
    assert decoded["telemetry"] == {
        "provider": "openrouter", "requested_model": "vendor/requested",
        "actual_model": "vendor/actual", "attempts": 1,
        "status_code": 200, "latency_ms": 26708,
        "input_tokens": 755, "output_tokens": 1102,
    }
    assert decoded["retrieval_telemetry"]["retrieval_mode"] == "HYBRID"
    assert decoded["retrieval_telemetry"]["selected_chunk_ids"] == ["c000", "c001"]
    assert "text" not in decoded

    sse = GraphExecutionEvent.from_row({**row, "id": 42}).to_sse(42)
    assert sse["data"]["telemetry"]["latency_ms"] == 26708
    assert sse["data"]["retrieval_telemetry"]["source_file_ids"] == [
        "doc-synthetic-001"]


def test_missing_generation_fields_remain_missing_in_historical_projection():
    # Sanitized replay packet from DEV-024's preserved run facts. Attempts and
    # HTTP status are present in the supplied packet; token/model details that
    # were not retained in this fixture remain absent rather than inferred.
    event = GraphExecutionEvent(
        project_id="starter", turn_id="historical-turn",
        event_type="model_completed", metadata={
            "turn_id": "historical-turn", "call_id": "historical-call",
            "telemetry": {
                "provider": "openrouter", "attempts": 1,
                "status_code": 200, "latency_ms": 26708,
            },
            "retrieval_telemetry": {
                "retrieval_mode": "HYBRID", "selected_chunk_ids": ["c000"],
            },
        },
    )
    row = event.to_row()
    wire = GraphExecutionEvent.from_row({**row, "id": 3}).to_sse(3)
    assert wire["data"]["telemetry"] == {
        "provider": "openrouter", "attempts": 1,
        "status_code": 200, "latency_ms": 26708,
    }
    assert "requested_model" not in wire["data"]["telemetry"]
    assert "actual_model" not in wire["data"]["telemetry"]
    assert "lexical_hits" not in wire["data"]["retrieval_telemetry"]
    assert "vector_hits" not in wire["data"]["retrieval_telemetry"]


def test_generation_telemetry_copies_only_present_response_fields():
    from app.routes.graph_runtime import _generation_telemetry

    class Call:
        def model_dump(self):
            return {"provider": "openrouter", "model": "vendor/resolved",
                    "requested_model": "vendor/requested", "latency_ms": 27}

    projected = _generation_telemetry(
        SimpleNamespace(usage={"status_code": 200, "attempts": 1}), Call())
    assert projected["actual_model"] == "vendor/resolved"
    assert projected["requested_model"] == "vendor/requested"
    assert projected["status_code"] == 200
    assert projected["attempts"] == 1
    assert projected["latency_ms"] == 27
    assert "input_tokens" not in projected
    assert "status" not in projected


def test_retrieval_counts_derive_from_real_fused_source_membership():
    from app.graphs.account_manager_graph import _retrieval_telemetry_from_result

    result = SimpleNamespace(mode="HYBRID", hits=[
        {"path": "knowledge.md", "chunk_id": "c0", "sources": ["fts", "semantic"]},
        {"path": "knowledge.md", "chunk_id": "c1", "sources": ["fts"]},
        {"path": "offer.md", "chunk_id": "c2", "sources": ["semantic"]},
    ])
    projected = _retrieval_telemetry_from_result(result, [
        {"path": "knowledge.md", "chunk_id": "c0"},
        {"path": "knowledge.md", "chunk_id": "c1"},
    ], source_file_ids=["doc-knowledge"])
    assert projected == {
        "retrieval_mode": "HYBRID", "lexical_hits": 2, "vector_hits": 2,
        "fused_hits": 3, "selected_chunk_ids": ["c0", "c1"],
        "source_file_ids": ["doc-knowledge"],
    }


def test_offline_hybrid_retrieval_projects_synthetic_business_knowledge(tmp_path):
    """Exercise SQLite FTS + deterministic fake vector leg; no Chroma/provider."""
    from app.graphs.account_manager_graph import _retrieval_telemetry_from_result

    workspace = tmp_path / "workspace"
    knowledge = workspace / "knowledge" / "synthetic-business-knowledge.md"
    knowledge.parent.mkdir(parents=True)
    knowledge.write_text(
        "# Business model\n\nNJM builds conversion-focused marketing websites.\n",
        encoding="utf-8",
    )
    conn = connect(tmp_path / "hybrid.db")
    try:
        IndexingService(conn, workspace).build_or_update()
        row = conn.execute(
            "SELECT d.id, d.path, d.file_sha, c.chunk_id FROM documents d JOIN chunks c "
            "ON c.document_id = d.id WHERE d.project_id = ? LIMIT 1",
            ("starter",),
        ).fetchone()
        assert row is not None

        class OfflineEmbeddings:
            semantic = True

            def embed_query(self, _query):
                return [0.0, 1.0]

        class FakeChroma:
            mode = "HYBRID"

            def query(self, _vector, k=6, where=None):
                    return [(f"{row['id']}:{row['chunk_id']}", {
                    "path": row["path"], "chunk_id": row["chunk_id"],
                        "project_id": "starter", "file_sha": row["file_sha"],
                }, 0.0)][:k]

        result = RetrievalAdapter(
            conn, "starter", store=FakeChroma(), provider=OfflineEmbeddings()
        ).retrieve("conversion-focused marketing websites")
        selected = [hit.model_dump() for hit in result.hits[:5]]
        telemetry = _retrieval_telemetry_from_result(
            result, selected, source_file_ids=[row["id"]])
        assert telemetry == {
            "retrieval_mode": "HYBRID", "lexical_hits": 1,
            "vector_hits": 1, "fused_hits": 1,
            "selected_chunk_ids": [row["chunk_id"]],
            "source_file_ids": [row["id"]],
        }
    finally:
        conn.close()


def test_persisted_model_completion_replays_through_graph_sse(tmp_path, monkeypatch):
    db = tmp_path / "dev024.db"
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setattr(deps, "ROOT", root)
    monkeypatch.setenv("AI_RUNTIME", "langgraph")
    deps.init_db(db)
    with deps.get_db(db) as conn:
        conversation = store.create_conversation(conn, project_id="starter")
        turn = "dev024-replay-turn"
        repos.Turns.upsert(conn, {
            "id": turn, "conversation_id": conversation["id"],
            "project_id": "starter", "status": "completed",
            "created_at": "2026-10-03T00:00:00+00:00",
        })
        event = GraphExecutionEvent(
            project_id="starter", conversation_id=conversation["id"],
            turn_id=turn, event_type="model_completed", detail="Replay answer",
            metadata={
                "turn_id": turn, "call_id": "replay-call",
                "telemetry": {"provider": "openrouter", "model": "stealth/space-bunny-alpha",
                              "attempts": 1,
                              "status_code": 200, "latency_ms": 26708},
                "retrieval_telemetry": {
                    "retrieval_mode": "HYBRID", "lexical_hits": 1,
                    "vector_hits": 1, "fused_hits": 1,
                    "selected_chunk_ids": ["c000"],
                    "source_file_ids": ["doc-synthetic-001"],
                },
            },
        )
        repos.ExecutionEvents.insert(conn, event.to_row())

    with TestClient(create_app()) as client:
        response = client.get(f"/api/graph/threads/{turn}/events")
    assert response.status_code == 200
    payload = _sse_events(response.text)["model_completed"]
    assert payload["telemetry"] == {
        "provider": "openrouter", "model": "stealth/space-bunny-alpha",
        "attempts": 1,
        "status_code": 200, "latency_ms": 26708,
    }
    assert payload["retrieval_telemetry"] == {
        "retrieval_mode": "HYBRID", "lexical_hits": 1, "vector_hits": 1,
        "fused_hits": 1, "selected_chunk_ids": ["c000"],
        "source_file_ids": ["doc-synthetic-001"],
    }

