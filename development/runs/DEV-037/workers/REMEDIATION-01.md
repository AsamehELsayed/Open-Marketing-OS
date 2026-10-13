# DEV-037 Remediation 01 — Numeric Unit and Scale Preservation

**Role:** Remediation implementer (separate execution).
**Checkout/base:** Isolated DEV-037 checkout, original baseline `68148c3184b74a129ded2b377599a930064be88a`.
**Scope:** `app/services/campaign_production.py`, `app/tests/test_dev033_campaign_validation.py`, and this handoff only.
**Real model/provider calls:** 0. All tests use deterministic local fixtures.

## QA finding addressed

The initial numeric fact key preserved only recognized units and collapsed arbitrary adjacent quantities to bare numbers. That allowed all three reported false accepts: `4 bags` supporting `4 cups`, `4 thousand bags` supporting `4 cups`, and `4 thousand` supporting bare `4`.

The parser now retains adjacent unit words, canonicalizes simple singular/plural variants, and applies explicit scale multipliers for hundred, thousand, million, billion, trillion, dozen, and the `k` suffix. Scale factors are applied before comparison; unknown units are not discarded. Existing comma-group and metric-mass equivalences remain in place.

## Regression coverage

- Negative: `4 bags` cannot support `4 cups`.
- Negative: `4 thousand bags` cannot support `4 cups`.
- Negative: `4 thousand` cannot support bare `4`.
- Positive: `4 bags` supports `4 bag`.
- Positive: `4 thousand bags` supports `4,000 bags`.
- Positive: `4k cups` supports `4 thousand cups`.
- Existing validator suite retains numeric-token boundaries, percent/currency/count distinctions, comma formatting, metric mass equivalence, proposed-metric handling, citation association, and unsupported location claims.

## Exact verification

- `python -m pytest -q --basetemp=development/runs/DEV-037/pytest-temp app/tests/test_dev033_campaign_validation.py -k "numeric_facts_preserve or numeric_unit_and_scale_equivalents or four_percent_baseline or numeric_tokens_keep_full"` — **12 passed, 53 deselected**.
- `python -m py_compile app/services/campaign_production.py; python -m pytest -q --basetemp=development/runs/DEV-037/pytest-temp app/tests/test_dev033_campaign_validation.py` — **65 passed**.
- `python -m pytest -q --basetemp=development/runs/DEV-037/pytest-temp app/tests/test_dev032_campaign_ai.py -k "unsupported_high_risk_facts or supported_request_price_discount_and_date or saved_batch_replay_precedes_provider_error_or_invalid_output or scoped_citation_diagnostic_is_private_and_batch_is_not_written"` — **7 passed, 60 deselected**.
- `git diff --check` — passed.

No assertion failures remain in these remediation checks. A prior focused run before correcting terminal-period token boundaries reported one failure for bare `4.`; the tokenizer was corrected and the 65-test full validator suite plus the focused campaign integration cases passed afterward.

No QA artifact was edited, no commit was created, and no excluded project files or data were touched. Independent re-review remains required because this remediation changes code after QA.
