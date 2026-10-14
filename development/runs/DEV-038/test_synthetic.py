"""Fail-closed regressions for DEV-038 source and factual acceptance."""
import importlib.util
import re
import tempfile
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
