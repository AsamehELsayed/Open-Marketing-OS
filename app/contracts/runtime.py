"""Runtime feature flags. LangGraph is the shipped default (DEV-007 W1).

Authoritative env contract (see docs/v1/migration-plan.md):
AI_RUNTIME=legacy|langgraph (default langgraph; legacy is rollback-only via
the v1.0.0 tag — not a second shipped runtime),
MAX_AGENT_CONCURRENCY (default 4, hard cap 4 — matches turns.py _POOL,
scripts/dev_sched.py cap, AGENTS.md parallelism).
"""
import os
from typing import Literal

from pydantic import BaseModel, field_validator

AiRuntime = Literal["legacy", "langgraph"]

AI_RUNTIME_DEFAULT: AiRuntime = "langgraph"
MAX_AGENT_CONCURRENCY_DEFAULT = 4
MAX_AGENT_CONCURRENCY_HARD_CAP = 4


def get_ai_runtime() -> AiRuntime:
    """Fail closed to the langgraph default on missing/invalid values."""
    raw = (os.getenv("AI_RUNTIME", "") or "").strip().lower()
    if raw in ("legacy", "langgraph"):
        return raw  # type: ignore[return-value]
    return AI_RUNTIME_DEFAULT


def get_max_agent_concurrency() -> int:
    """Default 4; clamp fail-closed to 4 on missing/invalid/out-of-range."""
    try:
        n = int(os.getenv("MAX_AGENT_CONCURRENCY", MAX_AGENT_CONCURRENCY_DEFAULT))
    except (ValueError, TypeError):
        return MAX_AGENT_CONCURRENCY_DEFAULT
    if 1 <= n <= MAX_AGENT_CONCURRENCY_HARD_CAP:
        return n
    return MAX_AGENT_CONCURRENCY_DEFAULT


def is_legacy_serving() -> bool:
    """True only when the operator explicitly opts into legacy rollback."""
    return get_ai_runtime() == "legacy"


class RuntimeConfig(BaseModel):
    """Validated snapshot of the two W0 flags. Strict: invalid values raise."""

    ai_runtime: AiRuntime = AI_RUNTIME_DEFAULT
    max_agent_concurrency: int = MAX_AGENT_CONCURRENCY_DEFAULT

    @field_validator("max_agent_concurrency")
    @classmethod
    def _cap_concurrency(cls, v: int) -> int:
        if not 1 <= v <= MAX_AGENT_CONCURRENCY_HARD_CAP:
            raise ValueError(
                f"max_agent_concurrency must be 1..{MAX_AGENT_CONCURRENCY_HARD_CAP}"
            )
        return v

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        return cls(ai_runtime=get_ai_runtime(),
                   max_agent_concurrency=get_max_agent_concurrency())
