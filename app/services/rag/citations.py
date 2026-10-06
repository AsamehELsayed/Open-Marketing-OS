"""W2 citations: every evidence item carries full provenance.

Required keys per rag-plan § isolation/metadata: source_id, project,
path/URL, chunk id, source type, content hash, status, observed/indexed
time, language, stale flag. Missing project_id fails closed.
"""
from __future__ import annotations

import hashlib


def _detect_language(text: str) -> str:
    if any("\u0600" <= ch <= "\u06FF" for ch in text):
        return "ar" if len(text) < 500 or " the " not in text.lower() else "mixed"
    return "en"


def build_citations(hits: list[dict], *, project_id: str) -> list[dict]:
    pid = (project_id or "").strip()
    if not pid:
        raise ValueError("project_id is required for citations (fail-closed)")
    cites = []
    for h in hits:
        if h.get("project_id") and h["project_id"] != pid:
            raise ValueError(f"cross-project citation refused: {h.get('path')}")
        text = h.get("text", "")
        cites.append({
            "source_id": f"{h.get('path', '')}#{h.get('chunk_id', '?')}",
            "project_id": pid,
            "path": h.get("path", ""),
            "chunk_id": h.get("chunk_id", "?"),
            "header": h.get("header", ""),
            "source_type": h.get("source", "fts"),
            "sources": list(h.get("sources", [h.get("source", "fts")])),
            "content_hash": h.get("file_sha", "") or hashlib.sha256(
                text.encode("utf-8", errors="replace")).hexdigest(),
            "status": h.get("status_tag", "UNKNOWN"),
            "observed_at": h.get("indexed_at", ""),
            "indexed_at": h.get("indexed_at", ""),
            "language": h.get("language", "") or _detect_language(
                f"{h.get('header', '')} {text}"),
            "stale": bool(h.get("stale", False)),
            "score": float(h.get("score", 0.0)),
            "snippet": h.get("snippet", ""),
        })
    return cites
