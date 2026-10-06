"""W2 deterministic RRF fusion (extracted from rag_service, behavior preserved).

RRF_K=60 + hierarchy boost identical to legacy. Determinism: ties broken
by (path, chunk_id) so repeated runs and reversed input orders fuse to the
same ranking. Pure function — no DB, no network.
"""
from app.services.rag.rag_service import HIERARCHY_BOOST, RRF_K, hierarchy_boost


def rrf_fuse(ranked_lists: list[tuple[str, list[dict]]]) -> list[dict]:
    """Fuse named ranked lists. Each hit needs path+chunk_id+rank(+source)."""
    fused: dict[tuple[str, str, str], dict] = {}
    for source_name, hits in ranked_lists:
        for h in hits:
            key = (h.get("project_id", ""), h.get("path", ""), h.get("chunk_id", "?"))
            score = 1.0 / (RRF_K + int(h.get("rank", 0))) + hierarchy_boost(
                h.get("path", ""))
            if key in fused:
                fused[key]["score"] += score
                fused[key]["sources"] = sorted(
                    set(fused[key]["sources"]) | {h.get("source", source_name)})
            else:
                row = dict(h)
                row["score"] = score
                row["sources"] = [h.get("source", source_name)]
                fused[key] = row
    # Deterministic: score desc, then (path, chunk_id) asc.
    return sorted(fused.values(),
                  key=lambda h: (-h["score"], h.get("path", ""), h.get("chunk_id", "")))


__all__ = ["RRF_K", "HIERARCHY_BOOST", "hierarchy_boost", "rrf_fuse"]
