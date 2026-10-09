"""RAGService: hybrid FTS5 + Chroma retrieval with RRF fusion (plan §§8–9).

Every hit carries provenance {path, chunk_id, header, file_sha, status_tag}.
Evidence hierarchy boosts founder-confirmed > durable learnings > verified
brain > snapshots > drafts. NEVER authoritative for structured state —
callers (ContextRouter) combine hits with direct SQLite reads.
"""
import sqlite3

RRF_K = 60

# Additive score boost by source (applied on top of RRF fusion).
HIERARCHY_BOOST = (
    ("founder-decisions", 2.0),
    ("learning-log", 1.5),
    ("knowledge/", 1.0),
    ("product-marketing", 1.0),
    ("research/", 0.5),
)


def hierarchy_boost(path: str) -> float:
    for needle, boost in HIERARCHY_BOOST:
        if needle in path.replace("\\", "/"):
            return boost
    return 0.0


def _fts_match(query: str) -> str:
    """Quote every token so FTS operators (-, OR, *) in user text stay literal."""
    tokens = [t.replace('"', "") for t in query.split() if t.strip('" ')]
    return " ".join(f'"{t}"' for t in tokens)


def _fts_or_match(query: str) -> str:
    tokens = [t.replace('"', "") for t in query.split() if t.strip('" ')]
    return " OR ".join(f'"{t}"' for t in tokens)


def _fts_query(conn: sqlite3.Connection, query: str, k: int, project_id: str | None = None) -> list[dict]:
    safe = _fts_match(query)
    if not safe:
        return []
    rows = conn.execute(
        "SELECT path, header, snippet(chunks_fts, 0, '<b>', '</b>', '...', 12) AS snippet,"
        " bm25(chunks_fts) AS rank FROM chunks_fts WHERE chunks_fts MATCH ?"
        " ORDER BY rank LIMIT ?",
        (safe, k),
    ).fetchall()
    if not rows and len(safe.split()) > 1:
        # Recall aid for natural questions: AND found nothing, retry as OR.
        # Ranking still surfaces the best-matching chunk first.
        rows = conn.execute(
            "SELECT path, header, snippet(chunks_fts, 0, '<b>', '</b>', '...', 12) AS snippet,"
            " bm25(chunks_fts) AS rank FROM chunks_fts WHERE chunks_fts MATCH ?"
            " ORDER BY rank LIMIT ?",
            (_fts_or_match(query), k),
        ).fetchall()
    hits = []
    for i, r in enumerate(rows):
        chunk = conn.execute(
            "SELECT c.chunk_id, c.text, d.file_sha, d.status_tag, d.project_id,"
            " d.source_kind, d.source_ref FROM chunks c"
            " JOIN documents d ON d.id = c.document_id"
            " WHERE d.path = ? AND c.header = ? LIMIT 1",
            (r["path"], r["header"]),
        ).fetchone()
        if project_id is not None and chunk is not None:
            try:
                if (chunk["project_id"] or "") != project_id:
                    continue
            except Exception:
                pass
        logical_path = r["path"]
        public_path = (chunk["source_ref"] if chunk and chunk["source_kind"] == "workspace"
                       else logical_path)
        hits.append({
            "path": public_path, "_logical_path": logical_path,
            "header": r["header"], "snippet": r["snippet"],
            "rank": i,
            "chunk_id": chunk["chunk_id"] if chunk else "?",
            "text": chunk["text"] if chunk else "",
            "file_sha": chunk["file_sha"] if chunk else "",
            "status_tag": chunk["status_tag"] if chunk else "UNKNOWN",
            "source": "fts",
        })
    return hits


def _semantic_query(conn: sqlite3.Connection, store, provider, query: str, k: int,
                    project_id: str | None = None) -> list[dict]:
    if store is None or store.mode != "HYBRID":
        return []
    if not project_id:
        # Chroma requires the project filter before top-k, so never query it
        # without an explicit scope.
        return []
    vector = provider.embed(query)
    hits = []
    for i, (key, meta, _dist) in enumerate(
        store.query(vector, k, where={"project_id": project_id})
    ):
        if project_id is not None and (meta or {}).get("project_id"):
            if (meta or {}).get("project_id") != project_id:
                continue
        logical_path = (meta or {}).get("path", "")
        chunk_id = (meta or {}).get("chunk_id", key.split(":")[-1])
        chunk = conn.execute(
            "SELECT c.header, c.text, d.file_sha, d.status_tag FROM chunks c"
            " JOIN documents d ON d.id = c.document_id"
            " WHERE d.path = ? AND c.chunk_id = ? LIMIT 1",
            (logical_path, chunk_id),
        ).fetchone()
        source = conn.execute(
            "SELECT source_kind,source_ref FROM documents WHERE path=? AND project_id=? LIMIT 1",
            (logical_path, project_id or (meta or {}).get("project_id", "")),
        ).fetchone()
        public_path = (source["source_ref"] if source and source["source_kind"] == "workspace"
                       else logical_path)
        hits.append({
            "path": public_path, "_logical_path": logical_path,
            "header": chunk["header"] if chunk else "",
            "snippet": (chunk["text"][:200] if chunk else "") + "...",
            "rank": i, "chunk_id": chunk_id,
            "text": chunk["text"] if chunk else "",
            "file_sha": chunk["file_sha"] if chunk else (meta or {}).get("file_sha", ""),
            "status_tag": chunk["status_tag"] if chunk else (meta or {}).get("status_tag", "UNKNOWN"),
            "source": "semantic",
        })
    return hits


def retrieve(conn: sqlite3.Connection, query: str, store=None, provider=None,
             k_fts: int = 6, k_sem: int = 6, project_id: str | None = None) -> dict:
    """Return {hits: [fused, ranked], mode}. RRF over both lists + hierarchy boost."""
    fts_hits = _fts_query(conn, query, k_fts, project_id)
    sem_hits = _semantic_query(conn, store, provider, query, k_sem, project_id) if provider else []
    fused: dict[tuple[str, str], dict] = {}
    for h in fts_hits + sem_hits:
        logical_path = h.get("_logical_path") or h["path"]
        key = (logical_path, h["chunk_id"])
        score = 1.0 / (RRF_K + h["rank"]) + hierarchy_boost(logical_path)
        if key in fused:
            fused[key]["score"] += score
            fused[key]["sources"] = sorted(set(fused[key]["sources"]) | {h["source"]})
        else:
            h["score"] = score
            h["sources"] = [h["source"]]
            fused[key] = h
    hits = sorted(fused.values(), key=lambda h: -h["score"])
    for hit in hits:
        hit.pop("_logical_path", None)
    return {"hits": hits, "mode": store.mode if store else "FTS_ONLY"}
