# DEV-037 Independent QA Rerun

**Role execution:** QA. The designated `qa` lane was unavailable, so this independent rerun used the `general` lane under `docs/development-delegation-system.md` §10. It is separate from planner, W1, and remediation executions. The orchestrator should record this execution with `role=qa, agent=general` and output `development/runs/DEV-037/qa-rerun.md`.

**Checkout/base:** `development/worktrees/DEV-037-validator-contract-hardening`; HEAD remains `68148c3184b74a129ded2b377599a930064be88a` plus the final W1 and remediation working-tree changes.

**VERDICT: FAIL** — the initial arbitrary-unit/scale defect is fixed and the required suites pass, but numeric validation still accepts a negative quantity as support for its positive counterpart. The approved numeric meaning contract is not yet satisfied.

## Commands and results

- `python -m pytest -q --basetemp=development/runs/DEV-037/qa-rerun-temp-numeric app/tests/test_dev033_campaign_validation.py -k "numeric_facts_preserve or numeric_unit_and_scale_equivalents or four_percent_baseline or numeric_tokens_keep_full"` — **12 passed, 53 deselected**.
- `python -m pytest -q --basetemp=development/runs/DEV-037/qa-rerun-temp-test033 app/tests/test_dev033_campaign_validation.py` — **65 passed**.
- `python -m pytest -q --basetemp=development/runs/DEV-037/qa-rerun-temp-test032 app/tests/test_dev032_campaign_ai.py` — **67 passed in 143.20s**.
- `python -m pytest -q --basetemp=development/runs/DEV-037/qa-rerun-temp-test031 app/tests/test_dev031_business_workspace.py -k citation_diagnostic` — **15 passed, 5 deselected**, one unrelated Starlette deprecation warning.
- Full-suite total: **147 passed, 0 failed, 5 deselected**. The 12 focused tests overlap with the full validator suite and are reported separately.
- `git diff --check` — passed. Inspected changed application files are limited to `app/services/campaign_production.py` and `app/tests/test_dev033_campaign_validation.py`; the remaining untracked content is DEV-037 run artifacts and QA-local pytest temp output.

All execution was deterministic and offline. There were **0 real AI/model/provider calls** and no Strategy Brief generation attempt.

## Blocking finding — sign is discarded from numeric tokens

The final validator accepts this unsupported claim:

- Source evidence: `Temperature was -4 degrees.`
- Generated claim: `Suggestion: Use 4 degrees.`
- Observed result: **ACCEPTED**.

The token matcher extracts the digits `4`, and `_numeric_fact_key` does not preserve the preceding minus sign. A negative quantity therefore compares equal to the positive quantity. This violates the requirement to retain the meaning of numeric quantities. Add a deterministic regression and preserve sign semantics before numeric validation can pass.

Other magnitude probes behaved as expected:

- Source `The source reports 4 thousand.` supports `Suggestion: Use 4,000.` — **ACCEPTED**.
- Source `The source reports 3k.` supports `Suggestion: Use 3,000.` — **ACCEPTED**.
- Source `The package contains 4 thousand bags.` supports `Suggestion: Use 4,000 bags.` — **ACCEPTED**.
- Source `The campaign had 4 fewer customers.` does **not** support `Suggestion: Get 4 more customers.` — **REJECTED**.

The regression tests also reject `4 bags` supporting `4 cups`, `4 thousand bags` supporting `4 cups`, and `4 thousand` supporting bare `4`.

## Preservation matrix

- **Fabricated 4% baseline and numeric boundaries — PASS.** Both `24–40` and `24–40%` fail to support a generated `4%`; larger-number, decimal, percentage, price, and recognized-count-unit collision tests pass.
- **Units and valid normalizations — PARTIAL / FAIL.** Arbitrary bag/cup distinction and magnitude semantics now pass their negative/positive regressions. Comma grouping, metric mass equivalence, `4 thousand`/`4,000`, and `3k`/`3,000` are preserved. Signed numeric meaning still fails as described above.
- **Proposed metric vs observed result — PASS.** `Suggestion: Track engagement, saves, and conversion rate after launch.` is accepted. Unsupported achieved/quantified 4% claims are rejected, including when followed by an `Unknown or unsupported information` heading; separate unknown/negated wording does not mask the result claim. The suggestion rule remains bounded and does not exempt arbitrary suggestions from numeric or high-risk checks.
- **Citation punctuation and ownership — PASS.** Both `claim [allowed-id].` and `claim. [allowed-id]` pass when immediate and tied to the same statement. Unknown IDs, foreign-project IDs, unbracketed noncanonical tokens, missing evidence citations, and a trailing citation supporting a different next statement remain rejected.
- **Unsupported place and commercial claims — PASS.** Unsupported Maadi café/branch/location, price, certification, and performance-result claims remain rejected. Supported location evidence remains accepted only through the existing scoped sources.
- **DEV-036 diagnostics — PASS.** The filtered diagnostic suite passes with redaction and bounds intact; no rejected prose or private IDs are exposed by the tested diagnostics.
- **Project isolation and persistence — PASS in the campaign AI suite.** Project-scoped retrieval/citations, foreign-project isolation, validate-before-write behavior, atomic batch preservation, existing approved deliverable preservation/replay, and same-turn idempotency tests pass.
- **Scope — PASS.** No Northstar data, provider routing, acceptance harness, installers, or unrelated application files are changed.

## Required remediation

Retain the sign of signed numeric tokens in numeric fact comparison and add a deterministic negative test for `-4` versus `4` with the same unit. Re-run the focused numeric regressions and all three requested suites, then obtain an independent re-QA verdict. This is within the approved DEV-037 scope. No independent reviewer verdict is made here; review remains outstanding.
