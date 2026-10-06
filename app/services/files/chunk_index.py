"""Project-scoped chunking + FTS index for uploaded files.

Reuses rag primitives: _chunk_markdown for markdown, RecursiveLanguageSplitter
for everything else, find_secrets for quarantine, retrieve_scoped's JOIN
pattern for isolation. Documents/chunks/FTS rows are written under a
`project-files/...` path (never a workspace path) with project_id set
explicitly (chunks defaulting to the default project would leak cross-project otherwise).
"""
import uuid
from datetime import datetime, timezone

from app.services.rag import secrets as secret_scan
from app.services.rag.indexing_service import _chunk_markdown
from app.services.rag.splitting import RecursiveLanguageSplitter
from app.services.rag.chroma_store import ChromaStore

# Logical citation path — NOT a filesystem path. Safe for chat/LLM payloads.
PATH_PREFIX = "project-files"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def chunk_text(text: str, mime: str) -> list[tuple[str, str]]:
    """-> [(header, body)]. Markdown uses H1-H3 header paths; others use
    the recursive language splitter with header='' (citations fall back to
    path)."""
    if not (text or "").strip():
        return []
    if mime == "text/markdown":
        return [(h, b) for h, b in _chunk_markdown(text) if b]
    splitter = RecursiveLanguageSplitter(chunk_size=1000, chunk_overlap=200)
    return [("", body) for body in splitter.split_text(text)]


def index_file_text(
    conn,
    *,
    file_id: str,
    project_id: str,
    original_name: str,
    sha256: str,
    text: str,
    mime: str,
    vector_store=None,
    provider=None,
) -> dict:
    """Chunk + insert documents/chunks/chunks_fts for one uploaded file.

    Returns {"indexed": bool, "chunks": int, "quarantined": list[str]}.
    Secret hits -> quarantine (no rows). Empty chunks -> not indexed.
    Fail-closed on blank project_id.
    """
    pid = (project_id or "").strip()
    if not pid:
        raise ValueError("project_id required for indexing (fail-closed)")
    path = f"{PATH_PREFIX}/{pid}/{file_id}"

    hits = secret_scan.find_secrets(text or "")
    if hits:
        return {"indexed": False, "chunks": 0, "quarantined": hits,
                "path": path}

    pairs = chunk_text(text, mime)
    if not pairs:
        return {"indexed": False, "chunks": 0, "quarantined": [],
                "path": path}

    # idempotent re-index of the same file_id
    old = conn.execute("SELECT id, project_id, source_kind, source_ref FROM documents WHERE path = ?",
                       (path,)).fetchone()
    if old:
        if (old["project_id"] != pid or old["source_kind"] != "project_file"
                or old["source_ref"] != file_id):
            raise ValueError("document ownership conflict")
        doc_id = old["id"]
    else:
        doc_id = uuid.uuid4().hex
    conn.execute("SAVEPOINT index_file_text")
    vector_inputs = []
    vector_failed = False
    try:
        if old:
            conn.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
            conn.execute("DELETE FROM chunks_fts WHERE path = ?", (path,))
            conn.execute(
                "UPDATE documents SET file_sha = ?, status_tag = ?, indexed_at = ? WHERE id = ?",
                (sha256, "UNKNOWN", _now(), doc_id),
            )
        else:
            conn.execute(
                "INSERT INTO documents (id, project_id, path, source_kind, source_ref, file_sha,"
                " status_tag, indexed_at) VALUES (?,?,?,?,?,?,?,?)",
                (doc_id, pid, path, "project_file", file_id, sha256, "UNKNOWN", _now()),
            )
        for i, (header, body) in enumerate(pairs):
            chunk_id = f"c{i:03d}"
            conn.execute(
                "INSERT INTO chunks (id, project_id, document_id, chunk_id,"
                " header, text, token_est) VALUES (?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, pid, doc_id, chunk_id, header, body,
                 max(1, len(body) // 4)),
            )
            conn.execute(
                "INSERT INTO chunks_fts (text, header, path) VALUES (?,?,?)",
                (body, header, path),
            )
            if vector_store is not None and provider is not None:
                vector_inputs.append((f"{doc_id}:{chunk_id}", f"{header}\n{body}",
                                      {"path": path, "document_id": doc_id, "chunk_id": chunk_id, "project_id": pid,
                                       "status_tag": "UNKNOWN", "file_sha": sha256}))
        # Commit the source-derived expected count with the complete lexical set.
        conn.execute("UPDATE documents SET expected_chunk_count=? WHERE id=?",
                     (len(pairs), doc_id))
        conn.execute("RELEASE SAVEPOINT index_file_text")
        conn.commit()
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT index_file_text")
        conn.execute("RELEASE SAVEPOINT index_file_text")
        raise
    if isinstance(vector_store, ChromaStore):
        vector_store.sync_document(conn, path, provider)
        return {"indexed": True, "chunks": len(pairs), "quarantined": [],
                "path": path, "vector_failed": vector_store._vector_error}
    if old and vector_store is not None:
        try:
            vector_store.remove_by_path(path)
        except Exception:
            vector_failed = True
    vectors = []
    if vector_store is not None and provider is not None and not vector_failed:
        try:
            encoded = (provider.embed_documents([text for _, text, _ in vector_inputs])
                       if hasattr(provider, "embed_documents") else
                       [provider.embed(text) for _, text, _ in vector_inputs])
            if len(encoded) != len(vector_inputs):
                raise RuntimeError("embedding_batch_incomplete")
            vectors = [(key, vector, metadata) for (key, _, metadata), vector in zip(vector_inputs, encoded)]
        except Exception:
            vector_failed = True
    if vectors and vector_store is not None and not vector_failed:
        try:
            vector_store.upsert(vectors)
        except Exception:
            vector_failed = True
    if vector_failed and vector_store is not None:
        try:
            vector_store._vector_error = True
            vector_store.offline_reason = "vector operation failed"
        except Exception:
            pass
    return {"indexed": True, "chunks": len(pairs), "quarantined": [],
            "path": path, "vector_failed": vector_failed}


def chat_file_summary(row: dict) -> dict:
    """LLM/chat-safe metadata: file_id + metadata only. Never rel_path,
    safe_name (disk layout), or any filesystem path."""
    return {
        "file_id": row.get("file_id", ""),
        "original_name": row.get("original_name", ""),
        "mime_detected": row.get("mime_detected", ""),
        "kind": row.get("kind", ""),
        "size": row.get("size", 0),
        "sha256": row.get("sha256", ""),
        "width": row.get("width"),
        "height": row.get("height"),
        "extraction": row.get("extraction", ""),
        "attach_scope": row.get("attach_scope", ""),
        "indexed": bool(row.get("indexed")),
    }
