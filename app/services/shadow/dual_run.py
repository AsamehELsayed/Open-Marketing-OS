"""DEV-005 W8 — dual-run harness (legacy + new path, same immutable input).

- Legacy leg: app.services.account_manager.handle_turn (frozen behavior).
- New leg: app.graphs.account_manager.run_graph (LEGACY/test fallback
  runner; wraps the same legacy flows — never imports langgraph here, so
  the harness stays deterministic offline).
- Deterministic offline retriever: empty hits / FTS_ONLY, injected into
  BOTH legs so retrieval nondeterminism cannot leak into verdicts.
- Read-only w.r.t. business SQLite: legs must not mutate business tables;
  snapshot_business_tables()/assert_no_business_mutation() prove it.
- Serving rule: served reply is ALWAYS the legacy reply (shadow mode).
"""
from __future__ import annotations

from typing import Any

from app.services.shadow import runtime_flags
from app.services.shadow.parity_corpus import load_corpus

BUSINESS_TABLES = ("campaigns", "approvals", "tasks", "experiments", "companies")


def deterministic_retriever(query: str) -> dict:
    """Offline deterministic retriever shared by both legs."""
    _ = query
    return {"hits": [], "mode": "FTS_ONLY"}


def _norm_reply(text: Any) -> str:
    return (text or "").strip() if isinstance(text, str) else ""


def _provenance_paths(provenance: Any) -> list[str]:
    out: list[str] = []
    for p in provenance or []:
        if isinstance(p, dict) and p.get("path"):
            out.append(str(p["path"]))
    return sorted(set(out))


def run_legacy(conn, root, case: dict, retriever=None) -> dict:
    from app.services import account_manager as legacy

    retr = retriever or deterministic_retriever
    out = legacy.handle_turn(conn, root, case.get("text", ""), retriever=retr)
    return {
        "reply": _norm_reply(out.get("reply_md")),
        "flow": str(out.get("flow", "")),
        "provenance_paths": _provenance_paths(out.get("provenance")),
        "retrieval_used": bool(out.get("retrieval_used")),
        "retrieval_mode": str(out.get("retrieval_mode", "")),
        "mode": str(out.get("mode", "") or ""),
    }


def run_new_path(conn, root, case: dict, retriever=None) -> dict:
    from app.graphs.account_manager import run_graph

    base = retriever or deterministic_retriever
    calls: list[str] = []

    def _spy(q: str) -> dict:
        calls.append(q)
        return base(q)

    project_id = case.get("project_id", "")
    try:
        out = run_graph(
            conn,
            root=root,
            project_id=project_id,
            conversation_id=f"w8-{case.get('case_id', 'c')}",
            turn_id=f"w8-{case.get('case_id', 't')}",
            user_text=case.get("text", ""),
            retriever=_spy,
        )
    except Exception as e:  # never throw out of the harness; record it
        return {
            "reply": "",
            "route": "",
            "provenance_paths": [],
            "retrieval_used": False,
            "retrieval_mode": "ERROR",
            "errors": [f"new-path raised: {e}"],
        }
    return {
        "reply": _norm_reply(out.get("final_answer")),
        "route": str(out.get("route", "")),
        "provenance_paths": _provenance_paths(out.get("provenance")),
        # retrieval was attempted iff the branch invoked the retriever
        # (mirrors legacy retrieval_used semantics; state_only skips it).
        "retrieval_used": bool(calls),
        "retrieval_mode": "",
        "errors": [str(e) for e in (out.get("errors") or [])],
    }


def dual_run_case(conn, root, case: dict, retriever=None) -> dict:
    """Run both legs on one frozen case. Served output is always legacy."""
    retr = retriever or deterministic_retriever
    legacy = run_legacy(conn, root, case, retr)
    new = run_new_path(conn, root, case, retr)
    return {
        "case_id": case.get("case_id", ""),
        "slice": case.get("slice", ""),
        "project_id": case.get("project_id", ""),
        "text": case.get("text", ""),
        "served": "legacy",
        "served_reply": runtime_flags.served_reply(legacy["reply"], new["reply"]),
        "legacy": legacy,
        "new": new,
    }


def run_corpus(conn, root, cases: list[dict] | None = None, retriever=None) -> list[dict]:
    rows = cases if cases is not None else load_corpus()
    return [dual_run_case(conn, root, c, retriever) for c in rows]


def snapshot_business_tables(conn) -> dict:
    snap: dict = {}
    for t in BUSINESS_TABLES:
        try:
            snap[t] = [dict(r) for r in conn.execute(f"SELECT * FROM {t}").fetchall()]
        except Exception as e:  # missing table on bare DBs → record, don't throw
            snap[t] = [f"<unavailable: {e}>"]
    return snap


def assert_no_business_mutation(before: dict, after: dict) -> None:
    assert before == after, "shadow dual-run must never mutate business tables"


def ensure_canary_projects(conn) -> None:
    """Setup helper for throwaway/test DBs ONLY: seed the cross-project
    canary project so the isolation probe exercises leakage (not just
    fail-closed on unknown ids). Never call on production data."""
    from app.database import repos

    if repos.Projects.get(conn, "other-proj") is None:
        repos.Projects.upsert(conn, {
            "id": "other-proj", "name": "Other Project", "website": "",
            "goal": "", "status": "active", "settings_json": "{}",
            "created_at": "2026-09-22T00:00:00+00:00",
            "updated_at": "2026-09-22T00:00:00+00:00"})
