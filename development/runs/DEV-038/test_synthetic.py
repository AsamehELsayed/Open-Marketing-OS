"""Fail-closed regressions for DEV-038 source and factual acceptance."""
import importlib.util
import re
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
