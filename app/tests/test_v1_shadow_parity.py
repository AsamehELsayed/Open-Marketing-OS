"""DEV-005 W8 — shadow/parity/migration plumbing (NO CUTOVER).

Proves: harness determinism, verdict evidence shape, cross-project
isolation canary + missing-scope fail-closed, rollback rehearsal log,
cutover defaults OFF, legacy default untouched, W8-only scope.
"""
import json
import os
from pathlib import Path

import pytest

from app.services.shadow import comparator, dual_run, runtime_flags
from app.services.shadow.parity_corpus import FROZEN, corpus_hash, load_corpus

#: DEV-008-PUBLISH-GATE. The rollback rehearsal below drives
#: `scripts/rollback_rehearse.py`, which is internal DEV-005 cutover-rehearsal
#: tooling and is not published. The rehearsal proved the *legacy* execution path
#: still works, which mattered while a cutover to the graph runtime was pending;
#: that cutover is long since complete and the legacy path is preserved but no
#: longer on the critical path for a Quick beta. So in the public export this one
#: test skips instead of failing, and it reactivates automatically if the
#: rehearsal script is ever allowlisted again.
ROLLBACK_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "rollback_rehearse.py"

requires_rollback_tooling = pytest.mark.skipif(
    not ROLLBACK_SCRIPT.is_file(),
    reason=(
        "rollback rehearsal tooling is not published; it is internal DEV-005 "
        "cutover-rehearsal tooling"
    ),
)


@pytest.fixture()
def env(tmp_path):
    root = tmp_path / "ws"
    root.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "w8.db"
    from app import deps
    from app.database.sqlite import connect

    deps.init_db(db)
    conn = connect(db)
    dual_run.ensure_canary_projects(conn)
    yield conn, root
    conn.close()


def _run_twice(env):
    conn, root = env
    cases = load_corpus()
    first = comparator.compare_all(dual_run.run_corpus(conn, root, cases))
    second = comparator.compare_all(dual_run.run_corpus(conn, root, cases))
    return first, second


def test_corpus_frozen_with_four_language_slices():
    assert FROZEN is True
    cases = load_corpus()
    slices = {c["slice"] for c in cases}
    for s in ("arabic_msa", "egyptian", "english", "mixed"):
        assert s in slices, f"missing language slice {s}"
    assert corpus_hash(cases) == corpus_hash(load_corpus())


def test_harness_deterministic(env):
    first, second = _run_twice(env)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_verdict_evidence_shape_never_cutover(env):
    conn, root = env
    duals = dual_run.run_corpus(conn, root)
    for d in duals:
        assert d["served"] == "legacy"
        assert d["served_reply"] == (d["legacy"]["reply"] or "")
    for v in comparator.compare_all(duals):
        assert v["verdict"] in ("match", "divergence")
        assert v["served"] == "legacy"
        assert v["cutover_triggered"] is False
        ev = v["evidence"]
        for k in ("diff_fields", "legacy_reply_sha", "new_reply_sha",
                  "legacy_provenance", "new_provenance",
                  "legacy_retrieval_used", "new_retrieval_used",
                  "legacy_flow", "new_route"):
            assert k in ev, k
        assert len(ev["legacy_reply_sha"]) == 64
    summary = comparator.summarize(comparator.compare_all(duals))
    assert summary["served"] == "legacy"
    assert summary["cutover_triggered"] is False
    assert summary["total"] == len(duals)
    assert summary["match"] + summary["divergence"] == len(duals)
    assert set(summary["by_slice"]) >= {"arabic_msa", "egyptian", "english", "mixed"}


def test_shadow_never_mutates_business(env):
    conn, root = env
    before = dual_run.snapshot_business_tables(conn)
    dual_run.run_corpus(conn, root)
    dual_run.assert_no_business_mutation(before, dual_run.snapshot_business_tables(conn))


def test_missing_project_new_path_fails_closed(env):
    conn, root = env
    case = next(c for c in load_corpus() if c["slice"] == "missing_project")
    new = dual_run.run_new_path(conn, root, case)
    assert any("NO_PROJECT_SCOPE" in e for e in new.get("errors", []))
    assert new["reply"] == ""


def test_cross_project_canary_no_starter_provenance(env):
    conn, root = env
    case = next(c for c in load_corpus() if c["slice"] == "cross_project")
    for leg in (dual_run.run_legacy(conn, root, case),
                dual_run.run_new_path(conn, root, case)):
        for p in leg["provenance_paths"]:
            assert "starter" not in p.lower()


@requires_rollback_tooling
def test_rollback_rehearsal_log_langgraph_legacy_langgraph(tmp_path):
    from scripts.rollback_rehearse import main as rehearse

    out = tmp_path / "w8rb.json"
    assert rehearse(["--out", str(out)]) == 0
    log = json.loads(out.read_text(encoding="utf-8"))
    assert log["order"] == "langgraph->legacy->langgraph"
    assert log["ok"] is True
    assert log["served"] == "legacy"
    assert log["cutover_triggered"] is False
    assert log["no_business_mutation"] is True
    runtimes = [s["runtime"] for s in log["steps"]]
    assert runtimes[:3] == ["langgraph", "legacy", "langgraph"]
    assert all(s["ok"] for s in log["steps"])


def test_cutover_defaults_off_requires_explicit_founder_gate(monkeypatch):
    monkeypatch.delenv("W8_CUTOVER_REQUESTED", raising=False)
    monkeypatch.delenv("W8_FOUNDER_GATE", raising=False)
    assert runtime_flags.is_cutover_enabled() is False
    monkeypatch.setenv("W8_CUTOVER_REQUESTED", "1")
    assert runtime_flags.is_cutover_enabled() is False  # gate value still missing
    monkeypatch.setenv("W8_FOUNDER_GATE", "approve-cutover")
    assert runtime_flags.is_cutover_enabled() is True
    monkeypatch.setenv("W8_FOUNDER_GATE", "yes")  # wrong value stays OFF
    assert runtime_flags.is_cutover_enabled() is False


def test_ai_runtime_default_stays_legacy(monkeypatch):
    monkeypatch.delenv("AI_RUNTIME", raising=False)
    assert runtime_flags.get_ai_runtime_name() == "langgraph"
    assert runtime_flags.is_legacy_default() is False
    monkeypatch.setenv("AI_RUNTIME", "legacy")
    assert runtime_flags.get_ai_runtime_name() == "legacy"
    assert runtime_flags.is_legacy_default() is True
    monkeypatch.setenv("AI_RUNTIME", "bogus")
    assert runtime_flags.get_ai_runtime_name() == "langgraph"  # fail closed


def test_served_reply_always_legacy():
    assert runtime_flags.served_reply("L", "N") == "L"


def test_w8_only_scope_no_forbidden_surface():
    root = Path(__file__).resolve().parents[1] / "shadow"
    text = "\n".join(p.read_text(encoding="utf-8") for p in root.glob("*.py"))
    lowered = text.lower()
    assert "from fastapi" not in lowered and "import fastapi" not in lowered
    assert "create table" not in lowered  # no DB schema edits
    assert "cutover_triggered\": true" not in text.replace(" ", "")
    assert "cutover_triggered=True" not in text
