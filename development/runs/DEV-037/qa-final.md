# DEV-037 Final Independent QA

**Role execution:** QA. The designated `qa` lane was unavailable, so this separate independent execution used the `general` lane under `docs/development-delegation-system.md` §10. It is independent of planner, W1, and both remediation executions. The orchestrator should record `role=qa, agent=general`, with output artifact `development/runs/DEV-037/qa-final.md`.

**Checkout/base:** `development/worktrees/DEV-037-validator-contract-hardening`; HEAD is `68148c3184b74a129ded2b377599a930064be88a` plus the final working-tree changes. Both prior FAIL artifacts, `qa.md` and `qa-rerun.md`, are preserved.

**VERDICT: PASS.** The final sign remediation closes the prior QA blocker. The requested deterministic suites, focused numeric regressions, direct acceptance/rejection probes, scope inspection, and whitespace check pass. No blocking QA findings remain. Independent reviewer re-review is still required after remediation.

## Commands and results

- `python -m pytest -q --basetemp=development/runs/DEV-037/qa-final-temp-numeric app/tests/test_dev033_campaign_validation.py -k "numeric_sign_is_part or matching_signed_numeric or metric_mass_equivalence_preserves_negative or range_separator_is_not_a_negative_sign or numeric_facts_preserve_arbitrary_units_and_scale or numeric_unit_and_scale_equivalents_remain_supported or four_percent_baseline or numeric_tokens_keep_full"` — **19 passed, 53 deselected**.
- `python -m pytest -q --basetemp=development/runs/DEV-037/qa-final-temp-test033 app/tests/test_dev033_campaign_validation.py` — **72 passed**.
- `python -m pytest -q --basetemp=development/runs/DEV-037/qa-final-temp-test032 app/tests/test_dev032_campaign_ai.py` — **67 passed in 117.93s**.
- `python -m pytest -q --basetemp=development/runs/DEV-037/qa-final-temp-test031 app/tests/test_dev031_business_workspace.py -k citation_diagnostic` — **15 passed, 5 deselected**, one unrelated Starlette deprecation warning.
- Full suites total: **154 passed, 0 failed, 5 deselected**. The focused numeric result overlaps the full validator suite and is reported separately.
- `git diff --check` — passed. Application diff scope is only `app/services/campaign_production.py` and `app/tests/test_dev033_campaign_validation.py`; other untracked content is DEV-037 run artifacts and QA-local pytest output.

## Direct probes

Direct calls to `app.tests.test_dev033_campaign_validation._validate` produced the expected result in all **21 direct probes** using exact Unicode code points where needed:

- **Numeric false match:** `24–40` and `24–40%` do not support a `4%` baseline; `4 bags` does not support `4 cups`; `4 thousand` does not support bare `4`.
- **Valid numeric equivalence:** `4 thousand` supports `4,000`; `3k` supports `3,000`; `4 thousand bags` supports `4,000 bags`; same-sign numeric values remain supported.
- **Sign and ranges:** `-4 degrees` does not support `4 degrees`, and `4 degrees` does not support `-4 degrees`. A range separator in `24–40` is not treated as a negative sign; the positive endpoint remains positive.
- **Recommendations and outcomes:** `Suggestion: Track engagement, saves, and conversion rate after launch.` passes. `The campaign achieved a 4% conversion rate.` remains rejected after an `Unknown or unsupported information` heading.
- **Citations:** Both `claim [allowed-id].` and `claim. [allowed-id]` pass when immediately attached to the same statement. Foreign and unknown IDs, unbracketed tokens, missing citations, and a citation after one statement supporting a different next statement are rejected.
- **Unsupported factual claims:** Maadi café/location, fabricated price, certification, and numeric performance result claims are rejected.

## Safety and persistence assessment

- **DEV-036 diagnostics — PASS.** The 15-test diagnostic filter passes; redaction, bounded diagnostic data, and privacy-safe citation failure information are preserved.
- **Project isolation — PASS.** The campaign AI suite passes project-scoped retrieval and citation tests, including foreign-project rejection and selected-project data boundaries.
- **Batch atomicity and existing rows — PASS.** Deterministic campaign tests pass malformed/incomplete batch preservation and validation-before-write/no-overwrite cases.
- **Approved deliverables and idempotency — PASS.** Existing approved deliverable preservation/replay and same-turn idempotency regressions pass.
- **Required factual protections — PASS.** Unsupported locations, prices, certifications, numeric results, and foreign citations remain rejected.
- **Determinism — PASS.** All tests and probes used synthetic/local fixtures and validator calls only. Real AI/model/provider calls: **0**; no Strategy Brief generation was initiated.
- **Scope — PASS.** No Northstar client data, provider routing, acceptance harness, installers, or unrelated application features were modified.

## Review gate

QA is PASS. Because the final change followed two remediation steps, an independent reviewer must inspect the latest diff and this QA artifact and provide a final `APPROVE` before the DEV-037 completion gate can pass.
