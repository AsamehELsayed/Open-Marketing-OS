# DEV-037 Independent QA

**Role execution:** QA. The designated `qa` lane was unavailable in this runtime, so this was a separate `general`-lane execution under `docs/development-delegation-system.md` §10. It is independent of planner and W1. The orchestrator must record this execution as `role=qa, agent=general` in `executions.jsonl`.

**Checkout/base:** `development/worktrees/DEV-037-validator-contract-hardening`, HEAD `68148c3184b74a129ded2b377599a930064be88a` with W1 changes.

**Verdict: FAIL** — the three reported defects are mostly fixed and all requested automated suites pass, but direct numeric probes find an in-scope unsupported-quantity acceptance. Do not pass the QA gate until remediation and re-QA.

## Commands and results

- `python -m pytest -q --basetemp=development/runs/DEV-037/qa-temp-test033 app/tests/test_dev033_campaign_validation.py` — **59 passed**.
- `python -m pytest -q --basetemp=development/runs/DEV-037/qa-temp-test032 app/tests/test_dev032_campaign_ai.py` — **67 passed**.
- `python -m pytest -q --basetemp=development/runs/DEV-037/qa-temp-test031 app/tests/test_dev031_business_workspace.py -k citation_diagnostic` — **15 passed, 5 deselected**, one unrelated Starlette deprecation warning.
- Total: **141 passed, 0 failed, 5 deselected**. Each suite was run as a separate deterministic offline command. No model/provider calls were made.
- `git diff --check` — passed. Changed application scope is limited to `app/services/campaign_production.py` and `app/tests/test_dev033_campaign_validation.py`, plus DEV-037 run artifacts.

## Findings

### Blocking — numeric quantity units and scale words can be discarded

`_numeric_fact_key` assigns an unrecognized adjacent quantity word the generic `number` unit, so distinct meanings compare equal. Scale words such as `thousand` are also not preserved when followed by an unrecognized quantity.

Direct calls to the generated-item validator produced:

- Source: `The package contains 4 bags.` Claim: `Suggestion: Use 4 cups.` — **ACCEPTED**.
- Source: `The package contains 4 thousand bags.` Claim: `Suggestion: Use 4 cups.` — **ACCEPTED**.
- Source: `The campaign reached 4 thousand.` Claim: `Suggestion: Reach 4.` — **ACCEPTED**.

This violates the approved requirement to preserve quantity units and semantic meanings. Similar unrecognized units/scales must not collapse to a bare-number match. Add deterministic negative regressions and re-run the affected validation and AI suites after remediation.

## Acceptance criteria assessment

1. **Complete numeric-token matching — PARTIAL / FAIL.** The confirmed `24–40` and `24–40%` to `4%` baselines are rejected, as are the added larger-token, percentage, price, and recognized-count-unit cases. But unrecognized quantity units and scale modifiers can still match as shown above.
2. **Valid normalization — PASS.** Existing comma-grouping (`EGP 1,000`/`EGP 1000`) and metric mass equivalence regressions pass; non-equivalent tested values/units are rejected.
3. **Recommendation vs result — PASS for the requested contract.** `Suggestion: Track engagement, saves, and conversion rate after launch.` and the equivalent clearly labeled recommendation pass. Quantified/factual campaign results remain subject to numeric/high-risk checks; `The campaign achieved a 4% conversion rate.` with a later unknown section is rejected. A separate unknown/negated statement does not mask the positive result claim.
4. **Claim-local interpretation — PASS for tested cases.** Later unknown heading and unrelated negation do not change the factual claim result; standalone unknown-location behavior remains accepted.
5. **Citation punctuation and association — PASS for tested cases.** Both immediate citation placements pass. The citation after punctuation cannot support the following statement. Unknown/foreign IDs, unbracketed tokens, missing citation, and misassociated evidence remain rejected in the deterministic tests.
6. **Place and commercial claim safety — PASS.** Unsupported Maadi café/branch/location claims, prices, and certification claims remain rejected; the supported-location test passes.
7. **DEV-036 privacy diagnostics — PASS.** The filtered DEV-036 diagnostic suite passes, including sensitive identifier redaction and bounded diagnostics. Campaign tests also confirm the rejected prose, source ID, and project identifier do not leak through sanitized citation diagnostics.
8. **Persistence and isolation — PASS in the requested regressions.** Campaign AI tests pass for project-scoped reads/citations, foreign-project isolation, validate-before-write/no-overwrite behavior, malformed/incomplete batch preservation, approved deliverable replay, and same-turn idempotency.
9. **Deterministic only — PASS.** Test suites use local fixtures/fake providers; this QA made **0 real model/provider calls** and did not initiate Strategy Brief generation.
10. **Scope — PASS.** No excluded application/data areas were changed in the inspected diff.

## Required remediation

Preserve adjacent quantity-unit and scale semantics for all numeric tokens (including unlisted units), add the negative regressions above, and obtain a new independent QA pass after the remediation diff is ready. The issue is within the approved DEV-037 scope. No independent review verdict is made by this QA execution; review remains outstanding.
