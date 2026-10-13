# DEV-037 Remediation 02 — Signed Numeric Facts

**Role:** Remediation implementer (separate execution).
**Checkout/base:** Isolated DEV-037 checkout, original baseline `68148c3184b74a129ded2b377599a930064be88a`.
**Scope:** `app/services/campaign_production.py`, `app/tests/test_dev033_campaign_validation.py`, and this handoff only.
**Real model/provider calls:** 0. All tests use deterministic local fixtures.

## QA finding addressed

QA reproduced a false accept where `Temperature was -4 degrees.` supported `Suggestion: Use 4 degrees.` because numeric fact keys discarded the sign. Numeric fact keys now preserve a leading minus (including the Unicode minus sign) so negative and nonnegative values remain distinct. A hyphen following a number remains a range separator, including compact `24-40` and spaced range forms, rather than becoming a negative sign. Signed metric mass normalization also retains the sign.

## Regression coverage

- Negative: `-4 degrees` cannot support `4 degrees`.
- Negative: `4 degrees` cannot support `-4 degrees`.
- Positive: matching negative and nonnegative temperature facts remain supported.
- Negative: `-0.25 kg` cannot support `250 g`; positive: it supports the equivalent `-250 g`.
- Negative: either `24–40 degrees` or `24-40 degrees` cannot support `-40 degrees`; positive: the range's `40 degrees` endpoint remains positive and supported.
- Prior arbitrary-unit/scale, complete-token, formatting, and validator contract regressions remain covered by the full deterministic DEV-033 validation suite.

## Exact verification

Commands were run from the isolated checkout root.

- `python -m pytest -q --basetemp=development/runs/DEV-037/pytest-temp-remediation02 app/tests/test_dev033_campaign_validation.py -k "numeric_sign_is_part or matching_signed_numeric or metric_mass_equivalence_preserves_negative or range_separator_is_not_a_negative_sign"` — **7 passed, 65 deselected**.
- `python -m pytest -q --basetemp=development/runs/DEV-037/pytest-temp-remediation02 app/tests/test_dev033_campaign_validation.py` — **72 passed**.
- `python -m pytest -q --basetemp=development/runs/DEV-037/pytest-temp-remediation02 app/tests/test_dev032_campaign_ai.py -k "unsupported_high_risk_facts or supported_request_price_discount_and_date or saved_batch_replay_precedes_provider_error_or_invalid_output or scoped_citation_diagnostic_is_private_and_batch_is_not_written"` — **7 passed, 60 deselected**.
- `python -m py_compile app/services/campaign_production.py` — passed.
- `git diff --check` — passed.

No QA artifact was edited and no commit was created. Independent QA and reviewer re-review remain required because this remediation changes code after review.
