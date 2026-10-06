"""W2 eval harness: deterministic, repeatable, offline.

evaluate(cases, retrieve_fn) runs a frozen case list through any
retrieval function and returns metric JSON:
  recall@5, mrr, ndcg@5, citation_correctness, groundedness_proxy,
  no_answer_honesty, leakage (must be 0), p50_ms/p95_ms, per-language
  slices (ar/en/mixed — never averaged away).

No model calls, no network. retrieve_fn(query, k) -> list of hit dicts
with at least path/chunk_id/project_id.
"""
from __future__ import annotations

import math
import time
from typing import Callable

from app.database.identity import DEFAULT_PROJECT_ID


def _recall(expected: str, hits: list[dict], k: int) -> float:
    top = [h.get("path", "") for h in hits[:k]]
    return 1.0 if expected and expected in top else 0.0


def _rr(hits: list[dict], expected: str) -> float:
    for i, h in enumerate(hits):
        if expected and h.get("path", "") == expected:
            return 1.0 / (i + 1)
    return 0.0


def _dcg(hits: list[dict], expected: str, k: int) -> float:
    score = 0.0
    for i, h in enumerate(hits[:k]):
        if expected and h.get("path", "") == expected:
            score += 1.0 / math.log2(i + 2)
    return score


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(math.ceil(pct / 100 * len(ordered)) - 1))
    return ordered[max(0, idx)]


def evaluate(cases: list[dict], retrieve_fn: Callable[..., list[dict]],
             k: int = 5, project_id: str = DEFAULT_PROJECT_ID) -> dict:
    recalls, rrs, ndcgs = [], [], []
    no_answer_ok, no_answer_total = 0, 0
    cite_ok, cite_total = 0, 0
    leakage = 0
    latencies: list[float] = []
    by_lang: dict[str, dict] = {}

    for case in cases:
        q = case["query"]
        t0 = time.perf_counter()
        hits = retrieve_fn(q, k=k) or []
        latencies.append((time.perf_counter() - t0) * 1000.0)
        for h in hits:
            if h.get("project_id") and h["project_id"] != project_id:
                leakage += 1
        lang = case.get("language", "en")
        slot = by_lang.setdefault(lang, {"n": 0, "recall": 0.0})
        slot["n"] += 1
        if case.get("retrieval_expectation") == "no-answer":
            no_answer_total += 1
            if not hits:
                no_answer_ok += 1
            continue
        expected = case.get("expected_top_path", "")
        r = _recall(expected, hits, k)
        recalls.append(r)
        slot["recall"] += r
        rrs.append(_rr(hits, expected))
        ideal = 1.0  # single relevant doc → ideal DCG@k = 1/log2(2) = 1
        ndcgs.append(_dcg(hits, expected, k) / ideal)
        cite_total += 1
        if hits and all(h.get("path") and h.get("chunk_id") for h in hits[:1]):
            cite_ok += 1

    for slot in by_lang.values():
        slot["recall"] = slot["recall"] / slot["n"] if slot["n"] else 0.0

    return {
        "n": len(cases),
        "recall@5": sum(recalls) / len(recalls) if recalls else 0.0,
        "mrr": sum(rrs) / len(rrs) if rrs else 0.0,
        "ndcg@5": sum(ndcgs) / len(ndcgs) if ndcgs else 0.0,
        "citation_correctness": cite_ok / cite_total if cite_total else 1.0,
        "groundedness_proxy": sum(recalls) / len(recalls) if recalls else 0.0,
        "no_answer_honesty": no_answer_ok / no_answer_total if no_answer_total else 1.0,
        "leakage": leakage,
        "p50_ms": _percentile(latencies, 50),
        "p95_ms": _percentile(latencies, 95),
        "by_language": by_lang,
    }
