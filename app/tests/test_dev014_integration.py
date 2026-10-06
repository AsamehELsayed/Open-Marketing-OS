from __future__ import annotations

import sqlite3
import hashlib
import pytest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import deps
from app.database import repos
from app.database.sqlite import connect, get_user_version
from app.routes.files import router as files_router
from app.services.rag.scoped_retrieval import retrieve_scoped
from app.services.files import repo as files_repo
from app.services.knowledge_index import rebuild_project_knowledge


def _projects(conn):
    for pid in ("audit014-a", "audit014-b"):
        repos.Projects.upsert(conn, {
            "id": pid, "name": pid, "website": "", "goal": "",
            "status": "active", "settings_json": "{}",
            "created_at": "2026-09-30T00:00:00+00:00",
            "updated_at": "2026-09-30T00:00:00+00:00",
        })


def test_fts_returns_the_chunk_that_matched_for_same_header(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    _projects(conn)
    from app.services.files.chunk_index import index_file_text
    tail = "LATER-CHUNK-ORANGE-927 " + ("padding words " * 90)
    text = ("EARLY-CHUNK-BLUE-111 " + ("padding words " * 90) + tail)
    index_file_text(conn, file_id="multi", project_id="audit014-a",
                    original_name="multi.txt", sha256="sha", text=text,
                    mime="text/plain")
    result = retrieve_scoped(conn, "LATER-CHUNK-ORANGE-927", project_id="audit014-a",
                             mode="lexical", k_fts=10)
    assert result["hits"]
    assert any("LATER-CHUNK-ORANGE-927" in hit["text"] for hit in result["hits"])
    assert all(hit["project_id"] == "audit014-a" for hit in result["hits"])
    conn.close()


def test_v9_to_v10_migration_preserves_rows_and_is_idempotent(tmp_path):
    path = tmp_path / "legacy-v9.sqlite"
    conn = connect(path)
    _projects(conn)
    conn.execute("INSERT INTO documents(id,project_id,path,file_sha,status_tag,indexed_at) VALUES(?,?,?,?,?,?)",
                 ("legacy-upload", "audit014-a", "project-files/audit014-a/file-9", "sha", "TXT", "when"))
    conn.execute("INSERT INTO project_files(file_id,project_id,original_name,safe_name,mime_detected,size,sha256,kind,extraction,attach_scope,indexed,rel_path,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("file-9", "audit014-a", "note.txt", "file-9.txt", "text/plain", 4, "sha", "document", "ready", "project", 0,
                  "data/projects/audit014-a/files/file-9.txt", "when"))
    conn.commit()
    # Recreate the v9 shape while retaining the same logical document/upload data.
    conn.execute("DROP INDEX IF EXISTS idx_documents_owner")
    conn.execute("ALTER TABLE documents DROP COLUMN source_kind")
    conn.execute("ALTER TABLE documents DROP COLUMN source_ref")
    conn.execute("ALTER TABLE project_files DROP COLUMN index_status")
    conn.execute("ALTER TABLE project_files DROP COLUMN index_error")
    conn.execute("PRAGMA user_version=9")
    conn.commit()
    conn.close()

    migrated = connect(path)
    assert get_user_version(migrated) == 11
    row = migrated.execute("SELECT id,project_id,path,file_sha,status_tag,indexed_at,source_kind,source_ref FROM documents WHERE id='legacy-upload'").fetchone()
    assert tuple(row) == ("legacy-upload", "audit014-a", "project-files/audit014-a/file-9", "sha", "TXT", "when", "project_file", "file-9")
    first = tuple(migrated.execute("SELECT file_id,project_id,original_name,index_status,indexed FROM project_files").fetchone())
    migrated.close()
    repeated = connect(path)
    second = tuple(repeated.execute("SELECT file_id,project_id,original_name,index_status,indexed FROM project_files").fetchone())
    assert second == first
    assert second == ("file-9", "audit014-a", "note.txt", "pending", 0)
    repeated.close()


class _HybridStore:
    mode = "HYBRID"
    offline_reason = ""
    _vector_error = False

    def __init__(self):
        self.upserted = []

    def remove_by_path(self, path):
        return None

    def upsert(self, items):
        self.upserted.extend(items)


class _Provider:
    def embed(self, value):
        return [0.25, 0.75]


def test_immediate_upload_uses_project_vector_runtime_and_live_status(tmp_path, monkeypatch):
    root, db = tmp_path / "isolated", tmp_path / "isolated" / "data" / "marketing.db"
    root.mkdir()
    monkeypatch.setattr(deps, "ROOT", root)
    monkeypatch.setattr(deps, "DB_PATH", db)
    deps.init_db(db)
    with deps.get_db() as conn:
        _projects(conn)
    store, provider = _HybridStore(), _Provider()
    from app.services import knowledge_index
    monkeypatch.setitem(knowledge_index.RUNTIME_OVERRIDES, "audit014-a", (store, provider))
    app = FastAPI()
    app.include_router(files_router)
    with TestClient(app) as client:
        response = client.post("/files/upload",
            files={"file": ("proof.txt", b"ORANGE-MARKETING-ELEPHANT-927", "text/plain")},
            data={"project_id": "audit014-a", "attach_scope": "project"})
    assert response.status_code == 201, response.text
    body = response.json()["data"]
    assert body["indexed"] is True and body["index_status"] == "indexed"
    assert body["search_mode"] == "HYBRID" and body["vector_status"] == "AVAILABLE"
    assert store.upserted and store.upserted[0][2]["project_id"] == "audit014-a"
    with deps.get_db() as conn:
        a = retrieve_scoped(conn, "ORANGE-MARKETING-ELEPHANT-927", project_id="audit014-a", mode="lexical")
        b = retrieve_scoped(conn, "ORANGE-MARKETING-ELEPHANT-927", project_id="audit014-b", mode="lexical")
    assert a["hits"] and not b["hits"]


def test_nonsearchable_extraction_is_reported_and_not_success(tmp_path, monkeypatch):
    from app.services import knowledge_index
    root = tmp_path / "workspace"
    conn = connect(root / "data" / "marketing.db")
    _projects(conn)
    files_repo.ensure_schema(conn)
    file_id = "empty-extraction"
    payload = b"accepted bytes with no extractable searchable content"
    safe_name = f"{file_id}.txt"
    rel = f"data/projects/audit014-a/files/{safe_name}"
    target = root / rel
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)
    files_repo.insert_file(conn, {
        "file_id": file_id, "project_id": "audit014-a", "original_name": "empty.txt",
        "safe_name": safe_name, "mime_detected": "text/plain", "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(), "kind": "document",
        "extraction": "ready", "attach_scope": "project", "indexed": False,
        "rel_path": rel,
    })
    monkeypatch.setattr(knowledge_index.extract, "extract", lambda *args: knowledge_index.extract.ExtractionResult(
        status="ready", note="no_extractable_text", text=""))

    report = rebuild_project_knowledge(conn, root, "audit014-a",
                                       store=_HybridStore(), provider=_Provider())
    row = conn.execute("SELECT * FROM project_files WHERE file_id=?", (file_id,)).fetchone()
    assert report["discovered"] == 1 and report["indexed"] == 0
    assert report["not_searchable"] == 1 and report["failed"] == 0
    assert report["errors"] == [{"file_id": file_id, "code": "no_extractable_text"}]
    assert knowledge_index.file_index_state(conn, row) == {
        "indexed": False, "index_status": "not_searchable", "index_error": "no_extractable_text"}
    conn.close()


@pytest.mark.parametrize("failure", ["extraction", "index"])
def test_rebuild_isolates_extraction_and_index_exceptions(tmp_path, monkeypatch, failure):
    from app.services import knowledge_index
    root = tmp_path / "workspace"
    conn = connect(root / "data" / "marketing.db")
    _projects(conn)
    files_repo.ensure_schema(conn)
    file_id = f"{failure}-exception"
    payload = b"Synthetic persisted text for isolated failure handling."
    safe_name = f"{file_id}.txt"
    rel = f"data/projects/audit014-a/files/{safe_name}"
    target = root / rel
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)
    files_repo.insert_file(conn, {
        "file_id": file_id, "project_id": "audit014-a", "original_name": safe_name,
        "safe_name": safe_name, "mime_detected": "text/plain", "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(), "kind": "document",
        "extraction": "ready", "attach_scope": "project", "indexed": False,
        "rel_path": rel,
    })
    if failure == "extraction":
        monkeypatch.setattr(knowledge_index.extract, "extract", lambda *args: (_ for _ in ()).throw(RuntimeError("synthetic extraction fault")))
    else:
        monkeypatch.setattr(knowledge_index, "index_file_text", lambda conn, **kwargs: (_ for _ in ()).throw(RuntimeError("synthetic index fault")))

    report = rebuild_project_knowledge(conn, root, "audit014-a",
                                       store=_HybridStore(), provider=_Provider())
    row = conn.execute("SELECT * FROM project_files WHERE file_id=?", (file_id,)).fetchone()
    assert report["failed"] == 1 and report["indexed"] == 0
    assert report["errors"] == [{"file_id": file_id, "code": "reindex_failed_RuntimeError"}]
    assert knowledge_index.file_index_state(conn, row)["indexed"] is False
    assert conn.execute("SELECT COUNT(*) FROM documents WHERE source_ref=?", (file_id,)).fetchone()[0] == 0
    conn.close()

