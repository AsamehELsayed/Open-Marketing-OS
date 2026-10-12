# DEV-036 Independent Post-Remediation Review

**Role:** Independent reviewer (general lane)
**Source commit:** `b7284427acf947c16bf4c46591edcf1e1ae555a4`
**Started:** `2026-10-12 18:29:02 UTC`
**Completed:** `2026-10-12 18:30:27 UTC`
**Reviewed:** Current full source/test diff, `qa-remediation.md`, and the baseline citation validators at the source commit.

## Previous finding: resolved

The prior review found that email, GitHub `ghp_`, and Slack `xoxb-` identifiers were returned verbatim. I re-probed those formats against the shared detector; each now returns `[redacted]`. The remediation tests also assert that those raw identifiers and rejected-output markers are absent from both the brief API failure response and campaign failure metadata. The same diagnostic object flows through both surfaces, and neither path stores the rejected completion.

## Blocking finding: formatted phone and SSN-shaped identifiers still leak

The sanitizer in `app/routes/business_workspace.py` redacts contiguous runs of seven or more digits, but not common formatted personal identifiers. Direct detector probes returned these offending tokens verbatim:

- `[+1-202-555-0123:chunk-1]` → `+1-202-555-0123:chunk-1`
- `[202-555-0123:chunk-1]` → `202-555-0123:chunk-1`
- `[123-45-6789:chunk-1]` → `123-45-6789:chunk-1`

These are phone-number and SSN-shaped values. The brief error response and campaign failure diagnostic expose the shared detector's token unchanged, so the same identifier forms can reach those callers. The current integration regressions cover the previously found email/GitHub/Slack formats but not these formatted personal identifiers. This leaves the approved privacy requirement unmet.

**Required remediation:** redact formatted phone and SSN-shaped identifiers (and add deterministic detector, brief-response, and campaign-failure assertions proving the raw values are absent). Keep the allowlisted diagnostic shape and 96-character cap; then repeat independent QA and review.

## Other checks

- **Behavioral parity:** Compared the detector with the baseline at `b7284427acf947c16bf4c46591edcf1e1ae555a4`. The bracket-ID-first scan, source-like-token second scan, regexes, URL/time exemptions, and exact canonical-span exemption are unchanged. `has_unsupported_citations()` remains boolean-compatible and delegates to the shared detector. The added golden-copy tests check representative parity cases.
- **First-failure precision:** The diagnostic reports `unknown_bracketed_id` with `bracketed_id`, or `noncanonical_source_like_token` with `source_like_token`, in the same legacy scan order. Token output is bounded to 96 characters.
- **Rejected output and persistence:** Brief validation still precedes the conditional project/profile/brief revision update. Campaign validation still uses project-scoped `context["evidence_ids"]` and validates the whole batch before `save_generated_batch()`. The campaign citation diagnostic omits rejected prose, claim excerpts, available source IDs, and project hashes. Existing brief and campaign rows remain unchanged on citation rejection according to the regression assertions.
- **Providers and scope:** QA reports 128 targeted tests passed, one existing Starlette deprecation warning, and zero real model/provider calls. Test boundaries use fake routers/providers. The diff contains only the two planned implementation files and three planned regression files; no schema or persistence implementation changed. I made no model/provider calls and edited no code/tests.

## Decision

**VERDICT: BLOCK**
