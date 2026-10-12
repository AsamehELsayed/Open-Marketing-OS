# DEV-036 Independent QA

**Role:** QA (`general` fallback execution; the specialized `qa` lane is unavailable in this runtime, substituted under `docs/development-delegation-system.md` §10)
**Source commit:** `b7284427acf947c16bf4c46591edcf1e1ae555a4`
**Verdict:** **PASS_WITH_CHANGES**

## Scope and review performed

Independently read `plan.md`, `workers/W1.md`, the complete source/test diff, and the baseline validator at the source commit. Reviewed the assertions in all three changed regression files and the existing campaign project-scope and batch-persistence paths. This is a separate QA execution from W1. No implementation or test files were edited.

## Findings

### Required before approval — sensitive token redaction is incomplete

`app/routes/business_workspace.py:27-43` redacts a narrow list of keywords, `sk-` values, and long digit runs, then returns every other token as-is (up to 96 characters). Direct calls to the diagnostic reproduced these outputs:

```text
[alice@example.com:customer-1] -> token "alice@example.com:customer-1"
[ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef12:chunk-1] -> token "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef12:chunk-1"
[xoxb-AAAA-BBBB-CCCC:chunk-1] -> token "xoxb-AAAA-BBBB-CCCC:chunk-1"
```

The first leaks an email identifier; the latter two are credential-shaped tokens. Add privacy-safe sanitization that does not depend only on the current substring list, and deterministic regressions proving these forms are redacted in both the shared diagnostic and any surfaced failure metadata. Keep the 96-character cap and allowlisted diagnostic keys. No rejected prose or full completion was observed in the implemented citation-failure payloads.

## Evidence that passed

- **Behavioral parity:** At the source commit, the old validator uses the same two passes, regexes, time/URL exemptions, and exact canonical-span rule now used by `diagnose_unsupported_citations()`. The boolean API delegates to the diagnostic and returns `is not None`. The added parameterized regression compares the new boolean result to a test-local golden copy of the old predicate across canonical, unknown, noncanonical, timestamp/URL, and first-failure cases. Bracketed unknown IDs still take precedence over source-like tokens as in the old scan order.
- **Diagnostic precision:** The implementation emits `unknown_bracketed_id` and `noncanonical_source_like_token`, plus `bracketed_id` or `source_like_token`. Tests assert first-failure selection, exact reason/form/token for representative cases, and a token length no greater than 96 characters.
- **Brief preservation and status:** The brief integration test checks the 503 `invalid_model_output` failure and retained fake provider/model fields, asserts the rejected-output marker is absent from the response, and compares the complete saved brief object before and after rejection, including revision and saved content. In source, persistence remains after citation validation and remains conditional on the project/profile/brief revisions.
- **Campaign isolation and no-write behavior:** Campaign validation receives the existing scoped `context["evidence_ids"]`; the cross-project citation rejection regression remains present. The new batch test checks `output_validation`, `preserved_existing`, absence of the rejected marker and sensitive source string from the returned diagnostic/error, and exact equality of existing rows plus unchanged storage-call count. Inspection confirms validation occurs before `save_generated_batch()`.
- **Diagnostic shape:** Campaign citation failures use the citation-only metadata builder. Its shape omits claim excerpts, available source IDs, project-scope hashes, and rejected output. No schema or persistence code changed.
- **Provider behavior / model calls:** The brief integration uses a fake router; the campaign tests use the test provider. My direct diagnostic probes invoked no provider. QA made **zero real model/provider calls** and touched no Northstar data.
- **Scope:** The source diff contains only the approved two implementation files and three regression-test files; W1’s separate handoff is under `development/runs/DEV-036/workers/W1.md`. No unrelated source or schema changes were found.

## Independent test run

Exact command (the in-checkout basetemp avoids the denied default temp path):

```text
python -m pytest -q app/tests/test_dev031_business_workspace.py app/tests/test_dev032_campaign_ai.py app/tests/test_dev033_campaign_validation.py --basetemp=development/runs/DEV-036/pytest-qa
```

**Result:** `125 passed, 1 warning in 81.74s (0:01:21)`.

The one warning is the existing Starlette `DeprecationWarning` at `starlette/testclient.py:53`: `anyio.abc.BlockingPortal` is deprecated; use `anyio.from_thread.BlockingPortal` instead. The sandboxed attempt stalled at local TestClient loopback; the same local-only deterministic command was rerun with the permitted local-test escalation and completed successfully. No external network or provider access was used.

## Decision

The functional parity, first-failure diagnostics, brief/campaign no-write guarantees, scoped IDs, and test suite pass. Do not approve the privacy requirement until the email/credential token leakage above is fixed and covered by deterministic tests. Re-run this QA after remediation; this report does not approve the current implementation for merge.
