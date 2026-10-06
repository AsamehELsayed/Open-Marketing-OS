"""Model-call observability. Unknown provider fields stay null — never fabricated.

Every v1 model call persists: call/turn/project ids, provider, actual model,
adapter, quantization, route mode + reason, token counts when reported,
latency, estimated API cost, pricing version. Local API cost is $0.00
(UI separately notes local compute is not metered).
See docs/v1/architecture.md §7.
"""
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Provider = Literal["local", "openai", "openrouter", "fake", "deterministic"]
RouteMode = Literal["AUTO", "LOCAL", "OPENAI", "OPENROUTER", "BASE", "MARKETING_LORA"]


class ModelCall(BaseModel):
    turn_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    call_id: str = ""
    provider: Provider
    model: str = Field(min_length=1)
    adapter: str = ""
    quantization: str = ""
    route_mode: RouteMode
    route_reason: str = Field(min_length=1)
    # Token counts as reported by the provider; None = unknown (never 0-filled).
    input_tokens: int | None = None
    cached_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int = 0
    estimated_cost_usd: float | None = None
    pricing_version: str = ""
    requested_model: str = ""
    behavior_profile: str = ""
    started_at: str = ""
    ended_at: str = ""

    @field_validator("latency_ms")
    @classmethod
    def _non_negative_latency(cls, v: int) -> int:
        return max(0, v)

    @model_validator(mode="after")
    def _subset_token_invariants(self):
        if (self.cached_tokens is not None and self.input_tokens is not None
                and self.cached_tokens > self.input_tokens):
            raise ValueError("cached_tokens is a subset of input_tokens "
                             "(cached_tokens <= input_tokens)")
        if (self.reasoning_tokens is not None and self.output_tokens is not None
                and self.reasoning_tokens > self.output_tokens):
            raise ValueError("reasoning_tokens is a subset of output_tokens "
                             "(reasoning_tokens <= output_tokens)")
        return self

    def unknown_token_fields(self) -> list[str]:
        return [f for f in ("input_tokens", "cached_tokens", "output_tokens",
                            "reasoning_tokens", "total_tokens")
                if getattr(self, f) is None]

    def totals_consistent(self) -> bool:
        """Provider-semantics arithmetic check on reported counts.

        Requires input/output/total present; total must equal
        input + output. Cached input tokens are a subset of input and
        reasoning tokens are a subset of output — never added again.
        False when required parts are unknown, the sum mismatches, or a
        known subset leg exceeds its parent — unknown is never estimated.
        """
        if (self.input_tokens is None or self.output_tokens is None
                or self.total_tokens is None):
            return False
        if self.total_tokens != self.input_tokens + self.output_tokens:
            return False
        if (self.cached_tokens is not None
                and self.cached_tokens > self.input_tokens):
            return False
        if (self.reasoning_tokens is not None
                and self.reasoning_tokens > self.output_tokens):
            return False
        return True

    def local_api_cost_usd(self) -> float | None:
        """Local leg has no API cost. Non-local calls return recorded cost."""
        if self.provider == "local":
            return 0.0
        return self.estimated_cost_usd
