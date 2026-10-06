"""DEV-005 W8 — Shadow/parity/migration plumbing (NO CUTOVER).

Dual-run harness: legacy runtime + new LangGraph path on a frozen parity
corpus. Comparator records per-case verdicts with evidence. Serving always
uses the legacy reply; cutover stays OFF behind an explicit founder gate.

W8 owns ONLY this package. No graph/RAG/provider/router internals,
FastAPI, React, DB schema, requirements, or lockfile edits.
"""

from app.services.shadow.comparator import compare_case, summarize
from app.services.shadow.dual_run import dual_run_case, ensure_canary_projects, run_corpus
from app.services.shadow.parity_corpus import FROZEN, FROZEN_AT, load_corpus
from app.services.shadow.runtime_flags import (
    CUTOVER_FOUNDER_GATE_VALUE,
    get_ai_runtime_name,
    is_cutover_enabled,
    is_legacy_default,
    served_reply,
)

__all__ = [
    "FROZEN",
    "FROZEN_AT",
    "load_corpus",
    "dual_run_case",
    "ensure_canary_projects",
    "run_corpus",
    "compare_case",
    "summarize",
    "CUTOVER_FOUNDER_GATE_VALUE",
    "get_ai_runtime_name",
    "is_cutover_enabled",
    "is_legacy_default",
    "served_reply",
]
