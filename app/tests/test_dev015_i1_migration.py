"""DEV-015 I1 canonical conversation attachment schema migration tests."""
from app.database.sqlite import (
    SCHEMA_VERSION,
    _migrate_v10_to_v11,
    connect,
    get_user_version,
)
from app.services.files import repo as files_repo
from app.services.rag.chroma_store import ChromaStore
from app.services.rag.embeddings import LocalMultilingualE5Embeddings


def test_v10_to_v11_attachment_tables_migrate_idempotently_and_round_trip(tmp_path):
    path = tmp_path / "legacy-v10.sqlite"
    conn = connect(path)
    conn.execute("DROP INDEX IF EXISTS idx_turn_file_binding_conversation")
    conn.execute("DROP TABLE IF EXISTS turn_attachment_selections")
    conn.execute("DROP TABLE IF EXISTS turn_file_bindings")
    conn.execute("PRAGMA user_version=10")
    conn.commit()

    _migrate_v10_to_v11(conn)
    _migrate_v10_to_v11(conn)
    conn.execute("""INSERT INTO project_files(
        file_id,project_id,original_name,safe_name,mime_detected,size,sha256,kind,
        extraction,attach_scope,indexed,rel_path,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("file-1", "project-a", "proposal.txt", "file-1.txt", "text/plain", 1,
         "sha", "document", "ready", "turn", 0, "data/file-1.txt", "now"))
    files_repo.bind_turn_file(conn, "file-1", "project-a", "conversation-a")
    files_repo.save_turn_selection(conn, turn_id="turn-a", file_ids=["file-1"],
                                   project_id="project-a", conversation_id="conversation-a")
    conn.close()

    reopened = connect(path)
    assert get_user_version(reopened) == SCHEMA_VERSION
    assert files_repo.list_turn_files(reopened, ["file-1"], project_id="project-a",
                                      conversation_id="conversation-a")[0]["file_id"] == "file-1"
    assert files_repo.list_turn_files(reopened, ["file-1"], project_id="project-a",
                                      conversation_id="conversation-b") == []
    assert files_repo.selected_for_turn(reopened, turn_id="turn-a", project_id="project-a",
                                        conversation_id="conversation-a") == ["file-1"]
    reopened.close()


def test_canonical_schema_and_runtime_repository_create_matching_tables(tmp_path):
    conn = connect(tmp_path / "fresh.sqlite")
    files_repo.ensure_schema(conn)
    files_repo.ensure_schema(conn)
    for table in ("turn_file_bindings", "turn_attachment_selections"):
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (table,)).fetchone()
    indexes = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_turn_file_binding_conversation" in indexes
    conn.close()


def test_missing_pinned_model_cache_cannot_advertise_hybrid():
    store = ChromaStore.__new__(ChromaStore)
    store._collection = object()
    store._vector_error = False
    store._semantic_inference = True
    store._coverage_complete = True
    store.offline_reason = ""
    store.provider = LocalMultilingualE5Embeddings(cache_dir="DEV-015-NO-MODEL-CACHE")
    assert store.mode == "FTS_ONLY"
    assert store.offline_reason == "embedding_model_not_cached"
