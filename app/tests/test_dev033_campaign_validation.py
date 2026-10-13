"""Deterministic regressions for safe campaign fact matching."""

import hashlib

import pytest

from app.services.campaign_production import (
    DeliverableValidationError,
    _numeric_fact_keys,
    validate_generated_items,
)


SOURCE_ID = "northstar-notes.md:c000"


def _block(facts, *, evidence="No project evidence available.",
           suggestions="Use a simple product photo.",
           unknowns="No performance results are documented."):
    return (
        '## DELIVERABLE type="strategy_brief" title="Northstar launch" '
        'platform=""\n\n'
        f"## Persisted user facts\n{facts}\n\n"
        f"## Retrieved evidence\n{evidence}\n\n"
        f"## AI suggestions\n{suggestions}\n\n"
        "## Unknown or unsupported information\n"
        f"{unknowns}\n"
    )


def _validate(facts, *, context=None, allowed_ids=None, evidence="No project evidence available.",
              suggestions="Use a simple product photo.",
              unknowns="No performance results are documented."):
    return validate_generated_items(
        _block(facts, evidence=evidence, suggestions=suggestions, unknowns=unknowns),
        types=["strategy_brief"],
        allowed_ids=allowed_ids if allowed_ids is not None else [SOURCE_ID],
        context=context or {"profile": {}, "brief_md": "", "evidence": []},
    )


def test_exact_grounded_fact_is_accepted():
    context = {
        "project_id": "northstar-test-project",
        "profile": {"business_name": "Northstar Coffee"},
        "brief_md": "",
        "evidence": [],
    }

    rows = _validate("Business name: Northstar Coffee.", context=context)

    assert rows[0]["type"] == "strategy_brief"


@pytest.mark.parametrize("facts", [
    "Business name: Northstar Coffee",
    "**Business name**: Northstar Coffee",
    "**Business name:** Northstar Coffee",
    "- **Business name:** Northstar Coffee",
])
def test_supported_business_name_accepts_plain_and_markdown_field_labels(facts):
    context = {
        "project_id": "northstar-test-project",
        "profile": {"business_name": "Northstar Coffee"},
        "brief_md": "",
        "evidence": [],
    }

    rows = _validate(facts, context=context)

    assert rows[0]["type"] == "strategy_brief"
    assert facts in rows[0]["content_md"]


def test_invented_business_name_with_markdown_field_label_is_rejected():
    context = {
        "project_id": "northstar-test-project",
        "profile": {"business_name": "Northstar Coffee"},
        "brief_md": "",
        "evidence": [],
    }

    with pytest.raises(DeliverableValidationError) as caught:
        _validate("**Business name:** Northstar Roasters", context=context)

    assert caught.value.diagnostic["rule"] == "persisted_fact_source_match"


def test_unsupported_business_location_remains_rejected():
    context = {
        "project_id": "northstar-test-project",
        "profile": {"business_name": "Northstar Coffee"},
        "brief_md": "",
        "evidence": [],
    }

    with pytest.raises(DeliverableValidationError) as caught:
        _validate("Location: Maadi, Cairo, Egypt", context=context)

    assert caught.value.diagnostic["rule"] == "persisted_fact_source_match"


def test_supported_name_does_not_mask_an_invented_location_in_same_claim():
    context = {
        "project_id": "northstar-test-project",
        "profile": {"business_name": "Northstar Coffee"},
        "brief_md": "",
        "evidence": [],
    }

    with pytest.raises(DeliverableValidationError) as caught:
        _validate(
            "**Business name:** Northstar Coffee is located in Maadi",
            context=context,
        )

    assert caught.value.diagnostic["rule"] == "persisted_fact_source_match"
    assert "located in Maadi" in caught.value.diagnostic["claim_excerpt"]


@pytest.mark.parametrize("claim", [
    "Offer: Northstar Coffee costs EGP 500",
    "Certification: certified organic",
])
def test_unsupported_price_or_certification_remains_rejected(claim):
    context = {
        "project_id": "northstar-test-project",
        "profile": {"business_name": "Northstar Coffee"},
        "brief_md": "",
        "evidence": [],
    }

    with pytest.raises(DeliverableValidationError) as caught:
        _validate(claim, context=context)

    assert caught.value.diagnostic["rule"] == "persisted_fact_source_match"


def test_unrecognized_bold_label_cannot_hide_an_unsupported_fact():
    context = {
        "project_id": "northstar-test-project",
        "profile": {"business_name": "Northstar Coffee"},
        "brief_md": "",
        "evidence": [],
    }

    with pytest.raises(DeliverableValidationError) as caught:
        _validate("**Campaign note:** Northstar Coffee", context=context)

    assert caught.value.diagnostic["rule"] == "persisted_fact_source_match"


def test_generation_prompt_keeps_facts_separate_and_quotes_sources():
    from app.services.campaign_production import build_generation_prompt

    system, user = build_generation_prompt(
        {"profile": {}, "brief_md": "", "evidence": []},
        types=["strategy_brief"],
        user_request="Use the supplied project notes.",
    )

    lowered = system.casefold()
    assert "copy source wording exactly" in lowered
    assert "do not paraphrase, translate, merge, or infer" in lowered
    assert "project-scoped evidence" in lowered
    assert "supported facts" in lowered
    assert "unknown information" in lowered
    assert "creative recommendations" in lowered
    assert "business location, branch, physical store" in lowered
    assert "Use the supplied project notes." in user


def test_process_location_does_not_support_a_cafe_location_in_suggestions():
    context = {
        "project_id": "northstar-test-project",
        "profile": {},
        "brief_md": "",
        "evidence": [{
            "source_id": SOURCE_ID,
            "text": "Beans are roasted in small batches in Maadi. "
                    "The café hosts a weekly tasting flight.",
        }],
    }

    with pytest.raises(DeliverableValidationError) as caught:
        _validate(
            "No persisted facts supplied.",
            context=context,
            suggestions="Visit our Maadi café for a tasting.",
        )

    assert caught.value.diagnostic["rule"] == "business_place_source_match"
    assert "Maadi café" in caught.value.diagnostic["claim_excerpt"]
    assert caught.value.diagnostic["available_source_ids"] == [SOURCE_ID]


def test_unknown_wording_does_not_mask_an_unsupported_positive_location():
    with pytest.raises(DeliverableValidationError) as caught:
        _validate(
            "No persisted facts supplied.",
            suggestions=("We are located in Maadi, but our location is unknown."),
        )

    assert caught.value.diagnostic["rule"] == "business_place_source_match"
    assert "located in Maadi" in caught.value.diagnostic["claim_excerpt"]


def test_standalone_unknown_business_location_is_accepted():
    rows = _validate(
        "No persisted facts supplied.",
        unknowns="Our business location is unknown.",
    )

    assert "business location is unknown" in rows[0]["content_md"].lower()


@pytest.mark.parametrize("claim", [
    "Visit our Maadi branch.",
    "The Northstar café is located in Maadi.",
    "We are opening a new café in Maadi.",
    "We are located in Maadi.",
    "We are based in Maadi.",
    "Find us in Maadi.",
    "Our address is Maadi, Cairo.",
    "We have a location in Maadi.",
])
def test_unsupported_branch_or_cafe_claim_is_rejected_in_recommendations(claim):
    with pytest.raises(DeliverableValidationError) as caught:
        _validate("No persisted facts supplied.", suggestions=claim)

    assert caught.value.diagnostic["rule"] == "business_place_source_match"


def test_explicitly_supported_cafe_location_in_profile_is_accepted():
    supported = "Northstar café is located in Cairo, Egypt."
    context = {
        "project_id": "northstar-test-project",
        "profile": {"description": supported},
        "brief_md": "",
        "evidence": [],
    }

    rows = _validate(supported, context=context)

    assert rows[0]["type"] == "strategy_brief"


def test_properly_cited_scoped_evidence_location_is_accepted():
    source_id = "doc-a:chunk-1"
    supported = "Northstar cafe is located in Cairo, Egypt."
    context = {
        "project_id": "northstar-test-project",
        "profile": {},
        "brief_md": "",
        "evidence": [{"source_id": source_id, "text": supported}],
    }

    rows = _validate(
        "No persisted facts supplied.", context=context,
        allowed_ids=[source_id],
        evidence="Northstar cafe is located in Cairo, Egypt [doc-a:chunk-1].",
    )

    assert rows[0]["type"] == "strategy_brief"


@pytest.mark.parametrize("evidence", [
    "Beans are roasted in Maadi [doc-a:chunk-1].",
    "Beans are roasted in Maadi. [doc-a:chunk-1]",
])
def test_retrieved_evidence_accepts_immediate_citation_before_or_after_punctuation(
        evidence):
    source_id = "doc-a:chunk-1"
    context = {
        "project_id": "northstar-test-project",
        "profile": {}, "brief_md": "",
        "evidence": [{"source_id": source_id,
                      "text": "Beans are roasted in Maadi."}],
    }

    rows = _validate(
        "No persisted facts supplied.", context=context,
        allowed_ids=[source_id], evidence=evidence)

    assert rows[0]["evidence_ids"] == [source_id]


def test_trailing_citation_cannot_support_the_next_evidence_statement():
    source_id = "doc-a:chunk-1"
    context = {
        "project_id": "northstar-test-project",
        "profile": {}, "brief_md": "",
        "evidence": [{"source_id": source_id,
                      "text": "Beans are roasted in Maadi."}],
    }

    with pytest.raises(DeliverableValidationError) as caught:
        _validate(
            "No persisted facts supplied.", context=context,
            allowed_ids=[source_id],
            evidence=("Beans are roasted in Maadi. [doc-a:chunk-1] "
                      "The café is located in Giza."))

    assert caught.value.diagnostic["rule"] == "evidence_citation_integrity"


def test_unbracketed_canonical_source_token_remains_rejected():
    with pytest.raises(DeliverableValidationError) as caught:
        _validate(
            "No persisted facts supplied.",
            evidence="Beans are roasted in Maadi. doc-a:chunk-1")

    assert caught.value.diagnostic["rule"] == "scoped_citation_integrity"


def test_matching_location_text_with_other_project_citation_is_rejected():
    source_id = "doc-a:chunk-1"
    supported = "Northstar cafe is located in Cairo, Egypt."
    context = {
        "project_id": "northstar-test-project",
        "profile": {},
        "brief_md": "",
        "evidence": [{"source_id": source_id, "text": supported}],
    }

    with pytest.raises(DeliverableValidationError) as caught:
        _validate(
            "No persisted facts supplied.", context=context,
            allowed_ids=[source_id],
            evidence=("Northstar cafe is located in Cairo, Egypt "
                      "[other-project:chunk-1]."),
        )

    assert caught.value.diagnostic["rule"] == "scoped_citation_integrity"


def test_scoped_citation_failure_exposes_only_safe_token_diagnostic():
    rejected_marker = "PRIVATE-REJECTED-OUTPUT-MARKER-63"
    sensitive_id = "private-doc:chunk-2"
    with pytest.raises(DeliverableValidationError) as caught:
        _validate(
            "No persisted facts supplied.",
            evidence=f"Unsupported source [{sensitive_id}].",
            suggestions=f"{rejected_marker} Use a clear call to action.",
            context={
                "project_id": "isolated-project-secret",
                "profile": {}, "brief_md": "",
                "evidence": [{"source_id": SOURCE_ID, "text": "Scoped source."}],
                "evidence_ids": [SOURCE_ID],
            },
            allowed_ids=[SOURCE_ID],
        )

    diagnostic = caught.value.diagnostic
    assert diagnostic["rule"] == "scoped_citation_integrity"
    assert diagnostic["citation_diagnostic"] == {
        "reason_code": "unknown_bracketed_id",
        "token_form": "bracketed_id",
        "token": "[redacted]",
    }
    assert "claim_excerpt" not in diagnostic
    assert "available_source_ids" not in diagnostic
    assert "project_scope_hash" not in diagnostic
    assert rejected_marker not in str(diagnostic)
    assert sensitive_id not in str(diagnostic)
    assert "isolated-project-secret" not in str(diagnostic)
    assert rejected_marker not in str(caught.value)
    assert sensitive_id not in str(caught.value)


def test_business_brief_alone_does_not_support_persisted_fact():
    supported = "Northstar café is located in Cairo, Egypt."
    context = {
        "project_id": "northstar-test-project",
        "profile": {},
        "brief_md": supported,
        "evidence": [],
    }

    with pytest.raises(DeliverableValidationError) as caught:
        _validate(supported, context=context)

    assert caught.value.diagnostic["rule"] == "persisted_fact_source_match"


def test_business_brief_alone_does_not_support_location_in_suggestions():
    supported = "Northstar café is located in Cairo, Egypt."
    context = {
        "project_id": "northstar-test-project",
        "profile": {},
        "brief_md": supported,
        "evidence": [],
    }

    with pytest.raises(DeliverableValidationError) as caught:
        _validate("No persisted facts supplied.", context=context,
                  suggestions=supported)

    assert caught.value.diagnostic["rule"] == "business_place_source_match"


def test_missing_business_information_can_remain_unknown():
    context = {
        "project_id": "northstar-test-project",
        "profile": {},
        "brief_md": "",
        "evidence": [],
    }
    unknowns = (
        "Business location is unknown. Price is unknown. Discount is unknown. "
        "Certification is unknown. Delivery terms are unknown. "
        "Performance results and testimonials are unknown."
    )

    rows = _validate(
        "No persisted facts supplied.", context=context,
        suggestions="Use a product close-up.", unknowns=unknowns)

    assert "location is unknown" in rows[0]["content_md"].lower()
    assert "certification is unknown" in rows[0]["content_md"].lower()


def test_harmless_business_name_paraphrase_is_accepted():
    context = {
        "profile": {"description": "Business name: Northstar Coffee."},
        "brief_md": "",
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


@pytest.mark.parametrize("source", [
    "The prior conversion rate was 24–40.",
    "The prior conversion rate was 24–40%.",
])
def test_four_percent_baseline_does_not_match_a_larger_source_range(source):
    context = {
        "profile": {}, "brief_md": "",
        "evidence": [{"source_id": SOURCE_ID, "text": source}],
    }

    with pytest.raises(DeliverableValidationError) as caught:
        _validate(
            "No persisted facts supplied.", context=context,
            suggestions="Suggestion: Use a 4% baseline conversion rate.")

    assert caught.value.diagnostic["rule"] == "numeric_source_match"


def test_unsupported_achieved_conversion_rate_stays_rejected_after_unknown_heading():
    context = {
        "profile": {}, "brief_md": "",
        "evidence": [{"source_id": SOURCE_ID,
                      "text": "Prior conversion rates ranged from 24–40%."}],
    }

    with pytest.raises(DeliverableValidationError) as caught:
        _validate(
            "No persisted facts supplied.", context=context,
            suggestions="The campaign achieved a 4% conversion rate.",
            unknowns="Results are unknown.")

    assert caught.value.diagnostic["rule"] in {
        "numeric_source_match", "high_risk_source_match"}


@pytest.mark.parametrize(("source", "claim"), [
    ("The source mentions 104 clicks.", "Suggestion: Run 4 clicks."),
    ("The rate was 14.5%.", "Suggestion: Expect a 4.5% rate."),
    ("The price was $4.", "Suggestion: Use a 4% conversion rate."),
    ("The campaign had 40 customers.", "Suggestion: Reach 4 customers."),
])
def test_numeric_tokens_keep_full_value_and_semantic_units(source, claim):
    context = {
        "profile": {}, "brief_md": "",
        "evidence": [{"source_id": SOURCE_ID, "text": source}],
    }

    with pytest.raises(DeliverableValidationError) as caught:
        _validate("No persisted facts supplied.", context=context,
                  suggestions=claim)

    assert caught.value.diagnostic["rule"] in {
        "numeric_source_match", "high_risk_source_match"}


@pytest.mark.parametrize(("source", "claim"), [
    ("The prior change was 4 percentage points.",
     "Suggestion: Use a 4% baseline."),
    ("The prior rate was 4%.",
     "Suggestion: Use 4 percentage points as the baseline."),
])
def test_percentage_points_do_not_match_percent(source, claim):
    context = {
        "profile": {}, "brief_md": "",
        "evidence": [{"source_id": SOURCE_ID, "text": source}],
    }

    with pytest.raises(DeliverableValidationError) as caught:
        _validate("No persisted facts supplied.", context=context,
                  suggestions=claim)

    assert caught.value.diagnostic["rule"] == "numeric_source_match"


@pytest.mark.parametrize(("source", "claim"), [
    ("4 percentage points", "4%"),
    ("4%", "4 percentage points"),
])
def test_percentage_point_and_percent_fact_keys_are_distinct(source, claim):
    assert _numeric_fact_keys(source).isdisjoint(_numeric_fact_keys(claim))


@pytest.mark.parametrize(("source", "claim"), [
    ("4%", "4 percent"),
    ("4 percent", "4%"),
])
def test_percent_and_percent_word_remain_equivalent(source, claim):
    assert _numeric_fact_keys(source) == _numeric_fact_keys(claim)


@pytest.mark.parametrize(("source", "claim"), [
    ("Temperature was -4 degrees.", "Suggestion: Use 4 degrees."),
    ("Temperature was 4 degrees.", "Suggestion: Use -4 degrees."),
])
def test_numeric_sign_is_part_of_the_supported_fact(source, claim):
    context = {
        "profile": {}, "brief_md": "",
        "evidence": [{"source_id": SOURCE_ID, "text": source}],
    }

    with pytest.raises(DeliverableValidationError) as caught:
        _validate("No persisted facts supplied.", context=context,
                  suggestions=claim)

    assert caught.value.diagnostic["rule"] == "numeric_source_match"


@pytest.mark.parametrize("temperature", ["-4", "4"])
def test_matching_signed_numeric_fact_remains_supported(temperature):
    context = {
        "profile": {}, "brief_md": "",
        "evidence": [{
            "source_id": SOURCE_ID,
            "text": f"Temperature was {temperature} degrees.",
        }],
    }

    rows = _validate(
        "No persisted facts supplied.", context=context,
        suggestions=f"Suggestion: Use {temperature} degrees.")

    assert rows[0]["type"] == "strategy_brief"


def test_metric_mass_equivalence_preserves_negative_sign():
    context = {
        "profile": {}, "brief_md": "",
        "evidence": [{
            "source_id": SOURCE_ID,
            "text": "The temperature change was -0.25 kg equivalent.",
        }],
    }

    with pytest.raises(DeliverableValidationError) as caught:
        _validate(
            "No persisted facts supplied.", context=context,
            suggestions="Suggestion: Use 250 g.")
    assert caught.value.diagnostic["rule"] == "numeric_source_match"

    rows = _validate(
        "No persisted facts supplied.", context=context,
        suggestions="Suggestion: Use -250 g.")
    assert rows[0]["type"] == "strategy_brief"


@pytest.mark.parametrize("range_text", ["24–40", "24-40"])
def test_range_separator_is_not_a_negative_sign(range_text):
    context = {
        "profile": {}, "brief_md": "",
        "evidence": [{
            "source_id": SOURCE_ID,
            "text": f"The valid temperature range is {range_text} degrees.",
        }],
    }

    with pytest.raises(DeliverableValidationError) as caught:
        _validate(
            "No persisted facts supplied.", context=context,
            suggestions="Suggestion: Use -40 degrees.")

    assert caught.value.diagnostic["rule"] == "numeric_source_match"

    rows = _validate(
        "No persisted facts supplied.", context=context,
        suggestions="Suggestion: Use 40 degrees.")
    assert rows[0]["type"] == "strategy_brief"


@pytest.mark.parametrize(("source", "claim"), [
    ("The package contains 4 bags.", "Suggestion: Use 4 cups."),
    ("The package contains 4 thousand bags.", "Suggestion: Use 4 cups."),
    ("The source reports 4 thousand.", "Suggestion: Use 4."),
])
def test_numeric_facts_preserve_arbitrary_units_and_scale(source, claim):
    context = {
        "profile": {}, "brief_md": "",
        "evidence": [{"source_id": SOURCE_ID, "text": source}],
    }

    with pytest.raises(DeliverableValidationError) as caught:
        _validate("No persisted facts supplied.", context=context,
                  suggestions=claim)

    assert caught.value.diagnostic["rule"] == "numeric_source_match"


@pytest.mark.parametrize(("source", "claim"), [
    ("The package contains 4 bags.", "Suggestion: Use 4 bag."),
    ("The package contains 4 thousand bags.", "Suggestion: Use 4,000 bags."),
    ("The package contains 4k cups.", "Suggestion: Use 4 thousand cups."),
])
def test_numeric_unit_and_scale_equivalents_remain_supported(source, claim):
    context = {
        "profile": {}, "brief_md": "",
        "evidence": [{"source_id": SOURCE_ID, "text": source}],
    }

    rows = _validate("No persisted facts supplied.", context=context,
                     suggestions=claim)

    assert rows[0]["type"] == "strategy_brief"


def test_unquantified_future_metric_tracking_suggestion_is_allowed():
    rows = _validate(
        "No persisted facts supplied.",
        suggestions=("Suggestion: Track engagement, saves, and conversion rate "
                     "after launch."))

    assert rows[0]["type"] == "strategy_brief"


@pytest.mark.parametrize("suggestion", [
    "Suggestion: The campaign achieved a 4% conversion rate.",
    "Suggestion: Track engagement and report a 4% conversion rate after launch.",
])
def test_suggestion_label_does_not_exempt_performance_claims(suggestion):
    with pytest.raises(DeliverableValidationError):
        _validate("No persisted facts supplied.", suggestions=suggestion)


def test_negation_in_a_separate_statement_does_not_mask_a_performance_claim():
    with pytest.raises(DeliverableValidationError) as caught:
        _validate(
            "No persisted facts supplied.",
            suggestions=("Conversion rate is unknown. The campaign achieved "
                         "a 4% conversion rate."))

    assert caught.value.diagnostic["rule"] in {
        "numeric_source_match", "high_risk_source_match"}


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
        "profile": {"business_name": "Northstar Coffee"},
        "brief_md": "",
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
        "profile": {"business_name": "Northstar Coffee"},
        "brief_md": "",
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
