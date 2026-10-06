"""DEV-005 W8 — frozen parity corpus (immutable inputs, S0 oracle shape).

Slices (evaluation-plan: never average away language failures):
arabic_msa / egyptian / english / mixed + isolation canaries
(missing_project fail-closed, cross_project leakage probe).

Frozen: callers must use load_corpus() (deep copy); never mutate FROZEN_CASES.
"""
from __future__ import annotations

import copy
import hashlib
import json

from app.database.identity import DEFAULT_PROJECT_ID

FROZEN = True
FROZEN_AT = "2026-09-22"

# DEV-008-PUBLISH-GATE: `project_id` was the literal "njm" in every case, and the
# cross-project canary's expectation was keyed `no_njm_provenance`. Both named a
# real business inside an Apache-2.0 repository for no evidential gain — the
# canary only needs *a* project id that is not the one under test. The case ids,
# the prompt text and the frozen hash inputs are otherwise unchanged; only the
# project-id *value* and the expectation key are generic now, so the corpus still
# detects leakage without naming anyone.
FROZEN_CASES: tuple[dict, ...] = (
    {
        "case_id": "w8-ar-msa-01",
        "slice": "arabic_msa",
        "project_id": DEFAULT_PROJECT_ID,
        "text": "ما هي حالة مشروعي الحالية؟",
    },
    {
        "case_id": "w8-eg-01",
        "slice": "egyptian",
        "project_id": DEFAULT_PROJECT_ID,
        "text": "إيه أخبار المشروع؟ الدنيا ماشية إزاي؟",
    },
    {
        "case_id": "w8-en-01",
        "slice": "english",
        "project_id": DEFAULT_PROJECT_ID,
        "text": "What is the current status of my project?",
    },
    {
        "case_id": "w8-mixed-01",
        "slice": "mixed",
        "project_id": DEFAULT_PROJECT_ID,
        "text": "ممكن summary سريع للـ campaigns المفتوحة؟",
    },
    {
        "case_id": "w8-en-approvals-01",
        "slice": "english",
        "project_id": DEFAULT_PROJECT_ID,
        "text": "What approvals are waiting?",
    },
    {
        "case_id": "w8-missing-project-01",
        "slice": "missing_project",
        "project_id": "",
        "text": "Show me project status",
        "expect": {"new_fail_closed": "NO_PROJECT_SCOPE"},
    },
    {
        "case_id": "w8-cross-project-01",
        "slice": "cross_project",
        "project_id": "other-proj",
        "text": "What is the current status of my project?",
        "expect": {"no_foreign_provenance": True},
    },
)


def load_corpus() -> list[dict]:
    """Deep copy of the frozen corpus (callers may attach runtime fields)."""
    return copy.deepcopy(list(FROZEN_CASES))


def corpus_hash(cases: list[dict] | None = None) -> str:
    """Deterministic sha256 over canonical JSON (frozen-input proof)."""
    rows = cases if cases is not None else list(FROZEN_CASES)
    canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
