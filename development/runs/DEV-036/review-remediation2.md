# DEV-036 Independent Re-review After Second Remediation

**Role:** Independent reviewer (general lane)
**Source commit:** `b7284427acf947c16bf4c46591edcf1e1ae555a4`
**Started:** `2026-10-12 18:42:17 UTC`
**Completed:** `2026-10-12 18:43:53 UTC`
**Reviewed:** Current full source/test diff, both remediation rounds recorded in `remediation.md`, both prior review findings, `qa-remediation2.md`, and the baseline citation validators at the source commit.

## Prior findings

1. **Email/GitHub/Slack leakage — resolved.** I directly checked the shared detector for email, GitHub `ghp_`, and Slack `xoxb-` examples; each is rejected as before and its diagnostic token is `[redacted]`. The latest brief API and campaign failure regressions iterate these examples and assert that neither the raw identifier nor rejected completion marker appears in the returned failure data. The saved brief and existing campaign rows remain unchanged.
2. **Formatted phone/SSN leakage — resolved.** The second remediation’s digit-count redaction masks the international phone, domestic phone, and SSN-shaped examples as `[redacted]`. The same examples are covered at the detector and both surfaced failure boundaries. I independently probed them through the shared detector.

## Seven-digit display redaction assessment

`_safe_citation_token()` redacts a token when it contains seven or more digit characters, regardless of punctuation. This runs only after the unchanged citation predicate selects a failure and only changes the diagnostic token field. It does not change the two scans, membership checks, URL/time/canonical exemptions, first-failure selection, return status, or boolean API result. I checked a canonical allowed ID `[doc-1234567:chunk-1]`: it remains accepted with no diagnostic. The same ID when unknown remains rejected, with its diagnostic token redacted.

This deliberately hides some non-sensitive numeric IDs from the diagnostic display. That is a precision tradeoff in the sanitized token field, not an acceptance/rejection or persistence change; the reason code and token form still identify the failure class. Given the privacy requirement for numeric personal identifiers and the boolean compatibility requirement, I find this display-only tradeoff acceptable and non-blocking.

## Review checks

- **Acceptance/rejection parity and order:** Compared the full detector with the baseline at `b7284427acf947c16bf4c46591edcf1e1ae555a4`. The bracket-ID pass and source-like-token pass preserve the original regexes, order, URL/time exemptions, and exact canonical-span exemption. `has_unsupported_citations()` remains a boolean wrapper around the shared detector. The golden-copy tests cover accepted canonical IDs, rejected unknown and noncanonical forms, exemptions, and bracket-first failure precedence.
- **Reason codes, first failure, and bounds:** Diagnostics use `unknown_bracketed_id` / `bracketed_id` and `noncanonical_source_like_token` / `source_like_token`; first-failure ordering matches the legacy predicate. The allowlisted shape remains `{reason_code, token_form, token}` and emitted tokens are bounded to 96 characters, with sensitive values redacted.
- **Brief and campaign failure surfaces:** The brief endpoint adds only the bounded diagnostic to its existing failure detail. Campaign validation uses the same detector and returns its citation-only diagnostic without excerpts, available source IDs, project hashes, or rejected prose. Regression tests cover all six identifier examples on both surfaces.
- **No rejected prose persistence:** Brief validation precedes its existing revision-guarded update. Campaign validation still validates the entire scoped batch before `save_generated_batch()`. Tests assert prior brief data/revision and campaign rows/write-call count remain unchanged and rejected-output markers are absent from returned and stored data.
- **Project isolation and provider safeguards:** Campaign continues to validate against project-scoped `context["evidence_ids"]`; the existing cross-project rejection coverage remains. The only integration test providers are fakes. QA independently reports 131 targeted tests passed, one existing Starlette deprecation warning, and zero real model/provider calls. I made no model/provider calls and did not access Northstar data.
- **Scope:** The source diff is limited to `app/routes/business_workspace.py`, `app/services/campaign_production.py`, and the three planned regression test files. No schema or persistence implementation changed. `git diff --check` is clean.

## Decision

**VERDICT: APPROVE**

VERDICT: APPROVE
