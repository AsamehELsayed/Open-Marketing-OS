"""DEV-005 W8 — parity comparator (verdicts with evidence, never cutover).

Verdict per case:
- ``match``: same normalized reply AND same provenance path set AND same
  retrieval_used flag (deterministic offline legs).
- ``divergence``: anything else, with field-level evidence. Divergences
  are expected on some slices (e.g. missing_project: the new graph fails
  closed NO_PROJECT_SCOPE while legacy serves a state reply) — they are
  recorded, never served, never auto-cutover.

Every verdict carries: case_id, slice, verdict, served="legacy",
cutover_triggered=False, and an evidence dict (reply hashes/lengths,
provenance lists, retrieval flags, route/flow, error strings).
"""
from __future__ import annotations

import hashlib
from typing import Any


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def compare_case(dual: dict) -> dict:
    legacy = dual.get("legacy", {}) or {}
    new = dual.get("new", {}) or {}
    l_reply = legacy.get("reply", "") or ""
    n_reply = new.get("reply", "") or ""
    l_prov = list(legacy.get("provenance_paths", []) or [])
    n_prov = list(new.get("provenance_paths", []) or [])
    l_ret = bool(legacy.get("retrieval_used"))
    n_ret = bool(new.get("retrieval_used"))

    diffs: list[str] = []
    if l_reply != n_reply:
        diffs.append("reply_text")
    if sorted(l_prov) != sorted(n_prov):
        diffs.append("provenance_paths")
    if l_ret != n_ret:
        diffs.append("retrieval_used")

    verdict = "match" if not diffs else "divergence"
    evidence: dict[str, Any] = {
        "diff_fields": diffs,
        "legacy_reply_sha": _sha(l_reply),
        "new_reply_sha": _sha(n_reply),
        "legacy_reply_len": len(l_reply),
        "new_reply_len": len(n_reply),
        "legacy_provenance": sorted(l_prov),
        "new_provenance": sorted(n_prov),
        "legacy_retrieval_used": l_ret,
        "new_retrieval_used": n_ret,
        "legacy_flow": str(legacy.get("flow", "")),
        "new_route": str(new.get("route", "")),
        "legacy_mode": str(legacy.get("retrieval_mode", "")),
        "new_errors": list(new.get("errors", []) or []),
    }
    return {
        "case_id": dual.get("case_id", ""),
        "slice": dual.get("slice", ""),
        "verdict": verdict,
        "served": "legacy",
        "cutover_triggered": False,
        "evidence": evidence,
    }


def compare_all(duals: list[dict]) -> list[dict]:
    return [compare_case(d) for d in duals]


def summarize(verdicts: list[dict]) -> dict:
    by_slice: dict[str, dict] = {}
    for v in verdicts:
        s = v.get("slice", "?")
        row = by_slice.setdefault(s, {"match": 0, "divergence": 0, "cases": []})
        row[v.get("verdict", "divergence")] = row.get(v.get("verdict", "divergence"), 0) + 1
        row["cases"].append(v.get("case_id", ""))
    matches = sum(1 for v in verdicts if v.get("verdict") == "match")
    return {
        "total": len(verdicts),
        "match": matches,
        "divergence": len(verdicts) - matches,
        "by_slice": by_slice,  # per-slice counts, never averaged away
        "served": "legacy",
        "cutover_triggered": False,
    }
