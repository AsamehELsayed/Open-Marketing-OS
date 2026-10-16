"""Fail-closed regressions for DEV-038 source and factual acceptance."""
import importlib.util
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import pytest

from app.services.campaign_production import DeliverableValidationError, validate_generated_items

SCRIPT = Path(__file__).with_name("run_synthetic.py")
spec = importlib.util.spec_from_file_location("dev038_synthetic", SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_scripted_strategy_has_substantive_sections_and_distinct_days():
    content = runner.build_synthetic_response("northstar:c000")
    for required in ("Audience and positioning", "Messaging pillars", "Visual guidance",
                     "Proposed success measures", "Performance baselines and results are unknown"):
        assert required in content
    days = re.findall(r"\*\*Day (One|Two|Three|Four|Five|Six|Seven) — ([^:]+):\*\*", content)
    assert len(days) == 7
    assert len({day for day, _ in days}) == 7
    assert len({idea for _, idea in days}) == 7
    assert "[northstar:c000]" in content
    assert "SYNTHETIC" in content


def test_scripted_strategy_passes_real_factual_validator():
    source_id = "northstar:c000"
    content = runner.build_synthetic_response(source_id)
    rows = validate_generated_items(content, types=["strategy_brief"], allowed_ids=[source_id],
        context={"project_id": "northstar", "profile": {"business_name": "Northstar Coffee"},
                 "brief_md": "", "evidence_ids": [source_id],
                 "evidence": [{"source_id": source_id,
                               "text": "Northstar House Blend is sold in 250 g bags for EGP 380."}]},
        user_request=runner.REQUEST)
    assert len(rows) == 1 and rows[0]["type"] == "strategy_brief"


def test_export_calls_production_service_with_exact_scope_and_writes_body(monkeypatch):
    persisted = {"id": "del-one", "project_id": runner.PROJECT_ID,
                 "campaign_id": runner.CAMPAIGN_ID, "content_md": "# SYNTHETIC\nBody\n"}
    calls = []

    def scoped_export(conn, project_id, campaign_id, deliverable_id):
        calls.append((conn, project_id, campaign_id, deliverable_id))
        return dict(persisted), persisted["content_md"]

    monkeypatch.setattr(runner.campaign_deliverables, "markdown_export", scoped_export)
    marker = object()
    with tempfile.TemporaryDirectory(dir=SCRIPT.parent / "synthetic-output") as folder:
        path = Path(folder) / "export.md"
        runner.export_persisted_markdown(marker, persisted, path)
        assert calls == [(marker, runner.PROJECT_ID, runner.CAMPAIGN_ID, "del-one")]
        assert path.read_bytes() == persisted["content_md"].encode("utf-8")


def test_export_refuses_mismatched_service_result(monkeypatch):
    persisted = {"id": "del-one", "project_id": runner.PROJECT_ID,
                 "campaign_id": runner.CAMPAIGN_ID, "content_md": "SYNTHETIC"}
    monkeypatch.setattr(runner.campaign_deliverables, "markdown_export",
                        lambda *_: ({**persisted, "id": "wrong"}, "SYNTHETIC"))
    with tempfile.TemporaryDirectory(dir=SCRIPT.parent / "synthetic-output") as folder:
        path = Path(folder) / "export.md"
        with pytest.raises(RuntimeError, match="did not match"):
            runner.export_persisted_markdown(object(), persisted, path)
        assert not path.exists()


def test_approved_exact_byte_source_path_still_works(monkeypatch):
    # Exercise the preserved exact-byte branch without changing production pins.
    monkeypatch.setattr(runner, "SOURCE_HASH", runner.sha(runner.SOURCE_DB))
    proof = runner.verify_input_database(derived=Path("missing-derived.db"))
    assert proof["provenance_mode"] == "exact_bytes"


@pytest.mark.parametrize("mutation", ["evidence", "extra_row", "extra_schema"])
def test_logical_guard_rejects_changed_source(monkeypatch, mutation):
    # The fixture copies are the only writable databases in these regressions.
    with tempfile.TemporaryDirectory(dir=SCRIPT.parent / "synthetic-output") as folder:
        copied = Path(folder) / "source.db"
        shutil.copyfile(runner.SOURCE_DB, copied)
        with closing(sqlite3.connect(copied)) as conn:
            if mutation == "evidence":
                conn.execute("UPDATE chunks SET text=text||' tampered' WHERE id=(SELECT id FROM chunks WHERE project_id=? LIMIT 1)", (runner.PROJECT_ID,))
            elif mutation == "extra_row":
                conn.execute("INSERT INTO chunks (id,project_id,document_id,chunk_id,header,text,token_est) VALUES (?,?,?,?,?,?,?)",
                             ("dev038-extra", runner.PROJECT_ID, "dev038-extra", "c999", "", "unexpected", 1))
            else:
                conn.execute("CREATE TABLE dev038_extra (id TEXT)")
            conn.commit()
        monkeypatch.setattr(runner, "SOURCE_HASH", "not-the-copied-hash")
        with pytest.raises(RuntimeError, match="mismatch"):
            runner.verify_input_database(source=copied)


def test_logical_guard_rejects_tampered_derived_snapshot(monkeypatch):
    with tempfile.TemporaryDirectory(dir=SCRIPT.parent / "synthetic-output") as folder:
        copied = Path(folder) / "derived.db"
        shutil.copyfile(runner.DERIVED_DB, copied)
        with closing(sqlite3.connect(copied)) as conn:
            conn.execute("UPDATE campaign_deliverables SET title=title||' tampered' WHERE id=?", (runner.SYNTHETIC_ID,))
            conn.commit()
        monkeypatch.setattr(runner, "SOURCE_HASH", "not-the-current-hash")
        with pytest.raises(RuntimeError, match="snapshot hash mismatch"):
            runner.verify_input_database(derived=copied)


def test_source_guard_rejects_dirty_checkout(monkeypatch):
    real_git = runner.git
    monkeypatch.setattr(runner, "git", lambda *args: " M app/deps.py" if args[0] == "status" else real_git(*args))
    with pytest.raises(RuntimeError, match="dirty"):
        runner.verify_source()


def test_source_guard_rejects_wrong_base(monkeypatch):
    real_git = runner.git
    monkeypatch.setattr(runner, "git", lambda *args: "wrong" if args[0] == "merge-base" else real_git(*args))
    with pytest.raises(RuntimeError, match="descendant"):
        runner.verify_source()


def test_source_guard_rejects_non_equivalent_clean_main_tree(monkeypatch):
    real_git = runner.git

    def mismatched_main_tree(*args):
        if args == ("rev-parse", f"{runner.CLEAN_MAIN_BASE}^{{tree}}"):
            return "unexpected-tree"
        return real_git(*args)

    monkeypatch.setattr(runner, "git", mismatched_main_tree)
    with pytest.raises(RuntimeError, match="Clean main tree does not match"):
        runner.verify_source()


def test_cross_project_source_id_rejected():
    text = (
        '## DELIVERABLE type="strategy_brief" title="SYNTHETIC brief" platform="Instagram"\n\n'
        '## Persisted user facts\nBusiness name: Northstar Coffee.\n\n'
        '## Retrieved evidence\nA product claim [other-project:c000].\n\n'
        '## AI suggestions\nConsider a product photo.\n\n'
        '## Unknown or unsupported information\nPerformance is unknown.\n'
    )
    with pytest.raises(DeliverableValidationError):
        validate_generated_items(text, types=["strategy_brief"], allowed_ids=["northstar:c000"],
                                 context={"project_id": "northstar", "profile": {"business_name": "Northstar Coffee"}, "evidence": []})


def test_fabricated_price_rejected():
    text = (
        '## DELIVERABLE type="strategy_brief" title="SYNTHETIC brief" platform="Instagram"\n\n'
        '## Persisted user facts\nBusiness name: Northstar Coffee.\n\n'
        '## Retrieved evidence\nNo project evidence available.\n\n'
        '## AI suggestions\nPrice the coffee at EGP 900.\n\n'
        '## Unknown or unsupported information\nPerformance is unknown.\n'
    )
    with pytest.raises(DeliverableValidationError):
        validate_generated_items(text, types=["strategy_brief"], allowed_ids=[],
                                 context={"project_id": "northstar", "profile": {"business_name": "Northstar Coffee"}, "evidence": []})
