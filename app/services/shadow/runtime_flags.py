"""DEV-005/007 — runtime/cutover flags. Read-only view over env.

Contract:
- AI_RUNTIME default is ``langgraph`` (DEV-007 W1; owns app/contracts/runtime.py).
- ``AI_RUNTIME=legacy`` is a rollback-only opt-out (v1.0.0 tag is the
  rollback point — not a second shipped runtime).
- Shadow mode records the comparison path; serving follows the runtime flag.
- Cutover flag exists but defaults OFF and requires an explicit founder
  gate: BOTH ``W8_CUTOVER_REQUESTED=1`` AND
  ``W8_FOUNDER_GATE=approve-cutover`` must be set. Anything else → OFF.
"""
from __future__ import annotations

import os

CUTOVER_REQUEST_ENV = "W8_CUTOVER_REQUESTED"
CUTOVER_FOUNDER_GATE_ENV = "W8_FOUNDER_GATE"
CUTOVER_FOUNDER_GATE_VALUE = "approve-cutover"

SHADOW_ENV = "W8_SHADOW"


def get_ai_runtime_name() -> str:
    """Authoritative AI_RUNTIME value; fail closed to langgraph (W0 contract)."""
    from app.contracts.runtime import get_ai_runtime

    try:
        return get_ai_runtime()
    except Exception:
        return "langgraph"


def is_legacy_default() -> bool:
    """True only when the operator explicitly selected legacy rollback."""
    return get_ai_runtime_name() == "legacy"


def is_shadow_enabled() -> bool:
    """Shadow recording toggle. Default ON (observe-only); serving unaffected."""
    raw = (os.getenv("W8_SHADOW", "1") or "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def is_cutover_enabled() -> bool:
    """Cutover gate. Default OFF. Requires explicit founder approval value.

    Both conditions required; secrets are never read here (plain gate
    string only, no credentials).
    """
    requested = (os.getenv("W8_CUTOVER_REQUESTED", "") or "").strip() == "1"
    gate = (os.getenv("W8_FOUNDER_GATE", "") or "").strip()
    return bool(requested and gate == CUTOVER_FOUNDER_GATE_VALUE)


def served_reply(legacy_reply: str, new_reply: str) -> str:
    """Shadow serving rule: legacy output is served; new output only recorded.

    Even if cutover were requested, this helper never serves the new reply —
    cutover execution belongs to a founder-gated S9 step, out of W8 scope.
    """
    _ = new_reply
    return legacy_reply
