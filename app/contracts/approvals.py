"""Approvals + interrupt/resume. Yellow/red never execute without approval.

Preserves the AGENTS.md green/yellow/red policy and the Approvals UI flow.
v1 maps request_approval onto a LangGraph interrupt() node; pending approvals
stay pending across rollback (docs/v1/rollback-plan.md). Side-effect nodes
require idempotency keys so resume/replay cannot duplicate external actions.
"""
from typing import Literal

from pydantic import BaseModel, Field, model_validator

ActionClass = Literal["green", "yellow", "red"]
ApprovalStatus = Literal["pending", "approved", "rejected"]
ApprovalDecision = Literal["approved", "rejected"]


class ApprovalRequest(BaseModel):
    approval_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    conversation_id: str = ""
    turn_id: str = ""
    kind: str = ""
    title: str = Field(min_length=1, max_length=300)
    body_md: str = ""
    status: ApprovalStatus = "pending"
    action_class: ActionClass = "yellow"
    # Stable idempotency key per intent (derived from approval id, one key per
    # approval). Required for yellow/red; green pass-through needs none.
    idempotency_key: str = ""
    decided_by: str = ""
    decided_at: str = ""

    @model_validator(mode="after")
    def _key_required_for_side_effects(self) -> "ApprovalRequest":
        if self.action_class in ("yellow", "red") and not self.idempotency_key.strip():
            raise ValueError(
                f"{self.action_class} approvals require an idempotency_key")
        return self

    def executable(self) -> bool:
        """Green executes immediately; yellow/red only when approved."""
        if self.action_class == "green":
            return True
        return self.status == "approved"


class ApprovalResume(BaseModel):
    """Human decision resuming an interrupted graph run."""

    approval_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    decision: ApprovalDecision
    decided_by: str = Field(min_length=1)
    decided_at: str = ""
    idempotency_key: str = ""

    def should_resume_graph(self) -> bool:
        return self.decision == "approved"
