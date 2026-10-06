"""Parallel tool fan-out (DEV-007 W4).

Dispatches independent tool calls concurrently through
``Registry.execute`` so research-style turns can fan out (website +
social + competitor + knowledge retrieval) inside the shared runtime
concurrency contract: default from ``MAX_AGENT_CONCURRENCY`` (4),
hard cap 4 via ``app.contracts.runtime``.

Guarantees:
- fail-closed on missing/blank ``project_id`` (ValueError, no dispatch);
- per-tool timeout from ``ToolRecord.timeout_s``;
- cost controls: ``allow_metered`` / ``max_metered`` gate non-free tools
  (blocked calls are returned, never executed, never telemetered);
- results returned in call order, each carrying ``tool_id``;
- each worker thread uses its own SQLite connection cloned from the
  main connection's file path (``sqlite3`` connections are not
  cross-thread safe). File-less (in-memory) databases fall back to
  sequential execution on the calling thread — timeouts are not
  enforced in that mode.

Known limitation: a handler that exceeds its timeout is abandoned (its
result is discarded) but the worker thread keeps running until the
handler returns.
"""
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout

from app.contracts.runtime import MAX_AGENT_CONCURRENCY_HARD_CAP, get_max_agent_concurrency
from app.services.tools.registry import ToolNotFound

__all__ = ["fanout", "resolve_max_workers"]


def resolve_max_workers(max_workers=None) -> int:
    """Explicit value in 1..hard-cap wins; invalid/missing falls back to env default."""
    if max_workers is None:
        return get_max_agent_concurrency()
    try:
        n = int(max_workers)
    except (TypeError, ValueError):
        return get_max_agent_concurrency()
    if n < 1:
        return get_max_agent_concurrency()
    if n > MAX_AGENT_CONCURRENCY_HARD_CAP:
        return MAX_AGENT_CONCURRENCY_HARD_CAP
    return n


def _db_file_path(conn) -> str:
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
        if row is not None:
            path = row[2] if len(row) > 2 else ""
            return str(path or "")
    except Exception:
        return ""
    return ""


def _open_worker_conn(path: str):
    conn = sqlite3.connect(path, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _call_worker(registry, db_path, project_id, root, tool_id, args) -> dict:
    conn = None
    try:
        conn = _open_worker_conn(db_path)
        out = registry.execute(conn, project_id=project_id, root=root,
                               name=tool_id, args=args)
        if not isinstance(out, dict):
            return {"ok": False, "error": f"tool {tool_id}: handler must return dict",
                    "status": "failed", "tool_id": tool_id}
        out = dict(out)
        out.setdefault("tool_id", tool_id)
        return out
    except Exception as e:
        return {"ok": False, "error": f"tool {tool_id} fanout failed: {e}",
                "status": "failed", "tool_id": tool_id}
    finally:
        if conn is not None:
            try:
                conn.commit()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass


def _error(tool_id, message) -> dict:
    return {"ok": False, "error": message, "status": "failed", "tool_id": tool_id}


def fanout(registry, conn, *, project_id, root, calls, max_workers=None,
           allow_metered=True, max_metered=None) -> list[dict]:
    """Run independent tool calls concurrently; results in call order."""
    if not project_id or not str(project_id).strip():
        raise ValueError("project_id is required (fail closed)")
    if calls is None:
        calls = []
    if not isinstance(calls, (list, tuple)):
        raise ValueError("calls must be a list of call objects")
    items = list(calls)
    results: list[dict | None] = [None] * len(items)
    dispatch: list[tuple[int, str, dict, int]] = []
    metered_used = 0

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            results[idx] = _error("", "call must be an object with tool_id or name")
            continue
        raw = item.get("tool_id") or item.get("name")
        if not raw or not isinstance(raw, str):
            results[idx] = _error("", "call must include tool_id or name")
            continue
        args = item.get("args") if item.get("args") is not None else {}
        if not isinstance(args, dict):
            results[idx] = _error(raw, f"tool {raw}: args must be an object")
            continue
        try:
            rec = registry.get_tool(raw, project_id)
        except ToolNotFound:
            results[idx] = _error(raw, f"unknown tool: {raw}")
            continue
        if rec.cost_type != "free":
            over_budget = max_metered is not None and metered_used >= max_metered
            if not allow_metered or over_budget:
                results[idx] = _error(
                    rec.tool_id,
                    f"tool {rec.tool_id}: blocked by cost controls (cost_type={rec.cost_type})")
                continue
            metered_used += 1
        dispatch.append((idx, rec.tool_id, args, rec.timeout_s))

    for idx in range(len(results)):
        if results[idx] is None:
            results[idx] = _error("", "internal: call not dispatched")

    if not dispatch:
        return [r for r in results if r is not None]  # type: ignore[misc]

    db_path = _db_file_path(conn)
    if not db_path:
        for idx, tid, args, _timeout_s in dispatch:
            out = registry.execute(conn, project_id=project_id, root=root,
                                   name=tid, args=args)
            out = dict(out) if isinstance(out, dict) else {
                "ok": False, "error": f"tool {tid}: handler must return dict",
                "status": "failed"}
            out.setdefault("tool_id", tid)
            results[idx] = out
        return [r if r is not None else _error("", "internal: call not dispatched")
                for r in results]

    workers = resolve_max_workers(max_workers)
    workers = min(workers, len(dispatch))
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        submitted = []
        for idx, tid, args, timeout_s in dispatch:
            fut = pool.submit(_call_worker, registry, db_path, project_id,
                              root, tid, args)
            submitted.append((fut, idx, tid, timeout_s))
        t0 = time.perf_counter()
        for fut, idx, tid, timeout_s in submitted:
            remaining = max(0.05, timeout_s - (time.perf_counter() - t0))
            try:
                out = fut.result(timeout=remaining)
            except FuturesTimeout:
                fut.cancel()
                out = _error(tid, f"tool {tid} timed out after {timeout_s}s")
            except Exception as e:
                out = _error(tid, f"tool {tid} fanout failed: {e}")
            results[idx] = out
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return [r if r is not None else _error("", "internal: call not dispatched")
            for r in results]
