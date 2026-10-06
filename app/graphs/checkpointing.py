"""Checkpointer / Store boundary (docs/v1/architecture.md §4).

Authority split (non-negotiable):
- Business state stays in data/marketing.db via app/database/repos.py.
- Graph workflow state lives in a DEDICATED checkpoint DB
  (data/graph_checkpoints.db) or in memory for tests.
- Durable memory reads/writes go through the memories table behind
  a Store interface, always keyed by project_id.

LangGraph checkpointers are an optional import: create_checkpointer()
never requires langgraph installed. langgraph_checkpointer() returns
a real saver only when the package is present (W6/S2 cutover path).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CHECKPOINT_DB_NAME = "graph_checkpoints.db"


def default_checkpoint_path(data_dir: str | Path = "data") -> Path:
    return Path(data_dir) / DEFAULT_CHECKPOINT_DB_NAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class InMemoryCheckpointer:
    """Dev/test checkpointer. Thread-isolated; no persistence."""

    def __init__(self):
        self._states: dict[str, dict] = {}
        self._history: dict[str, list[dict]] = {}

    def save(self, thread_id: str, state: dict) -> None:
        tid = (thread_id or "").strip()
        if not tid:
            raise ValueError("thread_id is required")
        snapshot = json.loads(json.dumps(state or {}, ensure_ascii=False, default=str))
        self._states[tid] = snapshot
        self._history.setdefault(tid, []).append(snapshot)

    def load(self, thread_id: str) -> dict | None:
        tid = (thread_id or "").strip()
        if not tid:
            return None
        snap = self._states.get(tid)
        return json.loads(json.dumps(snap, ensure_ascii=False)) if snap is not None else None

    def history(self, thread_id: str) -> list[dict]:
        return list(self._history.get((thread_id or "").strip(), []))


class SqliteCheckpointer:
    """Local checkpointer on a DEDICATED sqlite file (never the business DB)."""

    def __init__(self, db_path: str | Path):
        self._path = Path(db_path)
        if self._path.name == "marketing.db":
            raise ValueError("checkpointer must not use the business DB file")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._path))
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS graph_checkpoints(
                thread_id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL DEFAULT '')"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS graph_checkpoint_history(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                state_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '')"""
            )
            conn.commit()
        finally:
            conn.close()

    @property
    def path(self) -> Path:
        return self._path

    def save(self, thread_id: str, state: dict) -> None:
        tid = (thread_id or "").strip()
        if not tid:
            raise ValueError("thread_id is required")
        payload = json.dumps(state or {}, ensure_ascii=False, default=str)[:200000]
        conn = sqlite3.connect(str(self._path))
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                "INSERT OR REPLACE INTO graph_checkpoints(thread_id, state_json, updated_at)"
                " VALUES (?, ?, ?)", (tid, payload, _now()))
            conn.execute(
                "INSERT INTO graph_checkpoint_history(thread_id, state_json, created_at)"
                " VALUES (?, ?, ?)", (tid, payload, _now()))
            conn.commit()
        finally:
            conn.close()

    def load(self, thread_id: str) -> dict | None:
        tid = (thread_id or "").strip()
        if not tid:
            return None
        conn = sqlite3.connect(str(self._path))
        try:
            row = conn.execute(
                "SELECT state_json FROM graph_checkpoints WHERE thread_id = ?",
                (tid,)).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        try:
            data = json.loads(row[0] or "{}")
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def history(self, thread_id: str) -> list[dict]:
        tid = (thread_id or "").strip()
        conn = sqlite3.connect(str(self._path))
        try:
            rows = conn.execute(
                "SELECT state_json FROM graph_checkpoint_history"
                " WHERE thread_id = ? ORDER BY id",
                (tid,)).fetchall()
        finally:
            conn.close()
        out = []
        for (raw,) in rows:
            try:
                data = json.loads(raw or "{}")
            except ValueError:
                continue
            if isinstance(data, dict):
                out.append(data)
        return out


def create_checkpointer(db_path: str | Path | None = None,
                        backend: str = "sqlite"):
    """Factory. backend=memory for tests; sqlite (default) needs a path."""
    if backend == "memory" or db_path is None:
        if db_path is None and backend == "memory":
            return InMemoryCheckpointer()
        if db_path is None:
            return InMemoryCheckpointer()
        return SqliteCheckpointer(db_path)
    return SqliteCheckpointer(db_path)


def langgraph_checkpointer(db_path: str | Path | None = None):
    """Return a real LangGraph saver when installed, else None.

    v1 product path compiles graphs with one of these savers (memory for
    tests, SQLite file for restart persistence) and ALWAYS passes
    config={"configurable": {"thread_id": ...}} per invoke. W1 never
    hard-depends on langgraph (no requirement/lockfile change — I1 adds
    the packages listed in workers/w1.md). W6/S2 cutover reuses this.
    """
    try:
        import importlib.util as _ilu

        if _ilu.find_spec("langgraph") is None:
            return None
        from langgraph.checkpoint.memory import InMemorySaver

        if db_path is None:
            return InMemorySaver()
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver

            return SqliteSaver.from_conn_string(str(db_path))
        except Exception:
            return InMemorySaver()
    except Exception:
        return None


class ProjectMemoryStore:
    """Durable memory behind a Store interface, keyed by project_id.

    Backed by the memories table when available; falls back to an
    in-memory map (tests, tables missing). Values must be JSON-safe
    and human-language — never prompts, secrets, or reasoning traces.
    """

    _FORBIDDEN = frozenset({"prompt", "system", "secret", "api_key",
                            "chain_of_thought", "reasoning_content"})

    def __init__(self, conn=None):
        self._conn = conn
        self._mem: dict[tuple[str, str], dict] = {}

    @staticmethod
    def _row_id(project_id: str, key: str) -> str:
        return f"{project_id}::{(key or '').strip()}"

    def _sanitize(self, value: dict) -> dict:
        if not isinstance(value, dict):
            return {}
        return {k: v for k, v in value.items() if k not in self._FORBIDDEN}

    def put(self, project_id: str, key: str, value: dict) -> None:
        pid = (project_id or "").strip()
        k = (key or "").strip()
        if not pid or not k:
            raise ValueError("project_id and key are required")
        clean = self._sanitize(dict(value or {}))
        self._mem[(pid, k)] = clean
        conn = self._conn
        if conn is None:
            return
        try:
            from app.database import repos

            row_id = self._row_id(pid, k)
            repos.Memories.insert(conn, {
                "id": row_id, "project_id": pid, "kind": "preference",
                "body_md": json.dumps(clean, ensure_ascii=False)[:4000],
                "confidence": "MEDIUM", "source_ref": k,
                "created_at": _now(), "updated_at": _now(),
            })
        except Exception:
            pass  # memory table missing/kind gate: in-memory copy still serves

    def get(self, project_id: str, key: str) -> dict | None:
        pid = (project_id or "").strip()
        k = (key or "").strip()
        if not pid or not k:
            return None
        if (pid, k) in self._mem:
            return dict(self._mem[(pid, k)])
        conn = self._conn
        if conn is None:
            return None
        try:
            from app.database import repos

            for row in repos.Memories.list(conn, pid):
                if (row.get("source_ref") or "") == k or (row.get("id") or "") == self._row_id(pid, k):
                    try:
                        data = json.loads(row.get("body_md") or "{}")
                    except ValueError:
                        return None
                    clean = self._sanitize(data if isinstance(data, dict) else {})
                    self._mem[(pid, k)] = clean
                    return dict(clean)
        except Exception:
            return None
        return None


def create_store(conn=None) -> ProjectMemoryStore:
    return ProjectMemoryStore(conn)
