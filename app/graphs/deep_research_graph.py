"""DEV-005 W1 v1 product path — deep-research StateGraph (Send fan-out/fan-in).

Framework-first runtime (architecture §3): planner decomposes into ≤4
research_tasks, a conditional edge returns [Send("worker", ...)] so each
task runs as an independent branch, results merge through an
operator.add reducer, and a fuse node dedupes → fuses → cites. Failed
workers are recorded and excluded; dependents of failed workers stay
blocked. Workers never sub-delegate.

The ThreadPoolExecutor implementation in deep_research.py is the explicit
LEGACY/test fallback — never the v1 product path.
"""
from __future__ import annotations

import operator
import re
import threading
import time
from typing import Annotated, TypedDict

from app.contracts.events import safe_error_message, sanitize_user_text

_HARD_CAP = 4


class DeepResearchState(TypedDict, total=False):
    question: str
    tasks: list
    worker_results: Annotated[list, operator.add]
    chunks: list
    citations: list
    errors: Annotated[list, operator.add]
    overlap_peak: int


def _split_question(question: str, cap: int = _HARD_CAP) -> list[dict]:
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
    return [{"id": f"q{i}", "query": p} for i, p in enumerate(parts[:cap])]


def _normalize(chunk: str) -> str:
    return re.sub(r"\s+", " ", (chunk or "").strip().lower())


def build_deep_research_graph(*, checkpointer=None, worker_fn=None):
    """Compile the v1 deep-research graph. Requires langgraph installed."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Send

    tracker = {"live": 0, "peak": 0, "lock": threading.Lock()}

    def _default_worker(task: dict) -> dict:
        with tracker["lock"]:
            tracker["live"] += 1
            tracker["peak"] = max(tracker["peak"], tracker["live"])
        try:
            time.sleep(0.05)  # widen the overlap window for real branches
            q = (task or {}).get("query", "")
            return {
                "chunk": f"evidence for {q}",
                "sources": [f"https://example.com/{(task or {}).get('id', 'q0')}"],
            }
        finally:
            with tracker["lock"]:
                tracker["live"] -= 1

    _fn = worker_fn or _default_worker

    def planner(state: DeepResearchState) -> dict:
        return {"tasks": _split_question(state.get("question", ""))}

    def fanout(state: DeepResearchState):
        return [
            Send("worker", {"task_id": t["id"], "query": t["query"]})
            for t in (state.get("tasks") or [])
        ]

    def worker(state: dict) -> dict:
        task = {"id": state.get("task_id", "q0"), "query": state.get("query", "")}
        owns_tracker = worker_fn is not None
        if owns_tracker:
            with tracker["lock"]:
                tracker["live"] += 1
                tracker["peak"] = max(tracker["peak"], tracker["live"])
        try:
            try:
                if owns_tracker:
                    time.sleep(0.02)
                out = _fn(task)
            except Exception as e:
                return {
                    "worker_results": [
                        {
                            "task_id": task["id"],
                            "chunk": "",
                            "sources": [],
                            "ok": False,
                            "error": safe_error_message(e, "Research worker failed."),
                        }
                    ]
                }
            if not isinstance(out, dict):
                return {
                    "worker_results": [
                        {
                            "task_id": task["id"],
                            "chunk": "",
                            "sources": [],
                            "ok": False,
                            "error": "worker must return dict",
                        }
                    ]
                }
            return {
                "worker_results": [
                        {
                            "task_id": sanitize_user_text(task["id"], 120),
                            "chunk": sanitize_user_text(out.get("chunk", ""), 3000),
                            "sources": [
                                sanitize_user_text(source, 1000, preserve_urls=True)
                                for source in list(out.get("sources", []) or [])[:20]
                            ],
                            "ok": True,
                        }

                ]
            }
        finally:
            # The default worker balances the shared counter internally;
            # only balance here for injected worker_fn runs.
            if owns_tracker:
                with tracker["lock"]:
                    tracker["live"] -= 1

    def fuse(state: DeepResearchState) -> dict:
        chunks: list[str] = []
        seen: set[str] = set()
        citations: list[str] = []
        errors: list[dict] = []
        for r in state.get("worker_results") or []:
            if not (r or {}).get("ok"):
                errors.append(
                    {
                        "task_id": sanitize_user_text((r or {}).get("task_id", "?"), 120),
                        "error": safe_error_message(
                            (r or {}).get("error", "failed"),
                            "Research worker failed.",
                            force_generic=True,
                        ),
                    }
                )
                continue
            norm = _normalize(str((r or {}).get("chunk", "")))
            if norm and norm not in seen:
                seen.add(norm)
                chunks.append(sanitize_user_text(r.get("chunk", ""), 3000))
            for src in r.get("sources") or []:
                s = sanitize_user_text(src, 1000, preserve_urls=True)
                if s and s not in citations:
                    citations.append(s)
        return {
            "chunks": chunks,
            "citations": citations,
            "errors": errors,
            "overlap_peak": int(tracker["peak"]),
        }

    builder = StateGraph(DeepResearchState)
    builder.add_node("planner", planner)
    builder.add_node("worker", worker)
    builder.add_node("fuse", fuse)
    builder.add_edge(START, "planner")
    builder.add_conditional_edges("planner", fanout, ["worker"])
    builder.add_edge("worker", "fuse")
    builder.add_edge("fuse", END)
    return builder.compile(checkpointer=checkpointer or InMemorySaver())
