"""LEGACY/test fallback: ThreadPoolExecutor fan-out / fan-in.

Kept for unit tests and offline fallback only — never the v1 product
path. v1 traffic uses app.graphs.deep_research_graph.build_deep_research_graph
(real StateGraph with Send + reducer fan-in).

Planner decomposes into <= cap research_tasks (cap from RuntimeConfig,
hard cap 4). Workers run in a real ThreadPoolExecutor with measured
overlap; the reducer dedupes, fuses, and cites. Failed workers are
recorded and excluded — dependents of failed workers stay blocked.
Workers never sub-delegate (no nested fan-out).
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.contracts.events import safe_error_message, sanitize_user_text
from app.contracts.runtime import (
    MAX_AGENT_CONCURRENCY_HARD_CAP,
    RuntimeConfig,
)


def _cap(config: RuntimeConfig | None) -> int:
    try:
        n = int((config.max_agent_concurrency if config else 4))
    except Exception:
        n = 4
    return max(1, min(MAX_AGENT_CONCURRENCY_HARD_CAP, n))


def decompose(question: str, *, max_tasks: int = 4,
              config: RuntimeConfig | None = None) -> list[dict]:
    """Split a question into bounded research tasks (deterministic)."""
    cap = _cap(config)
    want = max(1, min(int(max_tasks or 1), cap))
    text = (question or "").strip()
    if not text:
        return [{"id": "q0", "query": ""}]
    parts = [p.strip() for p in re.split(r"[;?\n]+", text) if p.strip()]
    if len(parts) <= 1:
        words = text.split()
        if len(words) > 24:
            mid = len(words) // 2
            parts = [" ".join(words[:mid]), " ".join(words[mid:])]
        else:
            parts = [text]
    tasks = [{"id": f"q{i}", "query": p} for i, p in enumerate(parts[:want])]
    return tasks or [{"id": "q0", "query": text}]


def fan_out_and_collect(tasks: list[dict], worker, *,
                        config: RuntimeConfig | None = None) -> list[dict]:
    """Run worker(task, idx) in parallel, capped. Never raises for workers."""
    cap = _cap(config)
    jobs = list(tasks or [])[: max(cap, 1)]
    if not jobs:
        return []
    workers = max(1, min(len(jobs), cap))
    results: list[dict | None] = [None] * len(jobs)

    def _run(idx: int, task: dict) -> dict:
        try:
            out = worker(task, idx)
        except Exception as e:
            return {"task_id": sanitize_user_text(task.get("id", f"q{idx}"), 120),
                    "chunk": "", "sources": [], "ok": False,
                    "error": safe_error_message(e, "Research worker failed.")}
        if not isinstance(out, dict):
            return {"task_id": task.get("id", f"q{idx}"), "chunk": "",
                    "sources": [], "ok": False, "error": "worker must return dict"}
        out.setdefault("task_id", task.get("id", f"q{idx}"))
        out.setdefault("chunk", "")
        out.setdefault("sources", [])
        out.setdefault("ok", True)
        out["task_id"] = sanitize_user_text(out.get("task_id", ""), 120)
        out["chunk"] = sanitize_user_text(out.get("chunk", ""), 3000)
        out["sources"] = [
            sanitize_user_text(source, 1000, preserve_urls=True)
            for source in list(out.get("sources", []) or [])[:20]
        ]
        if not out.get("ok", True):
            out["error"] = safe_error_message(
                out.get("error", "failed"),
                "Research worker failed.",
                force_generic=True,
            )
        return out

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_idx = {pool.submit(_run, i, t): i for i, t in enumerate(jobs)}
        for fut in as_completed(future_to_idx):
            results[future_to_idx[fut]] = fut.result()
    return [r for r in results if r is not None]


def _normalize(chunk: str) -> str:
    return re.sub(r"\s+", " ", (chunk or "").strip().lower())


def fuse_results(results: list[dict]) -> dict:
    """Dedupe → fuse → cite. Only ok workers contribute chunks."""
    chunks: list[str] = []
    seen: set[str] = set()
    citations: list[str] = []
    errors: list[dict] = []
    for r in results or []:
        if not (r or {}).get("ok"):
            errors.append({
                "task_id": sanitize_user_text((r or {}).get("task_id", "?"), 120),
                "error": safe_error_message(
                    (r or {}).get("error", "failed"),
                    "Research worker failed.",
                    force_generic=True,
                ),
            })
            continue
        norm = _normalize(str((r or {}).get("chunk", "")))
        if norm and norm not in seen:
            seen.add(norm)
            chunks.append(sanitize_user_text(r.get("chunk", ""), 3000))
        for src in (r.get("sources") or []):
            s = sanitize_user_text(src, 1000, preserve_urls=True)
            if s and s not in citations:
                citations.append(s)
    return {"chunks": chunks, "citations": citations, "errors": errors}
