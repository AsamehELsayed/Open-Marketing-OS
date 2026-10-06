"""Route decisions. Branch names are normative (docs/v1/architecture.md §2).

understand-classifier outputs exactly one branch (multi-branch only for
deep_research fan-out). Deterministic account_manager.classify() flows run
first; LLM/tool routing only for ambiguous turns.
"""
from typing import Literal

from pydantic import BaseModel, Field

GraphRoute = Literal[
    "state_only",
    "conversation_meta",
    "compound_marketing_task",
    "tool_capability",
    "knowledge",
    "external_research",
    "social_research",
    "deep_research",
    "campaign_operation",
    "approval_operation",
    "job_followup",
]

# Escalation reasons recorded by the v1 model router (architecture.md §7).
ESCALATION_REASONS = (
    "benchmark winner default",
    "explicit user selection",
    "local unavailable",
    "context overflow",
    "repeated format/tool-route failure",
    "low confidence",
    "strategic-complexity policy",
)


class RouteDecision(BaseModel):
    route: GraphRoute
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    project_id: str = Field(min_length=1)
    fallback_route: GraphRoute | None = None

    def requires_approval(self) -> bool:
        return self.route == "approval_operation"


class ModelRoute(BaseModel):
    mode: Literal["AUTO", "LOCAL", "OPENAI", "OPENROUTER", "BASE", "MARKETING_LORA"]
    provider: Literal["local", "openai", "openrouter", "fake", "deterministic"]
    model: str = Field(min_length=1)
    reason: str = Field(min_length=1)
