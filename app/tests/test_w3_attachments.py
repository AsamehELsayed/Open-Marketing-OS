"""W3 conversation-bound attachment security contracts."""
import pytest
import importlib.util
import copy
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import deps
from app.database import repos
from app.routes.files import router as files_router
from app.routes.graph_runtime import router as graph_router
from app.routes.api_spa import router as spa_router
from app.routes.chat import router as chat_router
from app.services.files import repo as file_repo
from app.services.files.attachments import assemble_attachment_evidence

LANGGRAPH_AVAILABLE = importlib.util.find_spec("langgraph") is not None


@pytest.fixture
def w3env(tmp_path, monkeypatch):
    db, root = tmp_path / "w3.db", tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setattr(deps, "ROOT", root)
    deps.init_db(db)
    with deps.get_db() as conn:
        for pid in ("a", "b"):
            repos.Projects.upsert(conn, {"id": pid, "name": pid, "website": "", "goal": "",
                "status": "active", "settings_json": "{}", "created_at": "x", "updated_at": "x"})
        from app.services import state
        for cid, pid in (("ca", "a"), ("cb", "a"), ("cc", "b")):
            row = state.create_conversation(conn, project_id=pid, title=cid)
            conn.execute("UPDATE conversations SET id=? WHERE id=?", (cid, row["id"]))
    app = FastAPI()
    app.include_router(files_router)
    app.include_router(graph_router)
    app.include_router(spa_router)
    app.include_router(chat_router)
    return {"client": TestClient(app), "root": root, "db": db}


def _upload(env, *, scope="turn", conversation="ca", project="a", payload=b"Pricing method is monthly retainer."):
    return env["client"].post("/files/upload", files={"file": ("proposal.txt", payload, "text/plain")},
        data={"project_id": project, "attach_scope": scope, "conversation_id": conversation})


def test_turn_upload_requires_matching_conversation_and_never_indexes(w3env):
    assert _upload(w3env, conversation="cc").status_code == 400
    response = _upload(w3env)
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["attach_scope"] == "turn" and data["conversation_id"] == "ca"
    assert data["indexed"] is False
    with deps.get_db() as conn:
        row = file_repo.get_file(conn, data["file_id"], "a")
        assert row["indexed"] == 0
        assert conn.execute("SELECT count(*) FROM project_files WHERE file_id=? AND indexed=1",
                            (data["file_id"],)).fetchone()[0] == 0


def test_attachment_evidence_is_conversation_and_project_scoped(w3env):
    data = _upload(w3env).json()["data"]
    with deps.get_db() as conn:
        assert len(assemble_attachment_evidence(conn, root=w3env["root"], project_id="a",
            conversation_id="ca", file_ids=[data["file_id"]])) == 1
        for pid, cid in (("a", "cb"), ("b", "cc")):
            with pytest.raises(ValueError):
                assemble_attachment_evidence(conn, root=w3env["root"], project_id=pid,
                    conversation_id=cid, file_ids=[data["file_id"]])
        for ids in ([data["file_id"], data["file_id"]], ["missing"]):
            with pytest.raises(ValueError):
                assemble_attachment_evidence(conn, root=w3env["root"], project_id="a",
                    conversation_id="ca", file_ids=ids)


def test_secret_bearing_attachment_is_quarantined_from_context(w3env, monkeypatch):
    data = _upload(w3env, payload=b"API_KEY=sk-12345678901234567890").json()["data"]
    with deps.get_db() as conn:
        monkeypatch.setattr("app.services.files.attachments.find_secrets", lambda text: ["api_key"])
        assert assemble_attachment_evidence(conn, root=w3env["root"], project_id="a",
            conversation_id="ca", file_ids=[data["file_id"]]) == []


@pytest.mark.parametrize("mutation", ["bytes", "size", "sha256", "mime"])
def test_attachment_integrity_metadata_is_revalidated_before_extraction(w3env, monkeypatch, mutation):
    data = _upload(w3env).json()["data"]
    with deps.get_db() as conn:
        row = file_repo.get_file(conn, data["file_id"], "a")
        stored = w3env["root"] / row["rel_path"]
        if mutation == "bytes":
            stored.write_bytes(b"Changed after upload")
        elif mutation == "size":
            conn.execute("UPDATE project_files SET size=size+1 WHERE file_id=?",
                         (data["file_id"],))
        elif mutation == "sha256":
            conn.execute("UPDATE project_files SET sha256=? WHERE file_id=?",
                         ("0" * 64, data["file_id"]))
        else:
            conn.execute("UPDATE project_files SET mime_detected='application/pdf' WHERE file_id=?",
                         (data["file_id"],))
        monkeypatch.setattr("app.services.files.attachments.extract.extract",
                            lambda *args: pytest.fail("must validate before extract"))
        with pytest.raises(ValueError, match="integrity validation"):
            assemble_attachment_evidence(conn, root=w3env["root"], project_id="a",
                conversation_id="ca", file_ids=[data["file_id"]])


def test_turn_creation_persists_selected_ids_and_rejects_foreign_binding(w3env):
    data = _upload(w3env).json()["data"]
    with deps.get_db() as conn:
        from app.services.turns import create_turn
        made = create_turn(conn, str(w3env["db"]), conversation_id="ca", project_id="a",
                           text="Use this file", attachment_ids=[data["file_id"]])
        selected = file_repo.selected_for_turn(conn, turn_id=made["turn"]["id"],
                                               project_id="a", conversation_id="ca")
        assert selected == [data["file_id"]]
        with pytest.raises(ValueError):
            create_turn(conn, str(w3env["db"]), conversation_id="cb", project_id="a",
                        text="leak", attachment_ids=[data["file_id"]])


def test_graph_thread_entry_accepts_and_binds_optional_ids(w3env):
    data = _upload(w3env).json()["data"]
    response = w3env["client"].post("/api/graph/threads", json={
        "project_id": "a", "conversation_id": "ca", "text": "Use proposal",
        "attachment_ids": [data["file_id"]]})
    assert response.status_code == 201, response.text
    turn_id = response.json()["data"]["turn_id"]
    with deps.get_db() as conn:
        assert file_repo.selected_for_turn(conn, turn_id=turn_id, project_id="a",
                                           conversation_id="ca") == [data["file_id"]]


def test_graph_execute_persists_and_returns_sanitized_attachment_citations(w3env, monkeypatch):
    data = _upload(w3env).json()["data"]

    class FakeGraph:
        def invoke(self, state, config=None):
            assert "Pricing method is monthly retainer." in state["attachment_evidence"][0]["text"]
            return {"final_answer": "The proposal uses a monthly retainer.",
                    "route": "knowledge", "errors": []}

    monkeypatch.setattr("app.routes.graph_runtime._get_graph", lambda: FakeGraph())
    created = w3env["client"].post("/api/graph/threads", json={
        "project_id": "a", "conversation_id": "ca", "text": "Summarize proposal",
        "attachment_ids": [data["file_id"]]})
    assert created.status_code == 201, created.text
    turn_id = created.json()["data"]["turn_id"]
    response = w3env["client"].post(f"/api/graph/threads/{turn_id}/execute")
    assert response.status_code == 200, response.text
    citations = response.json()["data"]["citations"]
    assert citations and citations[0]["file_id"] == data["file_id"]
    assert citations[0]["source"] == "turn_attachment"
    assert "text" not in citations[0]
    history = w3env["client"].get("/api/chats/ca/messages?project_id=a")
    assert history.status_code == 200, history.text
    persisted = history.json()["data"][-1]["citations"]
    assert persisted == citations
    assert "text" not in persisted[0]


def test_graph_failure_with_selected_attachment_fails_without_fallback(w3env, monkeypatch):
    from app.contracts import runtime
    from app.services import turns

    data = _upload(w3env).json()["data"]
    with deps.get_db() as conn:
        made = turns.create_turn(conn, str(w3env["db"]), conversation_id="ca",
            project_id="a", text="Use proposal", attachment_ids=[data["file_id"]])
        turn_id = made["turn"]["id"]
    monkeypatch.setattr(runtime, "get_ai_runtime", lambda: "langgraph")
    monkeypatch.setattr(turns, "_run_graph_turn", lambda *a, **k: None)
    monkeypatch.setattr("app.services.llm.router.resolve_candidates",
                        lambda *a, **k: pytest.fail("fallback must not answer without attachment"))
    turns._run_turn(str(w3env["db"]), str(w3env["root"]), turn_id)
    with deps.get_db() as conn:
        failed = repos.Turns.get(conn, turn_id)
        assert failed["status"] == "failed"
        assert "selected attachment" in failed.get("error", "")


def test_project_rag_citations_survive_both_graph_completion_paths(w3env, monkeypatch):
    if not LANGGRAPH_AVAILABLE:
        pytest.skip("LangGraph is not installed in this runtime")
    from app.database.sqlite import connect
    from app.graphs.account_manager_graph import build_account_manager_graph
    from app.services import turns
    from app.services.files.chunk_index import index_file_text
    from app.services.files.hashing import sha256_hex

    file_id = "permanent-knowledge-id"
    content = "Renewal window is thirty days under ORANGE-POLICY-927."
    raw = content.encode("utf-8")
    stored_path = w3env["root"] / "data/projects/a/files/policy.txt"
    stored_path.parent.mkdir(parents=True, exist_ok=True)
    stored_path.write_bytes(raw)
    with deps.get_db() as conn:
        file_repo.insert_file(conn, {
            "file_id": file_id, "project_id": "a", "original_name": "Renewal Policy.txt",
            "safe_name": "policy.txt", "mime_detected": "text/plain", "size": len(raw),
            "sha256": sha256_hex(raw), "kind": "document", "extraction": "ready",
            "attach_scope": "project", "indexed": True,
            "index_status": "indexed", "rel_path": "data/projects/a/files/policy.txt"})
        index_file_text(conn, file_id=file_id, project_id="a",
            original_name="Renewal Policy.txt", sha256=sha256_hex(raw),
            text=content, mime="text/plain")

    # Use the real adapter + scoped SQLite/FTS retrieval, with vectors explicitly
    # absent so no model acquisition or semantic claim is involved.
    monkeypatch.setattr("app.services.knowledge_index.retrieval_runtime",
                        lambda root, project_id: (None, None))
    with deps.get_db() as conn:
        from app.graphs.adapters import RetrievalAdapter
        retrieved = RetrievalAdapter(conn, "a").retrieve("ORANGE-POLICY-927 renewal window")
        assert retrieved.hits
        assert retrieved.hits[0].file_id == file_id
        assert retrieved.hits[0].file_name == "Renewal Policy.txt"

    completion_contexts = []
    completion_citations = []
    from app.graphs import state as graph_state

    def deterministic_completion(**kwargs):
        completion_contexts.append(copy.deepcopy(kwargs["context"]))
        assert any(hit.get("file_id") == file_id
                   for hit in kwargs["context"].get("rag_evidence", []))
        completion_citations.append(graph_state.citation_projection(
            project_id=kwargs["project_id"],
            rag_hits=kwargs["context"].get("rag_evidence", [])))
        assert completion_citations[-1], kwargs["context"]["rag_evidence"]
        return "The renewal window is thirty days."

    graph = build_account_manager_graph(complete_fn=deterministic_completion)
    monkeypatch.setattr("app.routes.graph_runtime._get_graph", lambda: graph)
    prompt = "Why is the renewal window thirty days under ORANGE-POLICY-927?"
    direct_created = w3env["client"].post("/api/graph/threads", json={
        "project_id": "a", "conversation_id": "ca", "text": prompt})
    assert direct_created.status_code == 201, direct_created.text
    direct_turn = direct_created.json()["data"]["turn_id"]
    direct_response = w3env["client"].post(
        f"/api/graph/threads/{direct_turn}/execute",
        headers={"Idempotency-Key": "citation-replay"})
    assert direct_response.status_code == 200, direct_response.text
    assert completion_contexts, direct_response.json()

    from app.services.graph_conn import default_conn
    project_citations = direct_response.json()["data"]["citations"]
    assert project_citations, direct_response.json()
    citation = next(item for item in project_citations
                    if item.get("source") == "project_rag")
    assert citation["file_id"] == file_id
    assert citation["file_name"] == "Renewal Policy.txt"
    assert citation["project_id"] == "a"
    assert citation["chunk_id"]
    assert citation["retrieval_sources"] == ["fts"]
    assert "text" not in citation and content not in str(citation)
    direct_history = w3env["client"].get("/api/chats/ca/messages?project_id=a").json()["data"]
    assert direct_history[-1]["citations"] == project_citations
    replay = w3env["client"].post(
        f"/api/graph/threads/{direct_turn}/execute",
        headers={"Idempotency-Key": "citation-replay"})
    assert replay.status_code == 200
    assert replay.json()["data"]["replayed"] is True
    assert replay.json()["data"]["citations"] == project_citations

    with deps.get_db() as conn:
        made = turns.create_turn(conn, str(w3env["db"]), conversation_id="cb",
            project_id="a", text=prompt, client_message_id="project-rag-turn")
        turn = made["turn"]
    turn_conn = default_conn()
    try:
        graph_result = turns._run_graph_turn(
            turn_conn, str(w3env["db"]), "a", "cb", turn["id"], prompt)
    finally:
        turn_conn.close()
    assert graph_result and any(c.get("file_id") == file_id
                                for c in graph_result["provenance"])
    turns._finish_turn(str(w3env["db"]), str(w3env["root"]), turn,
                       graph_result, "langgraph", prompt)
    turn_history = w3env["client"].get("/api/chats/cb/messages?project_id=a").json()["data"]
    turn_citation = next(item for item in turn_history[-1]["citations"]
                         if item.get("source") == "project_rag")
    assert turn_citation["file_id"] == file_id
    assert turn_citation["file_name"] == "Renewal Policy.txt"
    assert "text" not in turn_citation and content not in str(turn_citation)


def test_state_only_answers_never_gain_citations_from_later_retrieval(w3env, monkeypatch):
    """A matching indexed file is not provenance unless completion saw it."""
    from app.services.files.chunk_index import index_file_text
    from app.services.files.hashing import sha256_hex
    from app.services import turns
    from app.routes import chat as chat_routes

    file_id = "state-only-matching-file"
    content = "A matching fact uses state-only marker CITATION-TRAP-31."
    raw = content.encode("utf-8")
    path = w3env["root"] / "data/projects/a/files/trap.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    with deps.get_db() as conn:
        file_repo.insert_file(conn, {
            "file_id": file_id, "project_id": "a", "original_name": "Trap.txt",
            "safe_name": "trap.txt", "mime_detected": "text/plain", "size": len(raw),
            "sha256": sha256_hex(raw), "kind": "document", "extraction": "ready",
            "attach_scope": "project", "indexed": True,
            "index_status": "indexed", "rel_path": "data/projects/a/files/trap.txt"})
        index_file_text(conn, file_id=file_id, project_id="a", original_name="Trap.txt",
                        sha256=sha256_hex(raw), text=content, mime="text/plain")

    class StateOnlyGraph:
        def invoke(self, state, config=None):
            return {"final_answer": "The project is active.", "route": "state_only"}

    monkeypatch.setattr("app.routes.graph_runtime._get_graph", lambda: StateOnlyGraph())
    from app.graphs import state as graph_state
    original_retrieve = graph_state.retrieve_project_citations
    later_lookups = []

    def track_lookup(conn, *, project_id, query):
        later_lookups.append((project_id, query))
        return original_retrieve(conn, project_id=project_id, query=query)

    monkeypatch.setattr(graph_state, "retrieve_project_citations", track_lookup)

    prompt = "What is the fact for CITATION-TRAP-31?"
    created = w3env["client"].post("/api/graph/threads", json={
        "project_id": "a", "conversation_id": "ca", "text": prompt})
    assert created.status_code == 201, created.text
    turn_id = created.json()["data"]["turn_id"]
    executed = w3env["client"].post(f"/api/graph/threads/{turn_id}/execute")
    assert executed.status_code == 200, executed.text
    assert executed.json()["data"]["citations"] == []

    # Run the ordinary /chat/turn transport synchronously for deterministic
    # persistence in this test, then inspect the resulting message history.
    monkeypatch.setattr(chat_routes.turnsvc, "start_turn_bg",
                        lambda db_path, root, tid: turns._run_turn(db_path, root, tid))
    ack = w3env["client"].post("/chat/turn", data={
        "conversation_id": "cb", "text": prompt, "client_message_id": "state-only-trap"})
    assert ack.status_code == 200, ack.text
    history = w3env["client"].get("/api/chats/cb/messages?project_id=a").json()["data"]
    assistant = next(message for message in history if message["role"] == "assistant")
    assert assistant["citations"] == []
    assert later_lookups == []


def test_persisted_citations_use_frozen_completion_snapshot_after_index_change(w3env, monkeypatch):
    """A later index mutation cannot replace IDs or retrieval branches."""
    from app.graphs.state import citation_projection
    from app.services.files.chunk_index import index_file_text
    from app.services.files.hashing import sha256_hex

    file_id = "frozen-completion-source"
    text = "Frozen evidence statement with SNAPSHOT-ID-81."
    raw = text.encode("utf-8")
    path = w3env["root"] / "data/projects/a/files/frozen.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    with deps.get_db() as conn:
        file_repo.insert_file(conn, {
            "file_id": file_id, "project_id": "a", "original_name": "Frozen.txt",
            "safe_name": "frozen.txt", "mime_detected": "text/plain", "size": len(raw),
            "sha256": sha256_hex(raw), "kind": "document", "extraction": "ready",
            "attach_scope": "project", "indexed": True,
            "index_status": "indexed", "rel_path": "data/projects/a/files/frozen.txt"})
        index_file_text(conn, file_id=file_id, project_id="a", original_name="Frozen.txt",
                        sha256=sha256_hex(raw), text=text, mime="text/plain")

    # This object stands for the structured evidence snapshot consumed by the
    # completion function. Its source IDs and FTS branch are captured before
    # the fake graph changes current index state.
    completion_snapshot = [{
        "source": "project_rag", "project_id": "a", "file_id": file_id,
        "file_name": "Frozen.txt", "path": "data/projects/a/files/frozen.txt",
        "chunk_id": "frozen-chunk-1", "sources": ["fts"], "text": text,
    }]
    expected = citation_projection(project_id="a", rag_hits=completion_snapshot)
    assert expected[0]["file_id"] == file_id
    assert expected[0]["retrieval_sources"] == ["fts"]

    from app.graphs import state as graph_state
    monkeypatch.setattr(graph_state, "retrieve_project_citations",
                        lambda *a, **kw: pytest.fail("post-answer retrieval is forbidden"))

    class CompletionSnapshotGraph:
        def invoke(self, state, config=None):
            # The completion already has this evidence. Index changes after
            # assembly must not affect the citations carried to persistence.
            context = copy.deepcopy(completion_snapshot)
            citations = citation_projection(project_id="a", rag_hits=context)
            with deps.get_db() as conn:
                conn.execute("UPDATE project_files SET indexed=0 WHERE file_id=?",
                             (file_id,))
            return {"final_answer": "Frozen evidence was used.",
                    "route": "knowledge", "citation_evidence": citations}

    monkeypatch.setattr("app.routes.graph_runtime._get_graph",
                        lambda: CompletionSnapshotGraph())
    created = w3env["client"].post("/api/graph/threads", json={
        "project_id": "a", "conversation_id": "ca",
        "text": "Explain SNAPSHOT-ID-81."})
    assert created.status_code == 201, created.text
    tid = created.json()["data"]["turn_id"]
    result = w3env["client"].post(f"/api/graph/threads/{tid}/execute")
    assert result.status_code == 200, result.text
    assert result.json()["data"]["citations"] == expected
    history = w3env["client"].get("/api/chats/ca/messages?project_id=a").json()["data"]
    assert history[-1]["citations"] == expected


def test_prompt_separates_current_attachments_from_project_knowledge():
    from app.routes.graph_runtime import build_context_text

    rendered = build_context_text({"rag_evidence": [
        {"source": "turn_attachment", "filename": "This chat.txt",
         "file_id": "turn-file", "chunk_id": "attachment:turn-file",
         "text": "chat-only secret phrase"},
        {"source": "project_rag", "path": "project-files/a/project-file",
         "chunk_id": "c000", "text": "permanent project phrase"},
    ]})
    assert "[CURRENT TURN ATTACHMENTS]" in rendered
    assert "[PROJECT KNOWLEDGE]" in rendered
    assert "This chat.txt" in rendered and "chat-only secret phrase" in rendered
    project_block = rendered.split("[PROJECT KNOWLEDGE]", 1)[1]
    assert "This chat.txt" not in project_block
    assert "chat-only secret phrase" not in project_block
    assert "project-files/a/project-file#c000 permanent project phrase" in project_block
