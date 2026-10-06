"""LEGACY/test fallback: pure approval interrupt / resume helpers.

Kept for unit tests and offline use — never the v1 product path.
v1 traffic uses app.graphs.approval_flow.build_approval_graph
(real StateGraph node with interrupt() + Command(resume=...)).

Green executes immediately (pass-through, no interrupt).
Yellow/red pause with a JSON-serializable interrupt payload and resume
only on ApprovalResume(decision=approved). Side effects run AFTER the
interrupt and are guarded by idempotency keys: a replayed resume with
an already-executed key is a no-op (no duplicate external actions).
"""
from __future__ import annotations

import hashlib

from app.contracts.approvals import ApprovalRequest, ApprovalResume


def stable_key(approval_id: str, purpose: str = "approval") -> str:
    digest = hashlib.sha256((approval_id or "").encode("utf-8")).hexdigest()[:16]
    return f"{purpose}:v1:{(approval_id or '').strip()}:{digest}"


def build_approval_request(*, approval_id: str, project_id: str,
                           title: str, action_class: str = "yellow",
                           idempotency_key: str = "",
                           conversation_id: str = "",
                           turn_id: str = "") -> ApprovalRequest:
    key = (idempotency_key or "").strip()
    if action_class in ("yellow", "red") and not key:
        key = stable_key(approval_id)
    return ApprovalRequest(
        approval_id=approval_id, project_id=project_id,
        conversation_id=conversation_id or "", turn_id=turn_id or "",
        kind="graph_operation", title=title,
        action_class=action_class, idempotency_key=key,
    )


def approval_interrupt(state: dict) -> dict:
    """Node: pass-through for green, pending payload for yellow/red."""
    approval = dict((state or {}).get("approval_state") or {})
    action = (approval.get("action_class") or "green").strip().lower()
    if action not in ("yellow", "red"):
        return {"status": "pass_through"}
    approval_id = (approval.get("approval_id") or "").strip() or "approval-1"
    key = (approval.get("idempotency_key") or "").strip() or stable_key(approval_id)
    payload = {
        "approval_id": approval_id,
        "project_id": (approval.get("project_id") or "").strip(),
        "title": (approval.get("title") or "")[:300],
        "action_class": action,
        "idempotency_key": key,
    }
    return {"status": "pending", "interrupt": payload,
            "idempotency_key": key}


def resume_approval(resume: ApprovalResume, side_effect,
                    executed_keys: set | None = None) -> dict:
    """Resume helper. Executes side_effect(key) at most once per key."""
    done = set(executed_keys or set())
    if resume.decision != "approved":
        return {"executed": False, "reason": "rejected",
                "executed_keys": done}
    key = (resume.idempotency_key or "").strip() or stable_key(resume.approval_id)
    if key in done:
        return {"executed": False, "reason": "idempotent_replay",
                "executed_keys": done, "idempotency_key": key}
    try:
        result = side_effect(key)
    except Exception as e:
        return {"executed": False, "reason": f"side_effect_failed: {e}",
                "executed_keys": done, "idempotency_key": key}
    done.add(key)
    return {"executed": True, "result": result,
            "executed_keys": done, "idempotency_key": key}
