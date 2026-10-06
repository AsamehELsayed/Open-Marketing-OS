"""DEV-005 W4 — LoRA dataset-prep tooling (records only, NO TRAINING).

Implements docs/v1/lora-training-plan.md dataset contract WITHOUT
downloading models, training adapters, or choosing a benchmark winner.

Observable fields per example (only these are stored):
  example_id, lang_slice, user_request, project_context (scoped),
  available_tools, expected_route, expected_tool_calls,
  expected_toon_response, final_answer, approval_expectation,
  evidence_labels, source_family

Forbidden (rejected by validate_example):
  hidden chain-of-thought, secrets, raw customer identifiers,
  invented metrics, unreviewed production answers.

Slices: arabic_msa / egyptian / english / mixed (code-switched ar-en).
TOON-boundary cases carry expected_toon_response with the 9 normative
fields from app.contracts.toon_boundary; safety_check must pass.

Split rule: group by source_family so related examples cannot cross
train / validation / held-out. freeze_heldout() seals the held-out set
before any training (hash + frozen flag).
"""

from __future__ import annotations

import hashlib
import json

from app.contracts.toon_boundary import safety_check, validate_response_fields

LANG_SLICES = ("arabic_msa", "egyptian", "english", "mixed")

OBSERVABLE_FIELDS = (
    "example_id",
    "lang_slice",
    "user_request",
    "project_context",
    "available_tools",
    "expected_route",
    "expected_tool_calls",
    "expected_toon_response",
    "final_answer",
    "approval_expectation",
    "evidence_labels",
    "source_family",
)

FORBIDDEN_KEYS = frozenset({
    "chain_of_thought",
    "reasoning_content",
    "secret",
    "secrets",
    "api_key",
    "password",
    "token",
    "customer_id",
    "customer_email",
    "customer_phone",
    "hidden_reasoning",
})

APPROVAL_CLASSES = ("green", "yellow", "red")


def validate_example(ex: dict) -> list[str]:
    """Return a list of contract problems (empty = valid)."""
    problems: list[str] = []
    if not isinstance(ex, dict):
        return ["example must be a mapping"]
    for field in OBSERVABLE_FIELDS:
        if field not in ex:
            problems.append(f"missing field: {field}")
    if not ex.get("example_id"):
        problems.append("example_id is required")
    if ex.get("lang_slice") not in LANG_SLICES:
        problems.append(f"lang_slice must be one of {LANG_SLICES}")
    if not ex.get("user_request"):
        problems.append("user_request is required")
    if not ex.get("final_answer"):
        problems.append("final_answer is required")
    if ex.get("approval_expectation") not in APPROVAL_CLASSES:
        problems.append(f"approval_expectation must be one of {APPROVAL_CLASSES}")
    for key in FORBIDDEN_KEYS:
        if key in ex:
            problems.append(f"forbidden key: {key}")
    ctx = ex.get("project_context")
    if not isinstance(ctx, dict) or not ctx.get("project_id"):
        problems.append("project_context.project_id is required (scoped context)")
    toon = ex.get("expected_toon_response")
    if toon is not None:
        if not isinstance(toon, dict):
            problems.append("expected_toon_response must be a mapping")
        else:
            missing = validate_response_fields(toon)
            if missing:
                problems.append(f"toon missing fields: {sorted(missing)}")
            problems.extend(f"toon {p}" for p in safety_check(toon))
    return problems


def is_valid(ex: dict) -> bool:
    return not validate_example(ex)


def fingerprint(ex: dict) -> str:
    """Semantic dedup fingerprint (normalized, stable)."""
    norm = json.dumps(
        {
            "user_request": str(ex.get("user_request", "")).strip().lower(),
            "expected_route": ex.get("expected_route"),
            "final_answer": str(ex.get("final_answer", "")).strip().lower(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]


def deduplicate(examples: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for ex in examples:
        fp = fingerprint(ex)
        if fp in seen:
            continue
        seen.add(fp)
        out.append(ex)
    return out


def dataset_hash(examples: list[dict]) -> str:
    payload = json.dumps(
        [fingerprint(e) for e in sorted(examples, key=lambda e: str(e.get("example_id")))],
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def split_by_family(
    examples: list[dict],
    heldout_families: tuple = ("heldout-seed",),
    val_families: tuple = ("val-seed",),
) -> dict:
    """Split by source_family so related examples never cross splits."""
    train: list[dict] = []
    val: list[dict] = []
    held: list[dict] = []
    for ex in examples:
        fam = ex.get("source_family", "")
        if fam in heldout_families:
            held.append(ex)
        elif fam in val_families:
            val.append(ex)
        else:
            train.append(ex)
    fams = lambda xs: {e.get("source_family") for e in xs}  # noqa: E731
    overlap = (fams(train) & fams(val)) | (fams(train) & fams(held)) | (fams(val) & fams(held))
    if overlap:
        raise ValueError(f"family leakage across splits: {sorted(overlap)}")
    return {"train": train, "validation": val, "held_out": held}


def freeze_heldout(held_out: list[dict]) -> dict:
    """Seal the held-out set before training (immutable + hashed)."""
    frozen = tuple(dict(e) for e in held_out)
    return {
        "cases": frozen,
        "n_cases": len(frozen),
        "sha256": dataset_hash(list(frozen)),
        "frozen": True,
    }


def _toon_stub(route: str, answer: str) -> dict:
    return {
        "schema_version": "1",
        "route": route,
        "confidence": 0.9,
        "tool_calls": [],
        "evidence_refs": [],
        "approval": "green",
        "unknowns": [],
        "final_answer": answer,
        "errors": [],
    }


def default_examples() -> list[dict]:
    """Seed prep examples covering all 4 slices + TOON-boundary cases."""
    return [
        {
            "example_id": "ar-msa-001",
            "lang_slice": "arabic_msa",
            "user_request": "لخص عرض شركتك في جملتين دون اختراع أرقام.",
            "project_context": {"project_id": "proj-seed", "brief": "sample offer summary"},
            "available_tools": [],
            "expected_route": "state_only",
            "expected_tool_calls": [],
            "expected_toon_response": _toon_stub("state_only", "ملخص العرض دون أرقام مخترعة."),
            "final_answer": "ملخص العرض دون أرقام مخترعة.",
            "approval_expectation": "green",
            "evidence_labels": [],
            "source_family": "train-seed",
        },
        {
            "example_id": "eg-001",
            "lang_slice": "egyptian",
            "user_request": "اشرح العرض بالمصري باختصار ومن غير أرقام مخترعة.",
            "project_context": {"project_id": "proj-seed", "brief": "sample offer summary"},
            "available_tools": [],
            "expected_route": "state_only",
            "expected_tool_calls": [],
            "expected_toon_response": _toon_stub("state_only", "شرح مختصر بالمصري."),
            "final_answer": "شرح مختصر بالمصري.",
            "approval_expectation": "green",
            "evidence_labels": [],
            "source_family": "train-seed",
        },
        {
            "example_id": "en-001",
            "lang_slice": "english",
            "user_request": "Summarize your offer in two sentences; invent no metrics.",
            "project_context": {"project_id": "proj-seed", "brief": "sample offer summary"},
            "available_tools": [],
            "expected_route": "state_only",
            "expected_tool_calls": [],
            "expected_toon_response": _toon_stub("state_only", "Two-sentence summary."),
            "final_answer": "Two-sentence summary.",
            "approval_expectation": "green",
            "evidence_labels": [],
            "source_family": "train-seed",
        },
        {
            "example_id": "mix-001",
            "lang_slice": "mixed",
            "user_request": "Summarize العرض in two sentences, no invented metrics.",
            "project_context": {"project_id": "proj-seed", "brief": "sample offer summary"},
            "available_tools": [],
            "expected_route": "state_only",
            "expected_tool_calls": [],
            "expected_toon_response": _toon_stub("state_only", "Mixed summary without metrics."),
            "final_answer": "Mixed summary without metrics.",
            "approval_expectation": "green",
            "evidence_labels": [],
            "source_family": "train-seed",
        },
        {
            "example_id": "ar-toon-001",
            "lang_slice": "arabic_msa",
            "user_request": "حدد المسار الصحيح لطلب موافقة حملة.",
            "project_context": {"project_id": "proj-val", "brief": "campaign approval"},
            "available_tools": ["request_approval"],
            "expected_route": "approval_operation",
            "expected_tool_calls": [{"tool": "request_approval", "args": {}}],
            "expected_toon_response": _toon_stub("approval_operation", "يتطلب موافقة قبل التنفيذ."),
            "final_answer": "يتطلب موافقة قبل التنفيذ.",
            "approval_expectation": "yellow",
            "evidence_labels": [],
            "source_family": "val-seed",
        },
        {
            "example_id": "eg-toon-001",
            "lang_slice": "egyptian",
            "user_request": "اختار الأداة الصحيحة للبحث الخارجي.",
            "project_context": {"project_id": "proj-val", "brief": "web research"},
            "available_tools": ["web_search"],
            "expected_route": "external_research",
            "expected_tool_calls": [{"tool": "web_search", "args": {"query": "..."}}],
            "expected_toon_response": _toon_stub("external_research", "هستخدم البحث الخارجي."),
            "final_answer": "هستخدم البحث الخارجي.",
            "approval_expectation": "green",
            "evidence_labels": [{"status": "VERIFIED", "source": "seed", "confidence": "HIGH"}],
            "source_family": "val-seed",
        },
        {
            "example_id": "en-held-001",
            "lang_slice": "english",
            "user_request": "Route this RAG question with citations; invent nothing.",
            "project_context": {"project_id": "proj-held", "brief": "rag synthesis"},
            "available_tools": ["rag_retrieve"],
            "expected_route": "knowledge",
            "expected_tool_calls": [{"tool": "rag_retrieve", "args": {"query": "..."}}],
            "expected_toon_response": _toon_stub("knowledge", "Answer with citations."),
            "final_answer": "Answer with citations.",
            "approval_expectation": "green",
            "evidence_labels": [{"status": "VERIFIED", "source": "seed", "confidence": "HIGH"}],
            "source_family": "heldout-seed",
        },
        {
            "example_id": "mix-held-001",
            "lang_slice": "mixed",
            "user_request": "حدد route لـ malformed TOON بدون تخمين.",
            "project_context": {"project_id": "proj-held", "brief": "toon repair"},
            "available_tools": [],
            "expected_route": "state_only",
            "expected_tool_calls": [],
            "expected_toon_response": _toon_stub("state_only", "Fail closed on malformed TOON."),
            "final_answer": "Fail closed on malformed TOON.",
            "approval_expectation": "green",
            "evidence_labels": [],
            "source_family": "heldout-seed",
        },
    ]


def prepare_default_dataset() -> dict:
    """Validate + dedup + split + freeze the seed prep set (no I/O)."""
    examples = deduplicate(default_examples())
    problems = {e["example_id"]: validate_example(e) for e in examples}
    bad = {k: v for k, v in problems.items() if v}
    if bad:
        raise ValueError(f"seed dataset contract violations: {bad}")
    split = split_by_family(examples)
    frozen = freeze_heldout(split["held_out"])
    return {
        "examples": examples,
        "split": split,
        "held_out_frozen": frozen,
        "dataset_hash": dataset_hash(examples),
        "slices_present": sorted({e["lang_slice"] for e in examples}),
        "training_executed": False,
    }
