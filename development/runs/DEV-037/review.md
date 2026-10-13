# DEV-037 Independent Code Review

**Role:** Independent reviewer, separate general-lane execution after final QA. No source or test files were edited. Review used the repository `code-review-and-quality` skill and the approved DEV-037 plan.

**Checkout/base:** `development/worktrees/DEV-037-validator-contract-hardening`; `HEAD` is `68148c3184b74a129ded2b377599a930064be88a` with the DEV-037 working-tree changes.

**Scope reviewed:** The complete diff in `app/services/campaign_production.py` and `app/tests/test_dev033_campaign_validation.py`, plus the plan, W1 handoff, both remediation handoffs, prior QA FAIL reports, final QA PASS report, and the relevant validation/persistence regressions. The changed application scope matches the approved plan. No Northstar data, provider routing, acceptance harness, installer, or unrelated application files were changed.

## Required finding

### Required — percentage points collapse into percent and support an unrelated baseline

`_NUMERIC_UNIT_SUFFIX_RE` matches the word `percentage` in `percentage points`, and `_numeric_fact_key` normalizes that match to the same `percent` unit used for `%` and `percent`. Consequently, the numeric fact comparison discards the material distinction between a percentage-point change and a percentage rate.

I reproduced this through both the parser and the full generated-item validator:

```text
Source: Prior campaign improved click-through by 4 percentage points.
_numeric_fact_keys(source): {('4', 'percent')}
Claim: Suggestion: Use a 4% baseline.
Observed: ACCEPTED
```

The claim contains no high-risk term that triggers the separate statement-text check, so that check does not catch this unit collision. This is an unsupported baseline acceptance and violates the approved requirement to keep percentage and performance-result meanings distinct.

**Required correction:** Parse `percentage point` / `percentage points` as a distinct unit before the generic percent-word match, and preserve it in the numeric fact key. Add negative regressions in both directions (`4 percentage points` must not support `4%`, and `4%` must not support `4 percentage points`) plus a positive equivalence regression for `%` and `percent`. Re-run the focused validator and integration suites and obtain a fresh independent QA PASS before re-review.

## Review assessment

- **Correctness:** The reported `24–40`/`4%` collision, sign, arbitrary-unit, scale, citation punctuation, and statement-local handling have deterministic coverage and the final QA report records passing probes. The percentage-point collision above remains a blocker within the approved numeric contract.
- **Citation association:** The punctuation rewrite binds an immediately following canonical citation to the preceding statement before splitting. Existing allowlist checks remain in place. Tests cover both placements, a citation that cannot support the next statement, and invalid citation forms.
- **Factual protections and state:** The final QA report records passes for unsupported locations, prices, certifications, numeric performance claims, foreign-project citations, DEV-036 privacy diagnostics, project isolation, validate-before-write/batch preservation, approved-row replay, and same-turn idempotency. The implementation does not change persistence code.
- **Readability/architecture:** The changes stay in the existing validator and focused test file, add no dependencies, and keep the recommendation exemption tightly bounded to an unquantified, explicitly labeled future-tracking sentence.
- **Security/privacy:** The changed numeric diagnostics use the existing sanitized diagnostic builder. No secrets or external calls were introduced.
- **Performance:** Numeric parsing is bounded by the existing deliverable size limits; no material performance risk was found.
- **Verification claims:** `qa-final.md` reports 72 validator tests, 67 campaign AI tests, and 15 DEV-036 diagnostic tests passing (154 passed total, 5 deselected), plus 21 direct probes. I independently reproduced the blocking unit collision; those passing tests do not cover this distinction.

All review activity was deterministic and offline. Real AI/model/provider calls: **0**.

## Verdict

**VERDICT: APPROVE_WITH_CHANGES**

Do not open or merge the PR until the percentage-point distinction is fixed, the missing regressions pass, and independent QA and reviewer re-review are complete.
