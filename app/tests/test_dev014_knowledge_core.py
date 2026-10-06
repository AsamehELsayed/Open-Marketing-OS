from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.database.sqlite import connect
from app.services.files import repo
from app.services.knowledge_index import file_capabilities, file_index_state, rebuild_project_knowledge
from app.services.rag import indexing_service
from app.services.rag.scoped_retrieval import retrieve_scoped


class FtsStore:
    mode = "FTS_ONLY"
    offline_reason = "synthetic vector runtime unavailable"

    def remove_by_path(self, path):
        pass

    def upsert(self, items):
        pass


class Provider:
    def embed(self, value):
        return [0.0]


def _db(path: Path, project_ids=("a", "b")):
    conn = connect(path)
    for pid in project_ids:
        conn.execute("INSERT INTO projects(id,name,created_at,updated_at) VALUES (?,?,?,?)",
                     (pid, pid, "now", "now"))
    conn.commit()
    return conn


def test_workspace_ownership_is_project_scoped_and_preserves_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(indexing_service, "DEFAULT_ROOTS", ("knowledge",))
    root = tmp_path / "repo"
    (root / "knowledge").mkdir(parents=True)
    (root / "knowledge" / "same.md").write_text("# Shared\nORANGE A", encoding="utf-8")
    conn = _db(tmp_path / "app.db")
    # An unclassified row is never swept merely because its path looks stale.
    conn.execute("INSERT INTO documents(id,project_id,path,file_sha,status_tag,indexed_at) VALUES (?,?,?,?,?,?)",
                 ("unknown", "a", "old/unknown.md", "x", "UNKNOWN", "now"))
    conn.commit()

    a = indexing_service.IndexingService(conn, root, project_id="a").build_or_update()
    b = indexing_service.IndexingService(conn, root, project_id="b").build_or_update()
    assert a["indexed"] == b["indexed"] == 1
    assert conn.execute("SELECT COUNT(*) FROM documents WHERE source_kind='workspace' AND source_ref='knowledge/same.md'").fetchone()[0] == 2
    assert conn.execute("SELECT 1 FROM documents WHERE id='unknown'").fetchone()

    (root / "knowledge" / "same.md").unlink()
    deleted = indexing_service.IndexingService(conn, root, project_id="a").build_or_update()
    assert deleted["deleted"] == 1
    assert conn.execute("SELECT 1 FROM documents WHERE project_id='b' AND source_kind='workspace'").fetchone()
    assert conn.execute("SELECT 1 FROM documents WHERE id='unknown'").fetchone()


def test_persisted_project_upload_rebuild_and_noop_report(tmp_path, monkeypatch):
    monkeypatch.setattr(indexing_service, "DEFAULT_ROOTS", ())
    root = tmp_path / "repo"
    conn = _db(tmp_path / "app.db", ("a",))
    repo.ensure_schema(conn)
    file_id = "synthetic-file-id"
    data = b"Persisted ORANGE-MARKETING-ELEPHANT-927 knowledge."
    safe = f"{file_id}.txt"
    path = root / "data" / "projects" / "a" / "files" / safe
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    repo.insert_file(conn, {"file_id": file_id, "project_id": "a", "original_name": "notes.txt",
                            "safe_name": safe, "mime_detected": "text/plain", "size": len(data),
                            "sha256": hashlib.sha256(data).hexdigest(), "kind": "document",
                            "extraction": "ready", "attach_scope": "project", "indexed": False,
                            "rel_path": f"data/projects/a/files/{safe}"})
    store, provider = FtsStore(), Provider()
    first = rebuild_project_knowledge(conn, root, "a", store=store, provider=provider)
    row = conn.execute("SELECT * FROM project_files WHERE file_id=?", (file_id,)).fetchone()
    assert first["indexed"] == 1 and first["chunks"] == 1, (first.get("errors"), first.get("uploads"))
    assert file_index_state(conn, row) == {"indexed": True, "index_status": "indexed", "index_error": ""}
    doc = conn.execute("SELECT source_kind,source_ref,project_id FROM documents WHERE path LIKE 'project-files/%'").fetchone()
    assert tuple(doc) == ("project_file", file_id, "a")

    second = rebuild_project_knowledge(conn, root, "a", store=store, provider=provider)
    assert second["indexed"] == 0 and second["skipped"] == 1
    assert second["search_mode"] == "FTS" and second["vector_status"] == "NOT_AVAILABLE"
    assert second["chunks"] == 1
    assert retrieve_scoped(conn, "ORANGE-MARKETING-ELEPHANT-927", project_id="a", mode="lexical")["hits"]
    assert retrieve_scoped(conn, "ORANGE-MARKETING-ELEPHANT-927", project_id="b", mode="lexical")["hits"] == []
    assert file_capabilities()["ocr_supported"] is False

    # A direct legacy workspace pass cannot sweep the upload pipeline.
    indexing_service.IndexingService(conn, root, project_id="a").build_or_update()
    assert conn.execute("SELECT 1 FROM documents WHERE source_kind='project_file' AND source_ref=?", (file_id,)).fetchone()

    # Rebuild must recover searchable rows from persisted metadata + bytes.
    conn.execute("DELETE FROM chunks_fts")
    conn.execute("DELETE FROM documents WHERE source_ref=?", (file_id,))
    conn.execute("UPDATE project_files SET indexed=1,index_status='indexed' WHERE file_id=?", (file_id,))
    conn.commit()
    recovered = rebuild_project_knowledge(conn, root, "a", store=store, provider=provider)
    repaired = conn.execute("SELECT * FROM project_files WHERE file_id=?", (file_id,)).fetchone()
    assert recovered["indexed"] == 1 and file_index_state(conn, repaired)["indexed"]


def test_dense_query_fails_closed_without_scoped_where(tmp_path):
    class UnsafeStore:
        mode = "HYBRID"

        def query(self, vector, k):
            pytest.fail("scoped retrieval must not issue global top-k")

    conn = _db(tmp_path / "app.db", ("a",))
    result = retrieve_scoped(conn, "phrase", project_id="a", store=UnsafeStore(),
                             provider=Provider(), mode="dense")
    assert result["hits"] == []


def test_missing_bytes_and_secret_reindex_clear_success_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(indexing_service, "DEFAULT_ROOTS", ())
    root = tmp_path / "repo"
    conn = _db(tmp_path / "app.db", ("a",))
    repo.ensure_schema(conn)
    store, provider = FtsStore(), Provider()

    def add_file(file_id, text, write_bytes=True):
        data = text.encode()
        safe = f"{file_id}.txt"
        rel = f"data/projects/a/files/{safe}"
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if write_bytes:
            target.write_bytes(data)
        repo.insert_file(conn, {"file_id": file_id, "project_id": "a", "original_name": f"{file_id}.txt",
                                "safe_name": safe, "mime_detected": "text/plain", "size": len(data),
                                "sha256": hashlib.sha256(data).hexdigest(), "kind": "document",
                                "extraction": "ready", "attach_scope": "project", "indexed": True,
                                "index_status": "indexed", "rel_path": rel})
        return file_id

    missing = add_file("missing-id", "missing content", write_bytes=False)
    secret = add_file("secret-id", "api_key=synthetic-test-secret")
    # Seed old searchable rows so both invalid cases must actively clean them.
    from app.services.files.chunk_index import index_file_text
    index_file_text(conn, file_id=missing, project_id="a", original_name="missing-id.txt",
                    sha256="old", text="old searchable text", mime="text/plain")
    index_file_text(conn, file_id=secret, project_id="a", original_name="secret-id.txt",
                    sha256="old", text="old searchable text", mime="text/plain")
    report = rebuild_project_knowledge(conn, root, "a", store=store, provider=provider)
    states = {r["file_id"]: file_index_state(conn, r)
              for r in conn.execute("SELECT * FROM project_files")}
    assert states[missing]["index_status"] == "failed" and not states[missing]["indexed"]
    assert states[secret]["index_status"] == "quarantined" and not states[secret]["indexed"]
    assert report["failed"] == 1 and report["quarantined"] == 1
    assert conn.execute("SELECT COUNT(*) FROM documents WHERE source_kind='project_file'").fetchone()[0] == 0


def test_additive_migration_can_run_again_without_losing_document_identity(tmp_path):
    db_path = tmp_path / "app.db"
    conn = _db(db_path, ("a",))
    conn.execute("INSERT INTO documents(id,project_id,path,source_kind,source_ref,file_sha,status_tag,indexed_at) VALUES (?,?,?,?,?,?,?,?)",
                 ("keep", "a", "workspace-files/a/old.md", "workspace", "old.md", "sha", "VERIFIED", "then"))
    conn.commit()
    conn.close()
    conn = connect(db_path)
    conn = connect(db_path)
    row = conn.execute("SELECT id,path,project_id,source_kind,source_ref,file_sha,indexed_at FROM documents WHERE id='keep'").fetchone()
    assert tuple(row) == ("keep", "workspace-files/a/old.md", "a", "workspace", "old.md", "sha", "then")
    assert {r[1] for r in conn.execute("PRAGMA table_info(project_files)")} >= {"index_status", "index_error"}


def test_vector_embedding_failure_keeps_lexical_upload_index(tmp_path, monkeypatch):
    monkeypatch.setattr(indexing_service, "DEFAULT_ROOTS", ())
    root = tmp_path / "repo"
    conn = _db(tmp_path / "app.db", ("a",))
    repo.ensure_schema(conn)
    file_id = "vector-failure-id"
    data = b"Lexical success survives optional embedding failure."
    safe = f"{file_id}.txt"
    path = root / "data" / "projects" / "a" / "files" / safe
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    repo.insert_file(conn, {"file_id": file_id, "project_id": "a", "original_name": "vector-failure.txt",
                            "safe_name": safe, "mime_detected": "text/plain", "size": len(data),
                            "sha256": hashlib.sha256(data).hexdigest(), "kind": "document",
                            "extraction": "ready", "attach_scope": "project", "indexed": False,
                            "rel_path": f"data/projects/a/files/{safe}"})

    class BrokenProvider:
        def embed(self, value):
            raise RuntimeError("synthetic embed failure")

    store = FtsStore()
    report = rebuild_project_knowledge(conn, root, "a", store=store, provider=BrokenProvider())
    assert report["indexed"] == 1 and report["chunks"] == 1
    assert report["search_mode"] == "FTS" and report["vector_status"] == "FAILED"
    assert conn.execute("SELECT 1 FROM chunks_fts WHERE path LIKE 'project-files/%'").fetchone()


def _insert_markdown_upload(conn, root, file_id, *, rel_path=None, write_bytes=True):
    from app.services.files.hashing import sha256_hex

    text = ("# First\nFirst section has alpha words.\n"
            "# Second\nSecond section has removedchunkphrase for retrieval.\n"
            "# Third\nThird section has omega words.\n")
    data = text.encode("utf-8")
    safe = f"{file_id}.md"
    normal_rel = f"data/projects/a/files/{safe}"
    target = root / normal_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if write_bytes:
        target.write_bytes(data)
    repo.insert_file(conn, {"file_id": file_id, "project_id": "a", "original_name": safe,
                            "safe_name": safe, "mime_detected": "text/markdown", "size": len(data),
                            "sha256": sha256_hex(data), "kind": "document", "extraction": "ready",
                            "attach_scope": "project", "indexed": False,
                            "rel_path": rel_path or normal_rel})
    return text


@pytest.mark.parametrize("damage", ["missing_fts", "missing_chunk", "partial_pair_lost",
                                      "duplicate_fts", "mismatched_fts", "both_lost"])
def test_partial_upload_index_is_detected_and_repaired(tmp_path, monkeypatch, damage):
    monkeypatch.setattr(indexing_service, "DEFAULT_ROOTS", ())
    root = tmp_path / "repo"
    conn = _db(tmp_path / "app.db", ("a",))
    repo.ensure_schema(conn)
    file_id = f"partial-{damage}"
    _insert_markdown_upload(conn, root, file_id)
    store, provider = FtsStore(), Provider()
    first = rebuild_project_knowledge(conn, root, "a", store=store, provider=provider)
    assert first["indexed"] == 1
    doc = conn.execute("SELECT id,path FROM documents WHERE source_ref=?", (file_id,)).fetchone()
    path = doc["path"]
    target = conn.execute("SELECT rowid,text,header,path FROM chunks_fts WHERE path=? AND header='Second'", (path,)).fetchone()
    chunk = conn.execute("SELECT id FROM chunks WHERE document_id=? AND header='Second'", (doc["id"],)).fetchone()
    if damage == "missing_fts":
        conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (target["rowid"],))
    elif damage == "missing_chunk":
        conn.execute("DELETE FROM chunks WHERE id=?", (chunk["id"],))
    elif damage == "partial_pair_lost":
        conn.execute("DELETE FROM chunks WHERE id=?", (chunk["id"],))
        conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (target["rowid"],))
    elif damage == "duplicate_fts":
        conn.execute("INSERT INTO chunks_fts(text,header,path) VALUES (?,?,?)",
                     (target["text"], target["header"], target["path"]))
    elif damage == "mismatched_fts":
        conn.execute("UPDATE chunks_fts SET text='unrelated text' WHERE rowid=?", (target["rowid"],))
    elif damage == "both_lost":
        conn.execute("DELETE FROM chunks WHERE document_id=?", (doc["id"],))
        conn.execute("DELETE FROM chunks_fts WHERE path=?", (path,))
    conn.commit()

    row = conn.execute("SELECT * FROM project_files WHERE file_id=?", (file_id,)).fetchone()
    # The expected count catches a paired chunk+FTS deletion as well as an
    # incomplete chunk/FTS mapping.
    assert file_index_state(conn, row)["indexed"] is False
    repaired = rebuild_project_knowledge(conn, root, "a", store=store, provider=provider)
    row = conn.execute("SELECT * FROM project_files WHERE file_id=?", (file_id,)).fetchone()
    assert repaired["indexed"] == 1 and repaired["skipped"] == 0
    assert file_index_state(conn, row)["indexed"] is True
    assert conn.execute("SELECT COUNT(*) FROM chunks WHERE document_id=?", (doc["id"],)).fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM chunks_fts WHERE path=?", (path,)).fetchone()[0] == 3
    hits = retrieve_scoped(conn, "removedchunkphrase", project_id="a", mode="lexical")["hits"]
    assert any("removedchunkphrase" in hit["text"] for hit in hits)


@pytest.mark.parametrize("fault,code", [("invalid_path", "invalid_stored_path"),
                                         ("missing_bytes", "stored_bytes_missing")])
def test_persisted_upload_path_failures_keep_specific_report_code(tmp_path, monkeypatch, fault, code):
    monkeypatch.setattr(indexing_service, "DEFAULT_ROOTS", ())
    root = tmp_path / "repo"
    conn = _db(tmp_path / "app.db", ("a",))
    repo.ensure_schema(conn)
    file_id = f"path-{fault}"
    bad_path = "data/projects/a/files/../outside.txt" if fault == "invalid_path" else None
    _insert_markdown_upload(conn, root, file_id, rel_path=bad_path,
                            write_bytes=(fault != "missing_bytes"))
    # Seed stale successful searchable records to prove error cleanup.
    from app.services.files.chunk_index import index_file_text
    index_file_text(conn, file_id=file_id, project_id="a", original_name=f"{file_id}.md",
                    sha256="old", text="# Old\nOld searchable rows.", mime="text/markdown")
    report = rebuild_project_knowledge(conn, root, "a", store=FtsStore(), provider=Provider())
    file_row = conn.execute("SELECT * FROM project_files WHERE file_id=?", (file_id,)).fetchone()
    assert report["failed"] == 1
    assert report["errors"] == [{"file_id": file_id, "code": code}]
    assert file_index_state(conn, file_row) == {"indexed": False, "index_status": "failed", "index_error": code}
    assert conn.execute("SELECT 1 FROM documents WHERE source_ref=?", (file_id,)).fetchone() is None


def test_v10_backfill_clears_inconsistent_legacy_upload_success(tmp_path, monkeypatch):
    monkeypatch.setattr(indexing_service, "DEFAULT_ROOTS", ())
    root = tmp_path / "repo"
    db_path = tmp_path / "app.db"
    conn = _db(db_path, ("a",))
    repo.ensure_schema(conn)
    file_id = "migration-partial"
    _insert_markdown_upload(conn, root, file_id)
    rebuild_project_knowledge(conn, root, "a", store=FtsStore(), provider=Provider())
    doc = conn.execute("SELECT id,path FROM documents WHERE source_ref=?", (file_id,)).fetchone()
    conn.execute("DELETE FROM chunks_fts WHERE path=? AND header='Second'", (doc["path"],))
    conn.execute("UPDATE project_files SET indexed=1,index_status='indexed' WHERE file_id=?", (file_id,))
    conn.commit()
    conn.close()

    conn = connect(db_path)
    row = conn.execute("SELECT indexed,index_status,index_error FROM project_files WHERE file_id=?", (file_id,)).fetchone()
    assert tuple(row) == (0, "pending", "")
