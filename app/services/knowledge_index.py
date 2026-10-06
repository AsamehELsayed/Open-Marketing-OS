"""Project-scoped knowledge indexing and truthful file/runtime status."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from app.services.files import extract, limits, repo, sniff
from app.services.files.ingest import _check_sizes, _sniff
from app.services.files.chunk_index import chunk_text, index_file_text
from app.services.rag import secrets as secret_scan
from app.services.rag.chroma_store import ChromaStore, lexical_index_ready
from app.services.rag.embeddings import LocalMultilingualE5Embeddings
from app.services.rag.indexing_service import IndexingService

# Tests and embedding hosts may replace construction without changing callers.
RUNTIME_OVERRIDES: dict[str, tuple[object, object]] = {}
_MIME_LABELS = {
    "application/pdf": "PDF", "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "DOCX",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "XLSX",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "PPTX",
    "text/plain": "TXT", "text/markdown": "MD", "text/csv": "CSV",
    "image/png": "PNG", "image/jpeg": "JPEG", "image/gif": "GIF", "image/webp": "WEBP",
}
_MIME_EXTENSIONS = {
    "application/pdf": [".pdf"],
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": [".pptx"],
    "text/plain": [".txt", ".text"], "text/markdown": [".md", ".markdown"],
    "text/csv": [".csv"], "image/png": [".png"], "image/jpeg": [".jpg", ".jpeg"],
    "image/gif": [".gif"], "image/webp": [".webp"],
}


def _require_project(project_id: str | None) -> str:
    pid = (project_id or "").strip()
    if not pid or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", pid) or pid in (".", ".."):
        raise ValueError("valid project_id is required")
    return pid


def retrieval_runtime(root, project_id, *, progress_callback=None):
    pid = _require_project(project_id)
    if pid in RUNTIME_OVERRIDES:
        return RUNTIME_OVERRIDES[pid]
    provider = LocalMultilingualE5Embeddings(progress_callback=progress_callback)
    store = ChromaStore(Path(root) / "data" / "chroma" / pid, provider, project_id=pid)
    return store, provider


def retrieval_status(root, project_id, conn=None) -> dict:
    try:
        store, _provider = retrieval_runtime(root, project_id)
        return retrieval_status_for_store(store, conn=conn, project_id=project_id)
    except Exception as exc:
        lexical_ready = bool(conn is not None and lexical_index_ready(conn, project_id))
        return {"search_mode": "FTS" if lexical_ready else "UNAVAILABLE",
                "vector_status": "FAILED",
                "vector_reason": (f"runtime_failed:{type(exc).__name__}" if lexical_ready else
                                  f"runtime_failed:{type(exc).__name__};lexical_index_unavailable")}


def retrieval_status_for_store(store, conn=None, project_id=None) -> dict:
    pid = project_id or getattr(store, "project_id", None)
    if conn is not None and pid:
        if isinstance(store, ChromaStore):
            store.coverage(conn)
        lexical_ready = lexical_index_ready(conn, pid)
    else:
        # Vector coverage does not prove that the lexical index is usable. Callers
        # without a DB connection may report only readiness cached explicitly by
        # their owner; otherwise fail closed.
        lexical_ready = bool(getattr(store, "_lexical_ready", False))
    mode = getattr(store, "mode", "FTS_ONLY")
    failed = bool(getattr(store, "_vector_error", False))
    if failed:
        mode = "FTS_ONLY"
    vector_ready = mode == "HYBRID" and not failed
    if vector_ready and lexical_ready:
        search_mode = "HYBRID"
    elif vector_ready:
        search_mode = "VECTOR DEGRADED"
    elif lexical_ready:
        search_mode = "FTS"
    else:
        search_mode = "UNAVAILABLE"
    reason = "" if search_mode in ("HYBRID", "FTS") else str(
        getattr(store, "offline_reason", "vector runtime unavailable")
    )
    if search_mode in ("VECTOR DEGRADED", "UNAVAILABLE"):
        reason = f"{reason};lexical_index_unavailable" if reason else "lexical_index_unavailable"
    return {"search_mode": search_mode,
            "vector_status": "AVAILABLE" if vector_ready else ("FAILED" if failed else "NOT_AVAILABLE"),
            "vector_reason": reason[:180]}


def file_capabilities() -> dict:
    accepted = []
    searchable = {"application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                  "text/plain", "text/markdown", "text/csv"}
    for mime in sorted(sniff.ALLOWED_MIMES):
        label = _MIME_LABELS.get(mime)
        if not label:
            continue
        image = mime.startswith("image/")
        accepted.append({"label": label, "extensions": _MIME_EXTENSIONS.get(mime, []),
                         "mime": mime, "searchable_text": mime in searchable,
                         "max_bytes": limits.MAX_IMAGE_BYTES if image else limits.MAX_DOCUMENT_BYTES})
    return {"accepted_types": accepted, "max_document_bytes": limits.MAX_DOCUMENT_BYTES,
            "max_image_bytes": limits.MAX_IMAGE_BYTES, "ocr_supported": False}


def _searchable(conn, row) -> bool:
    if not row:
        return False
    docs = conn.execute("SELECT id,path,expected_chunk_count FROM documents WHERE project_id=? AND source_kind='project_file' AND source_ref=?",
                         (row["project_id"], row["file_id"])).fetchall()
    if len(docs) != 1:
        return False
    doc = docs[0]
    chunks = conn.execute("SELECT project_id,chunk_id,header,text FROM chunks WHERE document_id=?",
                           (doc["id"],)).fetchall()
    if (not chunks or doc["expected_chunk_count"] is None
            or len(chunks) != doc["expected_chunk_count"]
            or any(chunk["project_id"] != row["project_id"] for chunk in chunks)):
        return False
    expected = Counter((chunk["header"], chunk["text"]) for chunk in chunks)
    actual = Counter((fts["header"], fts["text"]) for fts in conn.execute(
        "SELECT header,text FROM chunks_fts WHERE path=?", (doc["path"],)).fetchall())
    return actual == expected


def _document_matches(conn, doc, project_id: str, expected_pairs) -> bool:
    """Compare persisted chunk and FTS multisets against validated extracted text."""
    chunks = conn.execute(
        "SELECT project_id,chunk_id,header,text FROM chunks WHERE document_id=?", (doc["id"],)
    ).fetchall()
    expected_chunks = Counter((project_id, f"c{i:03d}", header, text)
                              for i, (header, text) in enumerate(expected_pairs))
    actual_chunks = Counter((r["project_id"], r["chunk_id"], r["header"], r["text"]) for r in chunks)
    if doc["expected_chunk_count"] != len(expected_pairs) or actual_chunks != expected_chunks:
        return False
    expected_fts = Counter((header, text) for header, text in expected_pairs)
    actual_fts = Counter((r["header"], r["text"]) for r in conn.execute(
        "SELECT header,text FROM chunks_fts WHERE path=?", (doc["path"],)).fetchall())
    return actual_fts == expected_fts


def file_index_state(conn, row) -> dict:
    exists = _searchable(conn, row)
    prior = row["index_status"] if "index_status" in row.keys() else "pending"
    if exists:
        status, error = "indexed", ""
    elif prior in {"failed", "quarantined", "not_searchable"}:
        status, error = prior, (row["index_error"] if "index_error" in row.keys() else "")
    else:
        status, error = "pending", ""
    return {"indexed": exists, "index_status": status, "index_error": error}


def _remove_upload_index(conn, file_row, store):
    docs = conn.execute("SELECT id,path FROM documents WHERE project_id=? AND source_kind='project_file' AND source_ref=?",
                        (file_row["project_id"], file_row["file_id"])).fetchall()
    for doc in docs:
        conn.execute("DELETE FROM chunks WHERE document_id=?", (doc["id"],))
        conn.execute("DELETE FROM documents WHERE id=?", (doc["id"],))
        conn.execute("DELETE FROM chunks_fts WHERE path=?", (doc["path"],))
        if store is not None:
            try:
                store.remove_by_path(doc["path"])
            except Exception:
                try:
                    store._vector_error = True
                    store.offline_reason = "vector operation failed"
                except Exception:
                    pass
    canonical_path = f"project-files/{file_row['project_id']}/{file_row['file_id']}"
    conn.execute("DELETE FROM chunks_fts WHERE path=?", (canonical_path,))
    if store is not None and not any(d["path"] == canonical_path for d in docs):
        try:
            store.remove_by_path(canonical_path)
        except Exception:
            try:
                store._vector_error = True
                store.offline_reason = "vector operation failed"
            except Exception:
                pass
    return bool(docs)


def _set_file_state(conn, file_id, status, error=""):
    conn.execute("UPDATE project_files SET indexed=?,index_status=?,index_error=? WHERE file_id=?",
                 (int(status == "indexed"), status, error, file_id))


def _rebuild_upload(conn, root: Path, row, store, provider) -> tuple[str, str, int, bool]:
    pid = row["project_id"]
    expected = Path("data") / "projects" / pid / "files" / row["safe_name"]
    if Path(row["rel_path"]).as_posix() != expected.as_posix():
        _remove_upload_index(conn, row, store)
        _set_file_state(conn, row["file_id"], "failed", "invalid_stored_path")
        conn.commit()
        return "failed", "invalid_stored_path", 0, False
    base = (root / "data" / "projects" / pid / "files").resolve()
    target = (root / row["rel_path"]).resolve()
    if base not in target.parents or not target.is_file():
        _remove_upload_index(conn, row, store)
        _set_file_state(conn, row["file_id"], "failed", "stored_bytes_missing")
        conn.commit()
        return "failed", "stored_bytes_missing", 0, False
    try:
        data = target.read_bytes()
        checked = _sniff(data, row["original_name"])
        _check_sizes(data, checked.kind)
        if checked.mime != row["mime_detected"] or len(data) != row["size"]:
            raise ValueError("stored_metadata_mismatch")
        from app.services.files.hashing import sha256_hex
        digest = sha256_hex(data)
        if digest != row["sha256"]:
            raise ValueError("stored_hash_mismatch")
        extracted = extract.extract(data, checked.mime, row["original_name"])
        if checked.kind != "document":
            _remove_upload_index(conn, row, store)
            status, code = "not_searchable", "not_searchable"
            _set_file_state(conn, row["file_id"], status, code)
            conn.commit()
            return status, code, 0, False
        if extracted.note == "no_extractable_text" or (extracted.status in ("ready", "stripped") and not extracted.text):
            _remove_upload_index(conn, row, store)
            _set_file_state(conn, row["file_id"], "not_searchable", "no_extractable_text")
            conn.commit()
            return "not_searchable", "no_extractable_text", 0, False
        if extracted.status not in ("ready", "stripped"):
            _remove_upload_index(conn, row, store)
            _set_file_state(conn, row["file_id"], "failed", "extraction_failed")
            conn.commit()
            return "failed", "extraction_failed", 0, False
        if secret_scan.find_secrets(extracted.text):
            _remove_upload_index(conn, row, store)
            _set_file_state(conn, row["file_id"], "quarantined", "secret_detected")
            conn.commit()
            return "quarantined", "secret_detected", 0, False
        existing_rows = conn.execute(
            "SELECT d.id,d.path,d.file_sha,d.expected_chunk_count FROM documents d WHERE d.project_id=? AND d.source_kind='project_file' AND d.source_ref=?",
            (pid, row["file_id"])).fetchall()
        expected_pairs = chunk_text(extracted.text, checked.mime)
        existing = existing_rows[0] if len(existing_rows) == 1 else None
        if existing and existing["file_sha"] == digest and _document_matches(conn, existing, pid, expected_pairs):
            count = len(expected_pairs)
            if count:
                _set_file_state(conn, row["file_id"], "indexed", "")
                conn.commit()
                return "indexed", "", count, True
        canonical_path = f"project-files/{pid}/{row['file_id']}"
        _set_file_state(conn, row["file_id"], "pending", "")
        if len(existing_rows) > 1 or (existing and existing["path"] != canonical_path):
            _remove_upload_index(conn, row, store)
        result = index_file_text(conn, file_id=row["file_id"], project_id=pid,
                                 original_name=row["original_name"], sha256=digest,
                                 text=extracted.text, mime=checked.mime,
                                 vector_store=store, provider=provider)
        if not result.get("indexed"):
            _remove_upload_index(conn, row, store)
            status, code = "not_searchable", "empty_extraction"
        else:
            status, code = "indexed", ""
        _set_file_state(conn, row["file_id"], status, code)
        conn.commit()
        return status, code, int(result.get("chunks", 0)), False
    except Exception as exc:
        conn.rollback()
        _remove_upload_index(conn, row, store)
        code = getattr(exc, "code", f"reindex_failed_{type(exc).__name__}")
        _set_file_state(conn, row["file_id"], "failed", str(code)[:64])
        conn.commit()
        return "failed", str(code)[:64], 0, False


def rebuild_project_knowledge(conn, root, project_id, *, store=None, provider=None) -> dict:
    pid = _require_project(project_id)
    root = Path(root)
    if store is None or provider is None:
        actual_store, actual_provider = retrieval_runtime(root, pid)
        store = store or actual_store
        provider = provider or actual_provider
    workspace = IndexingService(conn, root, vector_store=store, provider=provider, project_id=pid).build_or_update()
    rows = conn.execute("SELECT * FROM project_files WHERE project_id=? AND attach_scope='project' ORDER BY file_id",
                        (pid,)).fetchall()
    upload = {"discovered": len(rows), "indexed": 0, "skipped": 0, "deleted": 0,
              "quarantined": 0, "failed": 0, "not_searchable": 0, "chunks": 0}
    errors = []
    for row in rows:
        try:
            status, code, count, skipped = _rebuild_upload(conn, root, row, store, provider)
            upload["chunks"] += count
            if status == "indexed":
                if skipped:
                    upload["skipped"] += 1
                else:
                    upload["indexed"] += 1
            elif status == "quarantined":
                upload["quarantined"] += 1
                errors.append({"file_id": row["file_id"], "code": code})
            elif status == "failed":
                upload["failed"] += 1
                errors.append({"file_id": row["file_id"], "code": code})
            elif status == "not_searchable":
                upload["not_searchable"] += 1
                errors.append({"file_id": row["file_id"], "code": code})
        except Exception:
            _remove_upload_index(conn, row, store)
            _set_file_state(conn, row["file_id"], "failed", "reindex_failed")
            conn.commit()
            upload["failed"] += 1
            errors.append({"file_id": row["file_id"], "code": "reindex_failed"})
    if isinstance(store, ChromaStore):
        store.reconcile(conn)
    runtime = retrieval_status_for_store(store, conn=conn, project_id=pid)
    chunks = conn.execute("SELECT COUNT(*) FROM chunks WHERE project_id=?", (pid,)).fetchone()[0]
    discovered = workspace["discovered"] + upload["discovered"]
    indexed = workspace["indexed"] + upload["indexed"]
    errors = workspace.get("errors", []) + errors
    return {"project_id": pid, "discovered": discovered, "indexed": indexed,
            "skipped": workspace["skipped"] + upload["skipped"],
            "deleted": workspace["deleted"] + upload["deleted"],
            "quarantined": workspace["quarantined"] + upload["quarantined"],
            "failed": workspace["failed"] + upload["failed"],
            "not_searchable": upload["not_searchable"], "chunks": chunks,
            **runtime,
            "errors": errors, "workspace": workspace, "uploads": upload}


__all__ = ["retrieval_runtime", "retrieval_status", "file_capabilities", "file_index_state",
           "retrieval_status_for_store", "rebuild_project_knowledge"]
