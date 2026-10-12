# DEV-035 Mini-plan — Markdown field-label validation

**Classification:** SMALL — bounded parser regression in persisted-fact validation, with focused deterministic tests.
**Base:** Public `main` commit `3c84e33a81a1a3333a0b131503a3bbae9b8635f5`.
**Scope:** `app/services/campaign_production.py` and `app/tests/test_dev033_campaign_validation.py`.

## Goal

Accept the supported Northstar business name when its recognized `Business name` field label uses the four requested plain/Markdown/list formats, while continuing to compare the factual value against allowed source text exactly under the existing normalization rules.

## Root cause

`_validate_persisted_facts()` removes a bold label only when the colon follows the closing `**`. For `**Business name:** Northstar Coffee`, the colon is inside the bold markers, so the label remains in the candidate claim and the value fails `persisted_fact_source_match` against the profile value `Northstar Coffee`.

## Implementation

- Normalize the Markdown delimiters and colon placement only for recognized Business Profile field labels before the existing source-value comparison.
- Leave the factual value unchanged apart from existing whitespace normalization; do not broaden semantic matching, discard trailing claim text, or rewrite generated content.
- Add deterministic tests for all four requested formats, invented names, unsupported location/price/certification claims, and a supported name combined with an invented location.
- Keep citation, project-isolation, numeric/high-risk, batch atomicity, and preservation behavior unchanged; exercise those through the existing campaign regression suites.

## Non-goals and safety

- No inference/provider calls, Northstar database or profile edits, generation reruns, provider/account setting changes, release work, or installer changes.
- No changes to evidence matching, source scoping, citation checks, numeric/high-risk checks, persistence, or delivery behavior.

## Verification

- First record the deterministic pre-fix failure for `**Business name:** Northstar Coffee` against the allowed value `Northstar Coffee`.
- Run the focused DEV-033 validation suite, DEV-034 campaign AI suite, campaign integration/atomicity suite, and existing automatic-campaign suite with deterministic fixtures only.
- Have an independent QA execution inspect the diff and test the unchanged safety invariants; record the outcome in this run.
- Have a separate independent reviewer inspect the final diff, regression evidence, and QA report and record a verdict. This satisfies the repository's `approved`-stage gate without changing the SMALL classification.
- Open one PR from the focused branch and merge only after all applicable CI checks pass.
