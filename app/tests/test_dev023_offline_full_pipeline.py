"""Offline full-pipeline acceptance for DEV-023; all generation uses a fake local provider."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


class _FakeSemanticProvider:
    semantic = True

    def embed_query(self, _query):
        return [1.0]


class _FakeSemanticStore:
    mode = "HYBRID"

    def query(self, _vector, _k, *, where):
        assert where == {"project_id": "dev023-pipeline"}
        return [("doc-1:c000", {"project_id": "dev023-pipeline",
                                "path": "project-files/fake.md", "chunk_id": "c000",
                                "file_sha": "sha-1"}, 0.01)]


class _FakeProvider:
    model = "dev023-offline-model"

    def __init__(self):
        self.calls = 0

    def complete(self, **_kwargs):
        self.calls += 1
        return SimpleNamespace(text="Synthetic answer for the offline acceptance fixture.",
                               usage={"input_tokens": 9, "output_tokens": 7,
                                      "cached_tokens": 0, "total_tokens": 16})


def test_scoped_retrieval_to_graph_db_sse_react_and_durable_evidence(tmp_path, monkeypatch):
    pytest.importorskip("langgraph")
    from app import deps
    from app.database import repos
    from app.database.sqlite import connect
    from app.graphs import adapters
    from app.graphs.account_manager_graph import build_account_manager_graph
    from app.graphs.state import initial_state
    from app.routes import graph_runtime
    from app.services.config_service import ConfigService
    from app.services.llm.model_router import ModelRouter
    from app.contracts.events import GraphExecutionEvent
    from app.tests.fixtures.dev023_evidence_schema import capture_persisted_turn

    # DEV-023 acceptance remains isolated from any installed/canonical DB.
    db_path = tmp_path / "dev023-offline.sqlite"
    monkeypatch.setattr(deps, "DB_PATH", db_path)
    monkeypatch.setattr(ConfigService, "get_setting", classmethod(
        lambda cls, key, default=None, conn=None: "LOCAL" if key == "ai_mode" else default))
    monkeypatch.setattr(ConfigService, "is_openai_configured", classmethod(lambda cls, conn=None: False))
    monkeypatch.setattr(ConfigService, "is_openrouter_configured", classmethod(lambda cls, conn=None: False))
    conn = connect(db_path)
    conn.execute("INSERT INTO documents(id,project_id,path,file_sha,status_tag,indexed_at) VALUES(?,?,?,?,?,?)",
                 ("doc-1", "dev023-pipeline", "project-files/fake.md", "sha-1", "VERIFIED", "2026-10-03T00:00:00Z"))
    conn.execute("INSERT INTO chunks(id,project_id,document_id,chunk_id,header,text) VALUES(?,?,?,?,?,?)",
                 ("chunk-1", "dev023-pipeline", "doc-1", "c000", "Policy", "ORANGE-POLICY-927 uses a thirty day renewal window."))
    conn.execute("INSERT INTO chunks_fts(text,header,path) VALUES(?,?,?)",
                 ("ORANGE-POLICY-927 uses a thirty day renewal window.", "Policy", "project-files/fake.md"))
    conn.commit()

    fake_semantic_provider, fake_store = _FakeSemanticProvider(), _FakeSemanticStore()
    original_init = adapters.RetrievalAdapter.__init__

    def injected_init(self, connection, project_id, store=None, provider=None):
        original_init(self, connection, project_id, store=fake_store,
                      provider=fake_semantic_provider)

    monkeypatch.setattr(adapters.RetrievalAdapter, "__init__", injected_init)
    fake_provider = _FakeProvider()
    router = ModelRouter(local_provider=fake_provider, default_local_model="dev023-offline-model")
    complete_fn = graph_runtime._make_complete_fn(router)
    graph = build_account_manager_graph(
        known_projects={"dev023-pipeline"}, conn_factory=lambda: connect(db_path),
        complete_fn=complete_fn)
    state = initial_state(project_id="dev023-pipeline", conversation_id="dev023-convo",
                          turn_id="dev023-turn", user_request=(
                              "What does ORANGE-POLICY-927 say about the renewal window?"))
    result = graph.invoke(state, config={"configurable": {"thread_id": "dev023-thread"}})
    assert result["route"] == "knowledge"
    telemetry = result["retrieval_telemetry"]
    assert telemetry["retrieval_mode"] == "HYBRID"
    assert (telemetry["lexical_hits"], telemetry["vector_hits"], telemetry["fused_hits"]) == (1, 1, 1)
    assert telemetry["selected_chunk_ids"] == ["c000"]
    assert telemetry["source_file_ids"] == ["fake.md"]
    assert fake_provider.calls == 1

    events = repos.ExecutionEvents.for_turn(conn, "dev023-turn")
    model_event = next(e for e in events if e["event_type"] == "model_completed")
    event_meta = json.loads(model_event["metadata_json"])
    persisted_telemetry = event_meta["retrieval_telemetry"]
    for field in ("retrieval_mode", "lexical_hits", "vector_hits", "fused_hits",
                  "selected_chunk_ids", "source_file_ids"):
        assert persisted_telemetry[field] == telemetry[field]
    sse = GraphExecutionEvent.from_row(model_event).to_sse(id=model_event["id"])
    sse_telemetry = sse["data"]["meta"]["retrieval_telemetry"]
    assert sse_telemetry == persisted_telemetry
    model_calls = repos.ModelCalls.for_turn(conn, "dev023-turn", "dev023-pipeline")
    assert len(model_calls) == 1
    assert model_calls[0]["provider"] == "local"
    assert model_calls[0]["call_id"] == event_meta["call_id"]
    assert model_calls[0]["input_tokens"] == 9
    assert model_calls[0]["output_tokens"] == 7
    assert model_calls[0]["total_tokens"] == 16

    wire_fixture = tmp_path / "persisted-sse.json"
    wire_fixture.write_text(json.dumps(sse["data"]["meta"]), encoding="utf-8")
    subprocess.run(["node", "frontend/tests/dev023-full-pipeline.mjs", str(wire_fixture)],
                   check=True, cwd=Path(__file__).resolve().parents[2])

    # Exercise future-turn capture against actual persisted rows, including citations.
    repos.Companies.upsert(conn, {"id": "dev023-company", "name": "Synthetic", "created_at": "2026-10-03T00:00:00Z"})
    repos.Conversations.upsert(conn, {"id": "dev023-convo", "company_id": "dev023-company",
                                      "project_id": "dev023-pipeline", "title": "Synthetic", "created_at": "2026-10-03T00:00:00Z",
                                      "updated_at": "2026-10-03T00:00:00Z"})
    repos.Messages.insert(conn, {"id": "dev023-user", "conversation_id": "dev023-convo", "role": "user",
                                 "body_md": state["user_request"], "created_at": "2026-10-03T00:00:00Z"})
    repos.Messages.insert(conn, {"id": "dev023-assistant", "conversation_id": "dev023-convo", "role": "assistant",
                                 "body_md": result["final_answer"],
                                 "citations_json": json.dumps(result.get("citation_evidence", [])),
                                 "created_at": "2026-10-03T00:00:01Z"})
    repos.Turns.upsert(conn, {"id": "dev023-turn", "conversation_id": "dev023-convo",
                              "project_id": "dev023-pipeline", "user_message_id": "dev023-user",
                              "status": "completed", "created_at": "2026-10-03T00:00:00Z"})
    artifact = tmp_path / "turn-evidence.json"
    captured = capture_persisted_turn(
        conn, project_id="dev023-pipeline", conversation_id="dev023-convo",
        turn_id="dev023-turn", assistant_message_id="dev023-assistant",
        output_path=str(artifact))
    reread = json.loads(artifact.read_text(encoding="utf-8"))
    assert captured == reread
    assert reread["user_message"]["text"] == state["user_request"]
    assert reread["assistant_message"]["text"] == result["final_answer"]
    assert reread["runtime"]["lexical_hits"] == telemetry["lexical_hits"]
    assert reread["runtime"]["selected_chunk_ids"] == telemetry["selected_chunk_ids"]
    assert "retrieved_text" in reread["excluded_fields"]
    assert "ORANGE-POLICY-927 uses a thirty day" not in artifact.read_text(encoding="utf-8")
    conn.close()
