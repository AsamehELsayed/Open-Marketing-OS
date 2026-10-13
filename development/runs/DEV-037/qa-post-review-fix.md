# DEV-037 Post-Review Percentage-Point QA

**Role execution:** Independent QA. The designated `qa` lane was unavailable, so this separate execution used the `general` lane under `docs/development-delegation-system.md` §10. It is independent of planner, implementers, and reviewer. The orchestrator should record `role=qa, agent=general`, output `development/runs/DEV-037/qa-post-review-fix.md`.

**Checkout/base:** `development/worktrees/DEV-037-validator-contract-hardening`; HEAD remains `68148c3184b74a129ded2b377599a930064be88a` plus the final working-tree changes. Prior reports `qa.md`, `qa-rerun.md`, and `qa-final.md` remain preserved.

**VERDICT: PASS.** The percentage-point distinction, prior numeric/sign/unit repairs, and original validator fixes pass the requested deterministic checks. No blocking QA findings remain.

## Commands and results

- `python -m pytest -q --basetemp=development/runs/DEV-037/qa-post-review-temp-numeric app/tests/test_dev033_campaign_validation.py -k "percentage_points_do_not_match_percent or percentage_point_and_percent_fact_keys_are_distinct or percent_and_percent_word_remain_equivalent or numeric_sign_is_part or matching_signed_numeric or metric_mass_equivalence_preserves_negative or range_separator_is_not_a_negative_sign or numeric_facts_preserve_arbitrary_units_and_scale or numeric_unit_and_scale_equivalents_remain_supported or four_percent_baseline or numeric_tokens_keep_full"` — **25 passed, 53 deselected**.
- `python -m pytest -q --basetemp=development/runs/DEV-037/qa-post-review-temp-test033 app/tests/test_dev033_campaign_validation.py` — **78 passed**.
- `python -m pytest -q --basetemp=development/runs/DEV-037/qa-post-review-temp-test032 app/tests/test_dev032_campaign_ai.py` — **67 passed in 107.60s**.
- `python -m pytest -q --basetemp=development/runs/DEV-037/qa-post-review-temp-test031 app/tests/test_dev031_business_workspace.py -k citation_diagnostic` — **15 passed, 5 deselected**, one unrelated Starlette deprecation warning.
- Full-suite total: **160 passed, 0 failed, 5 deselected**. The focused numeric subset overlaps the full validator suite and is reported separately.
- `git diff --check` — passed. The application diff is limited to `app/services/campaign_production.py` and `app/tests/test_dev033_campaign_validation.py`; untracked content is DEV-037 run artifacts and QA-local pytest output.

## Direct numeric and contract probes

All **27 direct probes** through `app.tests.test_dev033_campaign_validation._validate` or `_numeric_fact_keys` matched the expected result:

- **Percent vs percentage points:** `4 percentage points` cannot support `4%`; `4%` cannot support `4 percentage points`. Numeric keys for `%` and `percent` are equivalent in both tested directions, while percentage-point keys are distinct from percent keys.
- **Ranges, tokens, units, scale, and sign:** Exact Unicode `24–40` and `24–40%` do not support `4%`; `4 bags` does not support `4 cups`; `4 thousand` does not support `4`; `4 thousand` supports `4,000`; `3k` supports `3,000`; and negative values do not support corresponding positive values.
- **Recommendations and results:** The unquantified future-metric recommendation passes. An achieved 4% conversion result remains rejected with a later Unknown heading.
- **Citation position and association:** Both immediate citation positions pass for the same statement. Foreign and unknown IDs, unbracketed citation tokens, missing evidence citations, and post-punctuation citation misassociation to the next statement are rejected.
- **Unsupported claims:** Maadi café/location, price, certification, and numeric performance-result claims are rejected.

## Safety and persistence assessment

- **DEV-036 diagnostics — PASS.** The citation diagnostic filter passes with privacy-safe redaction and bounds intact.
- **Project isolation — PASS.** Campaign AI tests pass project-scoped reads/citations and foreign-project rejection.
- **Atomicity and existing content — PASS.** Campaign AI tests pass malformed/incomplete batch preservation, validate-before-write, and no-overwrite behavior.
- **Approved deliverables and idempotency — PASS.** Existing approved deliverable preservation/replay and same-turn idempotency regressions pass.
- **Scope — PASS.** No Northstar client data, provider routing, acceptance harness, installers, or unrelated application features were modified.
- **Deterministic only — PASS.** All checks used local synthetic data, direct validator calls, and test fakes. Real AI/model/provider calls: **0**; no Strategy Brief generation was initiated.

No QA blocker remains. This QA verdict does not replace the independent reviewer’s required post-remediation review and final `APPROVE`.
