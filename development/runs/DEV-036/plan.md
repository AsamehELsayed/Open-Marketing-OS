# DEV-036 Plan: Citation diagnostic precision

**Classification:** MEDIUM
**Run:** `DEV-036`
**Plan status:** Founder plan approval recorded; implementation authorized.

## Goal

Add an additive, structured first-failure diagnostic for rejected citation-like output. The diagnostic must distinguish an unknown bracketed ID from a noncanonical source-like token, identify only the offending sanitized, bounded token, and preserve the existing citation acceptance and rejection decisions. Rejected completions remain unsaved; only bounded diagnostic metadata may be returned.

## Scope

- Add a diagnostic-producing form of the current citation check in `app/routes/business_workspace.py`; preserve `has_unsupported_citations(markdown, allowed_ids)` as a boolean-compatible interface if needed by existing callers.
- Surface the structured diagnostic from business-brief generation failures without changing the saved brief, revision, source snapshot, or success response contract.
- Thread the same diagnostic into campaign deliverable validation failures in `app/services/campaign_production.py`.
- Keep the existing scoped evidence IDs, project isolation, validate-before-write batch behavior, and atomic persistence path intact.
- Add deterministic regression coverage for both business briefs and campaign batches. Tests use only fake/stub providers and local test databases; no live provider or model calls.

## Non-goals

- No changes to which citation forms are accepted or rejected, prompt policy, source retrieval, evidence contents, or project scoping.
- No UI redesign, schema migration, new persistence of failed output, logging of completion text, or storage of rejected prose.
- No real model/provider calls, Northstar data reads or writes, Real Estate AI work, DEV-034 restart, or PR #9 changes.
- No SOL strategic planning/review, Antigravity/browser work, or media generation; no architecture or strategic decision is in scope.

## Current behavior and invariants

`business_workspace.has_unsupported_citations` currently checks bracketed IDs left-to-right first: any colon-containing ID absent from the allowed set rejects immediately. It then scans source-like tokens and rejects the first noncanonical token, while exempting time-like tokens, URL spans, and an exact allowed ID occupying its canonical bracket span. Campaign production calls this shared boolean validator, validates a whole batch before `save_generated_batch`, and preserves existing rows on failure. Brief generation only updates the brief after validation and uses project/revision conditions for its write.

The implementation must preserve that validator order and every current boolean result. A diagnostic may describe the first rejection found by the existing algorithm; it must not reorder checks to select the earliest character offset. For new citation diagnostics, return only a small allowlisted shape such as a failure kind, token form, and sanitized/truncated offending token (plus bounded available IDs only if useful). Do not attach a claim excerpt or any rejected prose to a citation diagnostic. Ensure no full completion is persisted or included in the returned diagnostic/error. Keep existing plain-language error behavior compatible apart from the additive diagnostic field.

## Implementation packet

### W1 — Citation diagnostic implementation and deterministic regressions

**Role:** Implementer (`general` lane)
**Dependencies:** None
**Parallelism:** One worker owns all shared validator, consumer, and regression-test files. Run sequentially; do not claim real parallelism.
**Worktree:** Use the designated DEV-036 checkout directly. There is one sequential code worker and QA/reviewer are read-only, so no code writers overlap; this also keeps the uncommitted plan and graph available to W1.

**Exact packet/files:**

- `app/routes/business_workspace.py`
- `app/services/campaign_production.py`
- `app/tests/test_dev031_business_workspace.py`
- `app/tests/test_dev032_campaign_ai.py`
- `app/tests/test_dev033_campaign_validation.py`
- Handoff artifact only: `development/runs/DEV-036/workers/W1.md`

**Task:** Add a shared structured first-failure citation diagnostic without changing the validator’s accept/reject behavior. Report `unknown_bracketed_id` for the first unknown colon-containing bracketed ID under the current bracket scan, or `noncanonical_source_like_token` for the first source-like token rejected by the current second scan. Include only sanitized, bounded token metadata; never include rejected body text, a claim excerpt, or a full completion. Propagate the metadata through brief and campaign validation errors while retaining their existing failure statuses and no-write guarantees. Add/update offline tests for canonical allowed IDs, unknown bracketed IDs, bare/parenthesized/noncanonical source-like forms, current timestamp and URL exemptions, first-failure selection, prior brief preservation, existing campaign deliverable preservation, and absence of rejected completion text from returned/persisted diagnostic data. Do not sub-delegate.

**Acceptance criteria:**

1. Existing citation acceptance/rejection results are unchanged, including canonical bracket syntax and current timestamp/URL exceptions; campaign continues to use the shared validator.
2. A rejected brief and rejected campaign batch expose a structured diagnostic that identifies the failure kind and bounded offending token, and does not expose or persist rejected prose or the full completion.
3. Invalid brief output leaves the prior brief, sources, revision, and project data unchanged; invalid campaign output makes no write and leaves the existing scoped deliverable batch unchanged.
4. Deterministic regression tests cover both integrations and validator edge cases without real model/provider calls.
5. W1 handoff lists changed files, exact tests run, results, and any known limitations; it contains no rejected completion text.

## QA and reviewer gates

- **QA:** Start only after W1 completes and its handoff exists. Use the `qa` lane as an independent execution; if that specialized lane is unavailable, use a separate `general` execution under §10 of `docs/development-delegation-system.md` and disclose the substitution in both QA artifact and execution record. QA independently checks the diff, test outcomes, existing acceptance behavior, first-failure metadata, redaction/bounds, project isolation, and atomic/no-write behavior. Write `development/runs/DEV-036/qa.md` with PASS / PASS_WITH_CHANGES / FAIL and evidence.
- **Reviewer:** Start only after QA artifact and execution are complete. A separate `general` reviewer (not W1 or QA) inspects plan, diff, handoff, tests, and QA report. Write `development/runs/DEV-036/review.md` with concrete findings and `VERDICT: APPROVE`, `APPROVE_WITH_CHANGES`, or `BLOCK`. MEDIUM completion requires an independent APPROVE. Any remediation requires another real reviewer execution and a newer APPROVE artifact.
- **Gate order:** Founder plan approval → W1 → QA → reviewer. No gate is implied by this plan document alone.

## Test and verification plan

Run the targeted deterministic suite after implementation and independently during QA:

```powershell
python -m pytest -q app/tests/test_dev031_business_workspace.py app/tests/test_dev032_campaign_ai.py app/tests/test_dev033_campaign_validation.py
```

Add focused assertions in those files for unchanged boolean decisions across the existing canonical, unknown, noncanonical, time, and URL cases. At both integration layers, assert the diagnostic has only approved bounded metadata and that rejected completion marker text does not appear in the API error/diagnostic or stored rows. Assert the previous brief and deliverable rows plus their revisions/counts remain identical after rejection. Check project-scoped allowed IDs are still used; do not read or alter any Northstar project data. Tests must monkeypatch fake routers/providers and use temporary databases; verify there are zero real model/provider calls. QA also inspects the final diff for out-of-scope files and confirms no schema or persistence changes were introduced.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Refactoring the predicate changes acceptance/rejection behavior | Preserve the existing two-pass order, regexes, URL/time exemptions, canonical-span condition, and keep boolean wrapper parity tests. |
| Diagnostic leaks generated prose or a hostile/oversized token | Emit only an allowlisted failure kind and sanitized, length-limited token metadata; omit `claim_excerpt`; assert a unique rejected-output marker never appears in returned or persisted data. |
| Campaign and brief paths diverge | Both consume the same shared diagnostic function; cover each path with offline integration tests. |
| Rejection weakens project isolation or atomicity | Keep current scoped allowed-ID construction and validate-before-write/conditional-write paths unchanged; assert no-write preservation in both consumers. |
| Test setup accidentally reaches a real provider | Patch fake router/provider at the boundary; treat any real model/provider request as a failure and keep call counts at zero. |

## Approval-sensitive actions and completion

The requested MEDIUM plan approval authorizes the development team to implement, run deterministic QA/review, create a focused branch and pull request, wait for the repository-required CI checks, and merge only after the latest independent review is `APPROVE` and every required CI check passes on the merge candidate. Do not reopen, modify, or restart PR #9. No production publishing/deployment, ad spend, Northstar data change, or destructive action is in scope. Completion requires real planner, W1, independent QA, and independent reviewer executions; required CI success; the permitted merge; and the run's proof artifacts. Record parallelism honestly as `NO` because this plan has one implementation worker.
