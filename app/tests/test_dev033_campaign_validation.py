"""Deterministic regressions for safe campaign fact matching."""

import hashlib

import pytest

from app.services.campaign_production import (
    DeliverableValidationError,
    validate_generated_items,
)


SOURCE_ID = "northstar-notes.md:c000"


def _block(facts, *, suggestions="Use a simple product photo."):
    return (
        '## DELIVERABLE type="strategy_brief" title="Northstar launch" '
        'platform=""\n\n'
        f"## Persisted user facts\n{facts}\n\n"
        f"## Retrieved evidence\nNo project evidence available.\n\n"
        f"## AI suggestions\n{suggestions}\n\n"
        "## Unknown or unsupported information\n"
        "No performance results are documented.\n"
    )


def _validate(facts, *, context=None, suggestions="Use a simple product photo."):
    return validate_generated_items(
        _block(facts, suggestions=suggestions),
        types=["strategy_brief"],
        allowed_ids=[SOURCE_ID],
        context=context or {"profile": {}, "brief_md": "", "evidence": []},
    )


def test_exact_grounded_fact_is_accepted():
    context = {
        "project_id": "northstar-test-project",
        "profile": {},
        "brief_md": "Business name: Northstar Coffee.",
        "evidence": [],
    }

    rows = _validate("Business name: Northstar Coffee.", context=context)

    assert rows[0]["type"] == "strategy_brief"


def test_generation_prompt_keeps_facts_separate_and_quotes_sources():
    from app.services.campaign_production import build_generation_prompt

    system, user = build_generation_prompt(
        {"profile": {}, "brief_md": "", "evidence": []},
        types=["strategy_brief"],
        user_request="Use the supplied project notes.",
    )

    assert "copy the source wording exactly" in system.casefold()
    assert "do not paraphrase, combine, translate, or infer facts" in system.casefold()
    assert "project-scoped evidence" in system.casefold()
    assert "Use the supplied project notes." in user


def test_harmless_business_name_paraphrase_is_accepted():
    context = {
        "profile": {},
        "brief_md": "Business name: Northstar Coffee.",
        "evidence": [],
    }

    rows = _validate("The business is named Northstar Coffee.", context=context)

    assert rows[0]["type"] == "strategy_brief"


def test_thousands_separator_formatting_equivalence_is_accepted():
    context = {
        "profile": {},
        "brief_md": "",
        "evidence": [{
            "source_id": SOURCE_ID,
            "text": "Free delivery applies to Cairo orders over EGP 1,000.",
        }],
    }

    rows = _validate(
        "Free delivery applies to Cairo orders over EGP 1000.",
        context=context,
    )

    assert rows[0]["type"] == "strategy_brief"


def test_metric_mass_equivalence_is_accepted():
    context = {
        "profile": {},
        "brief_md": "",
        "evidence": [{"source_id": SOURCE_ID,
                      "text": "Northstar House Blend is packed in 250 g bags."}],
    }

    rows = _validate(
        "Northstar House Blend is packed in 0.25 kg bags.",
        context=context,
    )

    assert rows[0]["type"] == "strategy_brief"


@pytest.mark.parametrize("claim", [
    "Northstar House Blend is packed in 250 ml bags.",
    "Northstar House Blend is packed in 0.5 kg bags.",
])
def test_non_equivalent_units_or_values_are_still_rejected(claim):
    context = {
        "profile": {},
        "brief_md": "",
        "evidence": [{"source_id": SOURCE_ID,
                      "text": "Northstar House Blend is packed in 250 g bags."}],
    }

    with pytest.raises(DeliverableValidationError):
        _validate(claim, context=context)


def test_project_knowledge_tasting_note_paraphrase_is_accepted():
    context = {
        "profile": {},
        "brief_md": "",
        "evidence": [{
            "source_id": SOURCE_ID,
            "text": "Northstar House Blend has tasting notes of cocoa and caramel.",
        }],
    }

    rows = _validate(
        "Northstar House Blend has flavor notes of cocoa and caramel.",
        context=context,
    )

    assert rows[0]["type"] == "strategy_brief"


def test_unsupported_business_claim_is_rejected():
    context = {
        "profile": {},
        "brief_md": "Business name: Northstar Coffee.",
        "evidence": [],
    }

    with pytest.raises(DeliverableValidationError):
        _validate(
            "The business is named Northstar Coffee and is certified organic.",
            context=context,
        )


@pytest.mark.parametrize("claim", [
    "Price the bag at EGP 900.",
    "Expect a 17% conversion rate.",
    "Testimonial: Best coffee in Cairo.",
    "Promote our certified organic beans.",
    "Offer 20% off.",
    "Delivery is guaranteed by tomorrow.",
])
def test_invented_commercial_and_high_risk_claims_remain_rejected(claim):
    context = {
        "profile": {},
        "brief_md": "",
        "evidence": [{
            "source_id": SOURCE_ID,
            "text": "Northstar House Blend is sold in 250 g bags for EGP 380. "
                    "Free delivery applies to Cairo orders over EGP 1,000.",
        }],
    }

    with pytest.raises(DeliverableValidationError):
        _validate("No persisted facts supplied.", context=context,
                  suggestions=claim)


def test_rejection_diagnostic_is_bounded_project_scoped_and_actionable():
    context = {
        "project_id": "northstar-test-project",
        "profile": {},
        "brief_md": "Business name: Northstar Coffee.",
        "evidence": [{"source_id": SOURCE_ID,
                      "text": "Northstar House Blend is a coffee."}],
    }

    with pytest.raises(DeliverableValidationError) as caught:
        _validate("Contact: owner@example.com", context=context)

    diagnostic = caught.value.diagnostic
    assert diagnostic["deliverable_id"] is None
    assert diagnostic["deliverable_ordinal"] == 1
    assert diagnostic["deliverable_type"] == "strategy_brief"
    assert diagnostic["rule"] == "persisted_fact_source_match"
    assert diagnostic["classification"] == "unverified"
    assert diagnostic["available_source_ids"] == [SOURCE_ID]
    assert diagnostic["project_scope_hash"] == hashlib.sha256(
        b"northstar-test-project").hexdigest()[:16]
    assert "[redacted email]" in diagnostic["claim_excerpt"].lower()
    assert len(diagnostic["claim_excerpt"]) <= 160
    assert diagnostic["can_revise"] is True
    assert diagnostic["can_retry"] is True
    assert "Nothing was saved" in str(caught.value)
