# DEV-037 Plan: Targeted Campaign Validator Contract Hardening

**Run:** `DEV-037`
**Base:** `68148c3184b74a129ded2b377599a930064be88a`
**Classification:** MEDIUM
**Plan status:** Complete; founder approval not recorded. Implementation is gated on founder approval.

## Goal

Fix the three confirmed deterministic campaign-validation defects from the October 13 current-source audit: numeric source false matches, claim classification that lets adjacent unknown wording suppress or distort validation, and evidence citations placed immediately after statement punctuation. Preserve unsupported-fact protections, citation allowlisting, project isolation, privacy-safe diagnostics, validate-before-write behavior, atomic persistence, approved deliverables, and same-turn idempotency. Use deterministic synthetic outputs only; make zero real model/provider calls.

## Classification and decision gates

MEDIUM is retained from the run record and fits the delegation policy in `docs/development-delegation-system.md` §2: the fix changes multiple interconnected safety predicates in the campaign validator and requires multiple focused regression suites. A false accept could persist unsupported campaign claims, so independent QA and review are required. This is routine validator engineering, not a positioning, offer, pricing, market-entry, spend, or architecture decision; the SOL planning and review gates do not trigger.

Stage order: technical planner → founder plan approval (`scripts/dev_run.py approve-plan --id DEV-037`) → W1 → independent QA → independent reviewer. No implementation before the founder approval flag is recorded. Reviewer must be a separate execution from both W1 and QA. Any remediation requires a real re-review. Create a focused PR only after the latest review is `APPROVE`; merge only after all required CI checks pass on the merge candidate.

## Scope

W1 may edit only:

- `app/services/campaign_production.py`
- `app/tests/test_dev032_campaign_ai.py`
- `app/tests/test_dev033_campaign_validation.py`
- `development/runs/DEV-037/workers/W1.md` (handoff artifact)

Keep the implementation limited to numeric fact matching, statement/section-local claim interpretation, citation-to-evidence-statement association, and deterministic regressions. Reuse the existing safe quantity normalization only where its semantic meaning remains equivalent. Keep DEV-036 citation diagnostic fields and sanitization unchanged.

### Explicit non-goals

Do not modify Northstar client data, provider routing, installers, the acceptance harness, retrieval behavior, schemas, persistence code, or unrelated validation contracts. Do not initiate a Strategy Brief attempt, call a real model/provider, use media generation or browser access, or make a production/external change. No real parallelism is planned or claimed.

## Current-source findings and invariants

- `_validate_high_risk_claims` currently normalizes text and accepts a numeric claim when its normalized digits occur as a substring in the joined source text. Thus the digit `4` can be “supported” by a source number such as `24–40`; the comparison does not preserve token boundaries or the numeric claim's unit/meaning.
- The same high-risk predicate scans a flattened document with nearby-text negation checks. Claim and section boundaries must prevent a later “Unknown or unsupported information” heading, or unrelated negation elsewhere, from changing an earlier factual claim. A specifically identified, unquantified instruction to track future metrics is a recommendation; it must not create a broad exemption for AI suggestions or high-risk factual claims.
- The shared citation allowlist detector recognizes canonical allowed IDs in brackets regardless of adjacent punctuation. Campaign evidence validation splits at terminal punctuation before checking citations, so a citation immediately after the period can be detached from the statement it supports. Bind only an immediately adjacent post-punctuation citation to the preceding statement; it must not support a following statement.
- Existing campaign generation validates the complete batch before delegating persistence to `save_generated_batch`; failed validation leaves existing rows intact. Citation IDs come from the selected project's allowed evidence. Existing DEV-036 diagnostics are sanitized, bounded, and citation-only.

## W1 — Single sequential implementation packet

**Role:** Implementer (`general` lane), one real execution, no sub-delegation.
**Dependencies:** Founder approval must be recorded before launch.
**Parallelism:** One worker only; `max_concurrency: 1`, `can_run_parallel: false`; report `PARALLELISM: NO`.
**Workspace:** Designated DEV-037 checkout at the stated base. QA and review start only after W1 completes and do not overlap as code writers.

**Task:** Make the smallest justified validator/test patch. Compare complete numeric tokens with semantically relevant units and meanings so digits embedded in a larger number cannot support a claim. Preserve documented safe equivalences (including grouped-thousands formatting and metric mass conversions) while keeping percentages, prices, quantities, and reported performance values distinct. Allow a clearly labeled, unquantified future measurement recommendation such as “Suggestion: Track engagement, saves, and conversion rate after launch.” Continue rejecting unsupported factual outcomes, including “The campaign achieved a 4% conversion rate.” Keep negation and unknown handling local to the relevant claim/section; a later unknown-information heading must not alter the earlier claim. Accept an allowed canonical citation on either side of the sentence punctuation only when it immediately belongs to that same evidence statement. Preserve unknown/foreign/noncanonical/missing/misassociated citation rejection and all existing private sanitized diagnostics. Add regression tests in the assigned suites. Use fakes/synthetic output only and do not sub-delegate.

## Acceptance criteria

1. **Complete numeric-token matching:** A source containing `24–40` or `24–40%` cannot support generated `4%`; digits inside a larger number cannot match as an independent token. Unsupported baselines and reported outcomes remain rejected when similar digits appear elsewhere. Relevant units and meanings remain significant for percentages, prices, quantities, and performance results.
2. **Valid normalization retained:** Existing positive comma-grouping and metric mass-equivalence regressions continue to pass; non-equivalent units and values remain rejected.
3. **Recommendation vs result:** The clearly identified unquantified future-metric recommendation passes. Unsupported quantified performance claims and factual outcome claims still fail, including when they are in AI suggestions.
4. **Claim-local interpretation:** Standalone unknown information and claim-local negation retain their documented behavior. An unsupported factual claim followed by a later `Unknown or unsupported information` heading remains rejected. Negation or unknown wording in another statement/section cannot mask it.
5. **Citation punctuation and association:** Both `Roasted in Maadi [allowed-source-id].` and `Roasted in Maadi. [allowed-source-id]` pass when the citation immediately belongs to that statement. A citation cannot support an adjacent different statement. Unknown IDs, foreign-project IDs, unbracketed noncanonical source tokens, missing supporting citations, and misassociated citations remain rejected.
6. **Place and commercial claim safety:** Unsupported café/branch/location claims (including the Maadi café example) and other high-risk factual claims stay rejected; supported café location remains accepted only from selected-project evidence/profile/request as currently allowed.
7. **DEV-036 diagnostic preservation:** Rejected citation failures keep the exact sanitized diagnostic contract (reason code, token form, redaction/bounds); do not expose rejected output, evidence excerpts/IDs, or project identifiers beyond the existing contract.
8. **Persistence and isolation:** Rejected batches write nothing and preserve existing deliverables, including approved content. Citation matching uses only the selected project's allowed IDs. Existing same-turn replay returns the original rows without provider calls or duplicate writes. Atomic batch behavior remains intact.
9. **Deterministic only:** All new and existing regressions use local fixtures and fake/synthetic model outputs. Real model/provider calls: 0.
10. **Scope and completion:** No excluded files/data are changed. A focused PR is opened only after latest independent review is `APPROVE`; merge only after all required CI passes. No claim of completion until planner, worker, QA, reviewer, and required proof artifacts are real and gates pass.

## QA and reviewer gates

- **QA:** After W1 and its handoff, an independent `qa` execution inspects the diff and runs the targeted deterministic suite. It checks every acceptance criterion, including safety negatives and normalization positives, citation ownership, project scoping, diagnostic privacy, batch atomicity, approved-row preservation, idempotency, and zero real provider/model calls. Write `development/runs/DEV-037/qa.md` with `PASS`, `PASS_WITH_CHANGES`, or `FAIL` and evidence. If the `qa` lane is unavailable, only a separate `general` execution may substitute under §10 of the delegation policy; disclose this in both artifact and execution record.
- **Reviewer:** After QA completes, a different independent `general` reviewer reads this plan, W1 handoff, complete diff, regressions, and QA report. Write `development/runs/DEV-037/review.md` with concrete findings and `VERDICT: APPROVE`, `APPROVE_WITH_CHANGES`, or `BLOCK`. MEDIUM completion requires the latest real review to be `APPROVE`. Any fix after a non-approval requires an independent re-review with a newer `APPROVE` artifact.

## Test and verification plan

Run after implementation, then independently during QA:

```powershell
python -m pytest -q app/tests/test_dev031_business_workspace.py app/tests/test_dev032_campaign_ai.py app/tests/test_dev033_campaign_validation.py
```

Add focused deterministic tests for the fabricated `4%` baseline against `24–40`/`24–40%`, other larger-number token-boundary collisions, unit/meaning distinctions, valid and invalid normalization, unquantified proposed metrics, unsupported factual performance claims, a later unknown-information heading, both citation placements, citation misassociation across statements, unsupported café location, and the preserved DEV-036 sanitized diagnostic. Reuse or strengthen existing integration regressions for foreign-project citations, missing evidence citations, batch no-write/preservation, approved-row replay, and same-turn idempotency. QA must confirm unchanged `campaign_deliverables` persistence behavior and inspect the final diff for excluded scope. No tests have been run as part of this planning execution.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Tokenization fixes the `4` false accept but accidentally conflates `%`, currency, quantities, or results | Assert both positive and negative cases with explicit unit-bearing fixtures; retain only documented equivalent formatting/unit conversions. |
| A broad AI-suggestion exception lets factual performance claims through | Allow only the bounded, clearly identified unquantified future tracking recommendation; keep numeric and factual high-risk checks active for the same section. |
| Negation/unknown handling crosses a sentence or section boundary | Evaluate local claim fragments and section context; include a fabricated result followed by a separate unknown-information heading as a negative regression. |
| Citation after terminal punctuation is detached or lends support to a neighboring statement | Attach only an immediate post-punctuation allowed token to the preceding claim; test a following uncited statement and all invalid citation forms. |
| Validator change bypasses project isolation, diagnostics, or atomic persistence | Keep the existing scoped-ID source and persistence paths; assert foreign IDs, exact DEV-036 diagnostics, no writes, existing approved rows, and replay behavior. |

## Stage dependency record

```text
technical planner execution
  → founder plan approval (required gate; currently NOT recorded)
    → W1 implementation (single sequential worker)
      → independent QA
        → independent reviewer
          → focused PR after APPROVE
            → merge after all required CI passes
```

Planning writes this artifact only. Founder approval, not the plan itself, opens the implementation gate.
