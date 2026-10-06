"""Execution-tree emitter (DEV-008-SKILLS-OPS W4).

Frozen contract: plan.md section 1.3 (event contract, parent_id rule, 7
TREE_STATUSES, no-chain-of-thought rule) and section 3.5 (this interface).

What this is
------------
``TreeEmitter`` is the per-turn bookkeeping object that makes ``parent_id``
links resolvable **at emit time with no backfill pass**. It holds
``{turn_id, task_planned_id, employee_started_id_by_employee_id}`` for the
lifetime of one turn and is passed into the graph via
``configurable["tree"]`` (alongside the existing
``configurable["event_callback"]``).

Every method writes through ``app.services.turns.emit_event``
(function-local import, matching ``turns.py:129``'s existing style) so the
write path is the single sanitised one, and returns the
``execution_events.id`` (or ``None`` on a swallowed exception — ``emit_event``
already never raises).

Tree metadata discipline (§1.3.6 — the 4000-char hazard)
--------------------------------------------------------
``GraphExecutionEvent.to_row`` truncates ``metadata_json`` to ``[:4000]`` and
a truncated JSON string degrades ``meta`` to ``{}`` on read — the node would
render as a parentless orphan. So tree metadata is **flat** (no nested
dicts/lists), **at most 12 keys** per event, and **detail ≤ 160 chars**.
The round-trip test in ``test_dev008so_execution_tree.py`` persists one
maximum-shape event of every one of the 15 types and asserts exact equality.

Concurrency model
-----------------
``TreeEmitter`` holds no connection and no thread state beyond a small lock
guarding the ``employee_started`` id map. Employee bodies run **inside the
single turn thread** via LangGraph's own ``Send`` scheduler — not as new
``_POOL`` submissions — so ``turns._POOL max_workers=4`` is never the binding
constraint (see the W4 handoff for the measured statement).

No chain-of-thought
-------------------
No method accepts free-form reasoning. Details are short human-language
labels; ``employee_failed`` uses ``safe_error_message`` (generic fallback, no
traceback, no path, no exception type). The enforcing sanitizer remains
``app.contracts.events.sanitize_metadata`` on all three paths.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any


def _utc_ms() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _role_label(role: str) -> str:
    return str(role or "").replace("_", " ").title() or "Team member"


def _parent_str(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(int(value))
    except (TypeError, ValueError):
        text = str(value or "").strip()
        return text


class TreeEmitter:
    """Per-turn tree event writer. See the module docstring."""

    def __init__(self, *, db_path, project_id: str, conversation_id: str,
                 turn_id: str, thread_id: str) -> None:
        self._db_path = str(db_path or "")
        self._project_id = str(project_id or "")
        self._conversation_id = str(conversation_id or "")
        self._turn_id = str(turn_id or "")
        self._thread_id = str(thread_id or "")
        self._lock = threading.Lock()
        self._task_planned_id: int | None = None
        self._employee_started_ids: dict[str, int] = {}

    # -- introspection (tests / graph nodes, no I/O) --------------------

    @property
    def turn_id(self) -> str:
        return self._turn_id

    @property
    def task_planned_id(self) -> int | None:
        with self._lock:
            return self._task_planned_id

    def employee_started_id(self, employee_id: str) -> int | None:
        with self._lock:
            return self._employee_started_ids.get(str(employee_id or ""))

    # -- internals -------------------------------------------------------

    def _emit(self, *, event_type: str, label: str, detail: str = "",
              metadata: dict[str, Any] | None = None) -> int | None:
        from app.services.turns import emit_event

        try:
            return emit_event(
                self._db_path,
                project_id=self._project_id,
                conversation_id=self._conversation_id,
                turn_id=self._turn_id,
                event_type=event_type,
                label=label,
                detail=(detail or "")[:160],
                metadata=dict(metadata or {}),
            )
        except Exception:
            return None

    def _task_parent(self) -> str:
        with self._lock:
            planned = self._task_planned_id
        return _parent_str(planned) if planned is not None else ""

    def _employee_parent(self, employee_id: str) -> str:
        started = self.employee_started_id(employee_id)
        if started is not None:
            return _parent_str(started)
        return self._task_parent()

    # -- frozen interface (plan §3.5) ------------------------------------

    def account_manager_started(self, *, intent: str, route: str) -> int | None:
        return self._emit(
            event_type="account_manager_started",
            label="Account Manager started",
            detail=str(intent or "")[:160],
            metadata={
                "status": "RUNNING",
                "project_id": self._project_id,
                "turn_id": self._turn_id,
                "thread_id": self._thread_id,
                "route": str(route or "")[:160],
                "parent_id": "",
            },
        )

    def task_planned(self, *, tasks: list[dict], employee_count: int) -> int | None:
        try:
            count = len(list(tasks or []))
        except TypeError:
            count = 0
        try:
            employees = max(0, int(employee_count))
        except (TypeError, ValueError):
            employees = 0
        row_id = self._emit(
            event_type="task_planned",
            label="Plan ready",
            detail=f"{count} task(s) planned",
            metadata={
                "status": "COMPLETE",
                "task_count": count,
                "employee_count": employees,
                "turn_id": self._turn_id,
                "thread_id": self._thread_id,
                "parent_id": "",
            },
        )
        if row_id is not None:
            with self._lock:
                self._task_planned_id = int(row_id)
        return row_id

    def employee_queued(self, *, employee_id: str, role: str, task: dict) -> int | None:
        query = ""
        try:
            query = str((task or {}).get("query", "") or "")[:160]
        except Exception:
            query = ""
        return self._emit(
            event_type="employee_queued",
            label=f"Assigned to {_role_label(role)}",
            detail=query,
            metadata={
                "status": "QUEUED",
                "employee_id": str(employee_id or ""),
                "employee_role": str(role or ""),
                "turn_id": self._turn_id,
                "thread_id": self._thread_id,
                "parent_id": self._task_parent(),
            },
        )

    def employee_started(self, *, employee_id: str, role: str, task: dict,
                         started_at: str | None = None) -> int | None:
        query = ""
        try:
            query = str((task or {}).get("query", "") or "")[:160]
        except Exception:
            query = ""
        row_id = self._emit(
            event_type="employee_started",
            label=f"{_role_label(role)} working",
            detail=query,
            metadata={
                "status": "RUNNING",
                "employee_id": str(employee_id or ""),
                "employee_role": str(role or ""),
                "started_at": started_at or _utc_ms(),
                "turn_id": self._turn_id,
                "thread_id": self._thread_id,
                "parent_id": self._task_parent(),
            },
        )
        if row_id is not None and str(employee_id or ""):
            with self._lock:
                self._employee_started_ids[str(employee_id)] = int(row_id)
        return row_id

    def employee_completed(self, *, employee_id: str, role: str,
                           duration_ms: int) -> int | None:
        try:
            duration = max(0, int(duration_ms))
        except (TypeError, ValueError):
            duration = 0
        except Exception:
            duration = 0
        return self._emit(
            event_type="employee_completed",
            label=f"{_role_label(role)} finished",
            detail="",
            metadata={
                "status": "COMPLETE",
                "employee_id": str(employee_id or ""),
                "employee_role": str(role or ""),
                "duration_ms": duration,
                "completed_at": _utc_ms(),
                "parent_id": self._task_parent(),
            },
        )

    def employee_failed(self, *, employee_id: str, role: str, exc: BaseException,
                        duration_ms: int) -> int | None:
        from app.contracts.events import safe_error_message

        try:
            duration = max(0, int(duration_ms))
        except (TypeError, ValueError):
            duration = 0
        except Exception:
            duration = 0
        try:
            detail = safe_error_message(exc)
        except Exception:
            detail = "The request could not be completed."
        return self._emit(
            event_type="employee_failed",
            label=f"{_role_label(role)} could not finish",
            detail=str(detail or "")[:160],
            metadata={
                "status": "FAILED",
                "employee_id": str(employee_id or ""),
                "employee_role": str(role or ""),
                "duration_ms": duration,
                "completed_at": _utc_ms(),
                "parent_id": self._task_parent(),
            },
        )

    def skill_selected(self, *, skill_id: str, reason_code: str, score: float,
                       employee_id: str = "") -> int | None:
        try:
            score_value = float(score)
        except (TypeError, ValueError):
            score_value = 0.0
        except Exception:
            score_value = 0.0
        name = str(skill_id or "")
        parent = (self._employee_parent(employee_id)
                  if str(employee_id or "") else self._task_parent())
        return self._emit(
            event_type="skill_selected",
            label=f"Using {name}",
            detail=str(reason_code or "")[:160],
            metadata={
                "status": "COMPLETE",
                "skill_id": name,
                "skill_name": name,
                "reason_code": str(reason_code or "")[:160],
                "score": score_value,
                "employee_id": str(employee_id or ""),
                "parent_id": parent,
            },
        )

    def skill_loaded(self, *, record: Any, employee_id: str = "") -> int | None:
        if isinstance(record, dict):
            skill_id = str(record.get("skill_id", "") or "")
            version = str(record.get("version", "") or "")
            category = str(record.get("category", "") or "")
            name = str(record.get("name", "") or skill_id)
        else:
            skill_id = str(getattr(record, "skill_id", "") or "")
            version = str(getattr(record, "version", "") or "")
            category = str(getattr(record, "category", "") or "")
            name = str(getattr(record, "name", "") or skill_id)
        parent = (self._employee_parent(employee_id)
                  if str(employee_id or "") else self._task_parent())
        return self._emit(
            event_type="skill_loaded",
            label=f"Loaded playbook: {name}",
            detail=f"v{version} · {category}"[:160],
            metadata={
                "status": "COMPLETE",
                "skill_id": skill_id,
                "skill_name": name,
                "skill_version": str(version)[:64],
                "category": str(category)[:64],
                "employee_id": str(employee_id or ""),
                "parent_id": parent,
            },
        )

    def tool_event(self, *, event_type: str, tool_id: str, tool_run_id: str,
                   employee_id: str = "", status: str = "COMPLETE",
                   duration_ms: int = 0) -> int | None:
        kind = str(event_type or "").strip() or "tool_completed"
        if kind not in ("tool_started", "tool_completed", "tool_failed"):
            kind = "tool_completed"
        tid = str(tool_id or "")
        if kind == "tool_started":
            label = f"Running {tid}"
        elif kind == "tool_completed":
            label = f"Tool {tid} finished"
        else:
            label = f"Tool {tid} could not finish"
        label = label[:300]
        try:
            duration = max(0, int(duration_ms))
        except (TypeError, ValueError):
            duration = 0
        except Exception:
            duration = 0
        meta: dict[str, Any] = {
            "status": str(status or "COMPLETE")[:32],
            "tool_id": tid[:128],
            "tool_run_id": str(tool_run_id or "")[:128],
            "employee_id": str(employee_id or ""),
            "parent_id": (self._employee_parent(employee_id)
                          if str(employee_id or "") else self._task_parent()),
        }
        if kind == "tool_completed":
            meta["duration_ms"] = duration
        return self._emit(
            event_type=kind,
            label=label,
            detail="",
            metadata=meta,
        )

    def evidence_added(self, *, count: int, kind: str,
                       employee_id: str = "") -> int | None:
        try:
            total = max(0, int(count))
        except (TypeError, ValueError):
            total = 0
        except Exception:
            total = 0
        parent = (self._employee_parent(employee_id)
                  if str(employee_id or "") else self._task_parent())
        return self._emit(
            event_type="evidence_added",
            label="Evidence added",
            detail=f"{total} source(s) · {str(kind or '')}"[:160],
            metadata={
                "status": "COMPLETE",
                "evidence_count": total,
                "evidence_kind": str(kind or "")[:64],
                "employee_id": str(employee_id or ""),
                "parent_id": parent,
            },
        )

    def synthesis(self, *, phase: str, duration_ms: int = 0) -> int | None:
        stage = str(phase or "").strip().lower()
        started = stage in ("started", "start", "begin", "synthesis_started")
        try:
            duration = max(0, int(duration_ms))
        except (TypeError, ValueError):
            duration = 0
        except Exception:
            duration = 0
        if started:
            return self._emit(
                event_type="synthesis_started",
                label="Writing response",
                detail="",
                metadata={
                    "status": "RUNNING",
                    "parent_id": self._task_parent(),
                },
            )
        return self._emit(
            event_type="synthesis_completed",
            label="Response written",
            detail="",
            metadata={
                "status": "COMPLETE",
                "duration_ms": duration,
                "parent_id": self._task_parent(),
            },
        )

    def approval_required(self, *, approval_id: str, action_class: str,
                          title: str) -> int | None:
        return self._emit(
            event_type="approval_required",
            label="Waiting for your approval",
            detail=str(title or "")[:160],
            metadata={
                "status": "WAITING_FOR_APPROVAL",
                "approval_id": str(approval_id or "")[:128],
                "action_class": str(action_class or "")[:32],
                "parent_id": self._task_parent(),
            },
        )


__all__ = ["TreeEmitter"]
