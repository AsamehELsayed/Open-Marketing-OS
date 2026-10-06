"""TOON semantic boundary. TOON crosses the LLM boundary ONLY.

Per docs/v1/toon-contract.md: Python objects, Pydantic models, LangGraph
state, SQLite, FastAPI/HTTP, React, and provider wire protocols stay
native/JSON. This module declares WHERE TOON may appear and the normative
response fields — it does NOT implement encoding (later worker owns it).
"""
from typing import Any

TOON_SCHEMA_VERSION = "1"

# Normative model-response fields (toon-contract §Envelope). Pydantic
# validates decoded output; IDs reference artifacts, never large payloads.
TOON_RESPONSE_FIELDS = (
    "schema_version",
    "route",
    "confidence",
    "tool_calls",
    "evidence_refs",
    "approval",
    "unknowns",
    "final_answer",
    "errors",
)

# Semantic payloads that MUST be TOON at the model boundary.
_TOON_CONTEXTS = frozenset({
    "context_pack",
    "rag_evidence",
    "tool_observation",
    "routing_decision",
    "agent_handoff",
    "worker_result",
    "model_response",
})

# Transports/internal state that MUST stay native/JSON — never TOON.
_NATIVE_CONTEXTS = frozenset({
    "sqlite_row",
    "graph_state",
    "fastapi_transport",
    "react_state",
    "provider_wire",
    "pydantic_model",
})

_FORBIDDEN_SAFETY_KEYS = frozenset({
    "api_key", "secret", "token", "password",
    "chain_of_thought", "reasoning_content",
})


def is_toon_boundary(context_name: str) -> bool:
    """True only for model-facing semantic contexts. Unknown → False."""
    return context_name in _TOON_CONTEXTS


def validate_response_fields(payload: dict[str, Any]) -> list[str]:
    """Return missing normative response fields (empty = complete)."""
    if not isinstance(payload, dict):
        return list(TOON_RESPONSE_FIELDS)
    return [f for f in TOON_RESPONSE_FIELDS if f not in payload]


def safety_check(payload: dict[str, Any]) -> list[str]:
    """Fail-closed safety scan: secrets/reasoning keys must never cross."""
    problems = []
    if not isinstance(payload, dict):
        return ["payload must be a mapping"]
    for key in _FORBIDDEN_SAFETY_KEYS:
        if key in payload:
            problems.append(f"forbidden key: {key}")
    return problems
