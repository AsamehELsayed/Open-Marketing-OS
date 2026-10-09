"""W2 scoped retrieval: project_id enforced BEFORE any store query (fail-closed).

Legacy rag_service.retrieve() is preserved byte-for-byte for FTS_ONLY
parity (old tests/callers untouched). New code must call retrieve_scoped()
or ScopedHybridRetriever, which:
- raise ValueError on missing/blank project_id before touching FTS/Chroma;
- filter by project inside the SQL (JOIN documents before LIMIT, never a
  Python post-filter over a global top-k);
- pass project_id as a Chroma `where` filter (server-side before top-k)
  with a contract-level re-check as defense-in-depth;
- fuse via fusion.rrf_fuse (deterministic RRF_K=60 + hierarchy boost).

No DB schema change (documents.path stays UNIQUE globally per W2 scope);
the UNIQUE(project_id,path) migration is deferred and noted in workers/w2.md.
"""
from __future__ import annotations

import sqlite3

from app.services.rag import fusion, rag_service
from app.services.rag.chroma_store import ChromaStore, lexical_index_ready

MODES = ("lexical", "dense", "hybrid")


def require_project_id(project_id: str | None) -> str:
    pid = (project_id or "").strip()
    if not pid:
        raise ValueError("project_id is required before retrieval (fail-closed)")
    return pid


def _fts_scoped_query(conn: sqlite3.Connection, query: str, k: int,
                      project_id: str) -> list[dict]:
    """FTS lookup with project predicate applied in SQL before LIMIT."""
    safe = rag_service._fts_match(query)
    if not safe:
        return []
    sql = (
        "SELECT f.path AS logical_path, f.header AS header, f.text AS text,"
        " snippet(chunks_fts, 0, '<b>', '</b>', '...', 12) AS snippet,"
        " bm25(chunks_fts) AS rank FROM chunks_fts AS f"
        " JOIN documents d ON d.path = f.path"
        " WHERE chunks_fts MATCH ? AND d.project_id = ?"
        " ORDER BY rank LIMIT ?"
    )
    rows = conn.execute(sql, (safe, project_id, k)).fetchall()
    if not rows and len(safe.split()) > 1:
        rows = conn.execute(sql, (rag_service._fts_or_match(query), project_id, k)).fetchall()
    hits = []
    for i, r in enumerate(rows):
        chunk = conn.execute(
            "SELECT d.id AS document_id,c.chunk_id,c.text,d.file_sha,d.status_tag,"
            "d.project_id,d.source_kind,d.source_ref FROM chunks c"
            " JOIN documents d ON d.id = c.document_id"
            " WHERE d.path = ? AND c.header = ? AND c.text = ? AND d.project_id = ? LIMIT 1",
            (r["logical_path"], r["header"], r["text"], project_id),
        ).fetchone()
        if chunk is None:
            continue  # orphan FTS row: fail closed, never surface unscopable evidence
        public_path = (chunk["source_ref"] if chunk["source_kind"] == "workspace"
                       else r["logical_path"])
        hits.append({
            "path": public_path, "header": r["header"], "snippet": r["snippet"],
            "rank": i, "chunk_id": chunk["chunk_id"], "text": chunk["text"],
            "file_sha": chunk["file_sha"], "status_tag": chunk["status_tag"],
            "project_id": project_id, "source": "fts",
            "document_id": chunk["document_id"],
            "file_id": (r["logical_path"].rsplit("/", 1)[-1]
                        if r["logical_path"].startswith("project-files/") else ""),
        })
    return hits


def _semantic_scoped_query(conn: sqlite3.Connection, store, provider,
                           query: str, k: int, project_id: str) -> list[dict]:
    if (store is None or provider is None or not getattr(provider, "semantic", False)
            or store.mode != "HYBRID"
            or getattr(store, "_vector_error", False)):
        return []
    vector = provider.embed_query(query)
    try:
        raw = store.query(vector, k, where={"project_id": project_id})
    except TypeError:
        return []  # Never issue an unscoped query and trim after top-k.
    hits = []
    for i, (key, meta, _dist) in enumerate(raw):
        meta = meta or {}
        if meta.get("project_id") != project_id:
            continue
        logical_path = meta.get("path", "")
        chunk_id = meta.get("chunk_id", "")
        if not logical_path or not chunk_id:
            continue
        chunk = conn.execute(
            "SELECT d.id AS document_id,c.header, c.text, d.file_sha, d.status_tag, d.project_id FROM chunks c"
            " JOIN documents d ON d.id = c.document_id"
            " WHERE d.path = ? AND c.chunk_id = ? AND d.project_id = ? LIMIT 1",
            (logical_path, chunk_id, project_id),
        ).fetchone()
        if (chunk is None or chunk["project_id"] != project_id or meta.get("file_sha") != chunk["file_sha"]
                or key != f"{chunk['document_id']}:{chunk_id}"):
            continue
        source = conn.execute(
            "SELECT source_kind,source_ref FROM documents WHERE path=? AND project_id=? LIMIT 1",
            (logical_path, project_id),
        ).fetchone()
        public_path = (source["source_ref"] if source and source["source_kind"] == "workspace"
                       else logical_path)
        hits.append({
            "path": public_path, "header": chunk["header"] if chunk else "",
            "snippet": (chunk["text"][:200] if chunk else "") + "...",
            "rank": i, "chunk_id": chunk_id,
            "text": chunk["text"] if chunk else "",
            "file_sha": chunk["file_sha"] if chunk else meta.get("file_sha", ""),
            "status_tag": chunk["status_tag"] if chunk else meta.get("status_tag", "UNKNOWN"),
            "project_id": project_id, "source": "semantic",
            "document_id": chunk["document_id"],
            "file_id": (logical_path.rsplit("/", 1)[-1]
                        if logical_path.startswith("project-files/") else ""),
        })
    return hits


def retrieve_scoped(conn: sqlite3.Connection, query: str, *, project_id: str,
                    store=None, provider=None, k_fts: int = 6, k_sem: int = 6,
                    mode: str = "hybrid") -> dict:
    """Scoped retrieval. Raises ValueError when project_id is missing."""
    pid = require_project_id(project_id)
    if mode not in MODES:
        raise ValueError(f"unknown retrieval mode: {mode!r}")
    if not (query or "").strip():
        return {"hits": [], "mode": "FTS_ONLY", "lexical_hits": 0, "vector_hits": 0, "fused_hits": 0}
    errors = {}
    fts_hits, sem_hits = [], []
    lexical_ok = False
    vector_ok = False
    if mode in ("lexical", "hybrid"):
        try:
            fts_hits = _fts_scoped_query(conn, query, k_fts, pid)
            lexical_ok = lexical_index_ready(conn, pid)
            if not lexical_ok:
                errors["lexical"] = "lexical_index_incomplete"
        except Exception as exc:
            errors["lexical"] = f"lexical_query_failed:{type(exc).__name__}"
    if mode in ("dense", "hybrid"):
        try:
            if isinstance(store, ChromaStore):
                store.coverage(conn)
            if store is not None and getattr(provider, "semantic", False) and store.mode == "HYBRID":
                sem_hits = _semantic_scoped_query(conn, store, provider, query, k_sem, pid)
                vector_ok = not getattr(store, "_vector_error", False)
            if not vector_ok:
                errors["vector"] = str(getattr(store, "offline_reason", "semantic_runtime_unavailable"))[:180]
        except Exception as exc:
            errors["vector"] = f"semantic_query_failed:{type(exc).__name__}"
    if mode == "lexical":
        fused = fusion.rrf_fuse([("fts", fts_hits)]) if fts_hits else []
    elif mode == "dense":
        fused = fusion.rrf_fuse([("semantic", sem_hits)]) if sem_hits else []
    else:
        fused = fusion.rrf_fuse([("fts", fts_hits), ("semantic", sem_hits)])
    for h in fused:
        h.setdefault("project_id", pid)
        if h.get("file_id"):
            try:
                row = conn.execute("SELECT original_name FROM project_files WHERE file_id=? AND project_id=?",
                                   (h["file_id"], pid)).fetchone()
                h["file_name"] = row[0] if row else ""
            except sqlite3.Error:
                h["file_name"] = ""
    actual_mode = "HYBRID" if mode == "hybrid" and lexical_ok and vector_ok else "FTS_ONLY"
    return {"hits": fused, "mode": actual_mode,
            "search_mode": "HYBRID" if actual_mode == "HYBRID" else "VECTOR DEGRADED" if vector_ok and not lexical_ok else "FTS",
            "lexical_hits": len(fts_hits), "vector_hits": len(sem_hits), "fused_hits": len(fused),
            "lexical_operational": lexical_ok, "vector_operational": vector_ok, "branch_errors": errors}


class ScopedHybridRetriever:
    """Framework-compatible retriever (BaseRetriever shape) over scoped retrieval."""

    def __init__(self, conn, store=None, provider=None, mode: str = "hybrid"):
        if mode not in MODES:
            raise ValueError(f"unknown retrieval mode: {mode!r}")
        self.conn = conn
        self.store = store
        self.provider = provider
        self.mode = mode

    def invoke(self, query: str, *, project_id: str, k: int = 6) -> list[dict]:
        res = retrieve_scoped(self.conn, query, project_id=project_id,
                              store=self.store, provider=self.provider,
                              k_fts=k, k_sem=k, mode=self.mode)
        return res["hits"]

    # LangChain alias name.
    def get_relevant_documents(self, query: str, *, project_id: str, k: int = 6) -> list[dict]:
        return self.invoke(query, project_id=project_id, k=k)
