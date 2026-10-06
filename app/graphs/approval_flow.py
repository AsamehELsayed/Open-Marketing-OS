"""DEV-005 W1 v1 product path — approval interrupt/resume StateGraph.

Framework-first approval gate (architecture §2): yellow/red actions pause
via langgraph interrupt() with a JSON-serializable payload and resume only
through Command(resume=...). Green actions pass through with no interrupt.
Side effects run AFTER the interrupt and are keyed by idempotency key, so
a replayed resume is a no-op.

The pure helpers in approvals.py (approval_interrupt/resume_approval) are
the explicit LEGACY/test fallback — never the v1 product path.
"""
from __future__ import annotations

from typing import TypedDict


class ApprovalState(TypedDict, total=False):
    approval_id: str
    project_id: str
    title: str
    action_class: str
    idempotency_key: str
    approved: bool
    executed: bool
    skipped_interrupt: bool


def build_approval_graph(*, checkpointer=None):
    """Compile the v1 approval graph. Requires langgraph installed."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    from app.graphs.approvals import stable_key

    def request(state: ApprovalState) -> dict:
        approval_id = (state.get("approval_id") or "").strip() or "approval-1"
        key = (state.get("idempotency_key") or "").strip() or stable_key(approval_id)
        return {"approval_id": approval_id, "idempotency_key": key}

    def gate(state: ApprovalState) -> dict:
        action = (state.get("action_class") or "green").strip().lower()
        if action not in ("yellow", "red"):
            return {"approved": True, "skipped_interrupt": True}
        # interrupt() must come first: everything before it re-runs on resume,
        # so no side effect may precede this call (idempotency rule).
        decision = interrupt(
            {
                "approval_id": state.get("approval_id", ""),
                "project_id": state.get("project_id", ""),
                "title": (state.get("title", "") or "")[:300],
                "action_class": action,
                "idempotency_key": state.get("idempotency_key", ""),
            }
        )
        if isinstance(decision, dict):
            approved = bool(decision.get("approved"))
        else:
            approved = bool(decision)
        return {"approved": approved, "skipped_interrupt": False}

    def execute(state: ApprovalState) -> dict:
        # Side effect AFTER the interrupt: runs exactly once per resume.
        if state.get("approved"):
            return {"executed": True}
        return {"executed": False}

    builder = StateGraph(ApprovalState)
    builder.add_node("request", request)
    builder.add_node("gate", gate)
    builder.add_node("execute", execute)
    builder.add_edge(START, "request")
    builder.add_edge("request", "gate")
    builder.add_edge("gate", "execute")
    builder.add_edge("execute", END)
    return builder.compile(checkpointer=checkpointer or InMemorySaver())
