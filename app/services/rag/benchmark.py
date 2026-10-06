"""W2 benchmark hooks: compare lexical / dense / hybrid legs, offline.

compare() runs the same query through each retrieval leg and returns
{leg: {hits, mode, latency_ms}} plus eval metrics when cases are supplied.
No model download, no network, no spend.
"""
from __future__ import annotations

import time

from app.services.rag import eval as rag_eval
from app.services.rag import scoped_retrieval


def _timed(fn, *args, **kwargs):
    t0 = time.perf_counter()
    out = fn(*args, **kwargs)
    return out, (time.perf_counter() - t0) * 1000.0


def compare(conn, query: str, *, project_id: str, store=None, provider=None,
            k: int = 6, cases: list[dict] | None = None) -> dict:
    legs: dict[str, dict] = {}
    for leg, mode in (("lexical", "lexical"), ("dense", "dense"), ("hybrid", "hybrid")):
        try:
            res, ms = _timed(scoped_retrieval.retrieve_scoped, conn, query,
                             project_id=project_id, store=store, provider=provider,
                             k_fts=k, k_sem=k, mode=mode)
        except Exception as e:  # never throw from a benchmark hook
            res, ms = {"hits": [], "mode": "FTS_ONLY", "error": str(e)}, 0.0
        legs[leg] = {"hits": res.get("hits", []), "mode": res.get("mode", "FTS_ONLY"),
                     "latency_ms": round(ms, 3)}
        if "error" in res:
            legs[leg]["error"] = res["error"]
    if cases:
        for leg, mode in (("lexical", "lexical"), ("dense", "dense"), ("hybrid", "hybrid")):
            def _fn(q, k=k, _m=mode):
                return scoped_retrieval.retrieve_scoped(
                    conn, q, project_id=project_id, store=store,
                    provider=provider, k_fts=k, k_sem=k, mode=_m)["hits"]
            legs[leg]["metrics"] = rag_eval.evaluate(cases, _fn, k=k, project_id=project_id)
    return legs
