# DEV-036 Independent Review

**Role:** Independent reviewer (general lane)
**Source commit:** `b7284427acf947c16bf4c46591edcf1e1ae555a4`
**Reviewed:** Approved plan, W1 handoff, QA report, complete source/test diff, and the baseline validators at the source commit.

## Findings

### Blocking — sensitive identifiers and credentials are returned verbatim

The sanitizer in `app/routes/business_workspace.py:27-42` uses a short keyword/shape denylist. It does not recognize email addresses or common credential prefixes such as GitHub `ghp_` and Slack `xoxb-`. I independently called the shared diagnostic with bracketed unknown IDs and reproduced:

- `[alice@example.com:customer-1]` → token `alice@example.com:customer-1`
- `[ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef12:chunk-1]` → the credential token is returned verbatim
- `[xoxb-AAAA-BBBB-CCCC:chunk-1]` → the credential token is returned verbatim

These values can be included in brief API failures and campaign validation results because both consumers surface this same diagnostic. This violates the approved requirement not to expose sensitive identifiers or credentials. The current tests cover keyword-based redaction and rejected prose markers but do not cover these cases at the detector and surfaced-failure boundaries.

**Required remediation:** redact email and credential-shaped tokens (including these prefixes) before returning the diagnostic, keep the 96-character bound, and add deterministic tests for the shared diagnostic plus the brief and campaign failure payloads. Re-run independent QA and review after remediation.

## Verified areas

- **Behavioral parity and first-failure order:** Compared the new detector against the baseline at `b7284427acf947c16bf4c46591edcf1e1ae555a4`. It preserves the old bracket-ID-first pass, source-like-token second pass, regexes, URL and timestamp exemptions, and exact canonical-span exemption. The boolean API delegates to the diagnostic and returns whether one exists. The golden-copy tests exercise representative accepted/rejected inputs and assert the legacy decision; an unknown bracketed ID retains precedence over an earlier bare token.
- **Diagnostic precision:** The implementation reports `unknown_bracketed_id` / `bracketed_id` and `noncanonical_source_like_token` / `source_like_token`, and reports the first failure in the legacy validator's scan order. Token output is truncated to 96 characters.
- **Rejected prose and persistence:** The brief validates before its conditional profile/revision update and only adds the diagnostic to the generic failure response. Campaign validation passes the existing scoped allowed-ID set to the shared detector and fails before the existing batch save. The citation-only campaign payload omits excerpts, source lists, and project hashes. The regression tests compare the preexisting brief and deliverable rows and check that a rejected-output marker is absent from returned/stored data.
- **Isolation, atomicity, providers, and scope:** No project retrieval or save logic, schema, provider safeguard, or persistence path was changed. Campaign validation remains per-project and validate-before-write. The changed source/test files match the planned scope.
- **Verification evidence:** W1 reports 125 targeted tests passed and fake-only provider boundaries. QA independently reports the same 125 tests passed and zero real model/provider calls. The new tests assert one brief fake call and only the expected campaign fake call; I made no model/provider calls.

## Decision

**VERDICT: BLOCK**
