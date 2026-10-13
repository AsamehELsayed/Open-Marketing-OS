# DEV-037 Remediation 03 — Percentage-Point Unit Distinction

**Role:** Remediation implementer (separate execution).
**Checkout/base:** Isolated DEV-037 checkout, original baseline `68148c3184b74a129ded2b377599a930064be88a`.
**Scope:** `app/services/campaign_production.py`, `app/tests/test_dev033_campaign_validation.py`, and this handoff only.
**Real model/provider calls:** 0. All tests use deterministic local fixtures.

## Review finding addressed

`4 percentage points` and `4%` previously shared the numeric fact key `('4', 'percent')`. The unit parser now recognizes `percentage point` and `percentage points` before generic percent units and assigns them a distinct `percentage_points` key. The same precedence is applied to range endpoints. `%`, `percent`, and `percentage` keep their existing percent equivalence.

## Regression coverage

- End-to-end negative: `4 percentage points` cannot support `4%`.
- End-to-end negative: `4%` cannot support `4 percentage points`.
- Direct fact-key negative in both directions: percentage-point and percent keys are disjoint.
- Positive parser equivalence in both directions: `4%` and `4 percent` yield identical fact keys.
- Existing sign/range, scale, arbitrary-unit, high-risk claim, citation, and validator contract regressions remain covered by the full DEV-033 suite and selected DEV-032 integration cases.

The positive case asserts numeric fact-key equivalence directly. A first validator-level positive fixture also exercised the separate high-risk phrase matcher and was rejected there despite equivalent numeric keys; the direct assertion isolates the unit-normalization contract while the two negatives remain end-to-end validator checks.

## Exact verification

Commands were run from the isolated checkout root.

- Before the parser fix, `python -m pytest -q --basetemp=development/runs/DEV-037/pytest-temp-remediation03 app/tests/test_dev033_campaign_validation.py -k "percentage_points_do_not_match_percent or percent_and_percent_word_remain_equivalent"` — **3 failed, 1 passed, 72 deselected**. The `4 percentage points` to `4%` direction was accepted. The reciprocal negative case and one initial positive validator fixture were rejected by the separate high-risk phrase matcher, so the positive equivalence assertion and reciprocal parser distinction are also covered directly at the fact-key level.
- `python -m pytest -q --basetemp=development/runs/DEV-037/pytest-temp-remediation03 app/tests/test_dev033_campaign_validation.py -k "percentage_points_do_not_match_percent or percentage_point_and_percent_fact_keys_are_distinct or percent_and_percent_word_remain_equivalent"` — **6 passed, 72 deselected**.
- `python -m pytest -q --basetemp=development/runs/DEV-037/pytest-temp-remediation03 app/tests/test_dev033_campaign_validation.py` — **78 passed**.
- `python -m pytest -q --basetemp=development/runs/DEV-037/pytest-temp-remediation03 app/tests/test_dev032_campaign_ai.py -k "unsupported_high_risk_facts or supported_request_price_discount_and_date or saved_batch_replay_precedes_provider_error_or_invalid_output or scoped_citation_diagnostic_is_private_and_batch_is_not_written"` — **7 passed, 60 deselected**.
- `python -m py_compile app/services/campaign_production.py` — passed.
- `git diff --check` — passed.

No QA artifact was edited and no commit was created. Independent QA and reviewer re-review remain required because this remediation changes code after review.
