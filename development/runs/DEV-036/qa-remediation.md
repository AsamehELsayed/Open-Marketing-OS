# DEV-036 Independent Post-Remediation QA

**Role:** QA (`general` fallback execution; the specialized `qa` lane is unavailable in this runtime, disclosed per `docs/development-delegation-system.md` §10)
**Execution:** Separate post-remediation QA; no source or test files edited
**Source commit:** `b7284427acf947c16bf4c46591edcf1e1ae555a4`
**Started:** `2026-10-12 18:24:22 UTC`
**Completed:** `2026-10-12 18:28:26 UTC`
**Verdict:** **PASS**

## Scope

Reviewed the current full diff, `remediation.md`, the initial QA finding, W1’s handoff, and the baseline validator at the source commit. Independently inspected the parity and redaction assertions in all three targeted test files, the brief and campaign failure payloads, project-scoped allowed-ID flow, and validate-before-write behavior.

The current code diff remains limited to the approved files:

- `app/routes/business_workspace.py`
- `app/services/campaign_production.py`
- `app/tests/test_dev031_business_workspace.py`
- `app/tests/test_dev032_campaign_ai.py`
- `app/tests/test_dev033_campaign_validation.py`

No schema or persistence implementation changed. Run records and QA reports are confined to `development/runs/DEV-036/`.

## Findings and evidence

- **Parity and precedence:** The detector keeps the baseline’s bracket-ID pass, source-like-token pass, regexes, timestamp/URL exemptions, and exact canonical-span exemption. The boolean API still returns a boolean and delegates to the same detector. The golden-copy regression compares decisions and covers canonical allowed IDs, unknown IDs, source-like forms, timestamp/URL exemptions, and the original bracket-first failure precedence. Remediation changed only token sanitization and tests.
- **Reason codes and first failure:** The detector returns `unknown_bracketed_id` / `bracketed_id` for an unknown bracketed ID and `noncanonical_source_like_token` / `source_like_token` for an offending source-like token. Existing assertions check exact first-failure reason, form, and token for representative cases.
- **Privacy at the detector:** Direct checks and deterministic tests show email, GitHub `ghp_` / `github_pat_`, Slack `xoxb-` / `xoxp-`, GitLab `glpat-`, AWS `AKIA`, and existing keyword/long-numeric cases are emitted as `[redacted]`. The 96-character token cap and exact allowlisted metadata shape remain covered.
- **Privacy at brief and campaign surfaces:** The brief API regression iterates email, GitHub, and Slack examples and asserts the raw sensitive identifier and rejected completion marker are absent from response and saved brief. It compares the complete brief object before and after, preserving content and revision. Campaign validation iterates the same sensitive forms and asserts the raw IDs and completion markers are absent from the error/diagnostic; existing rows and storage-call count remain unchanged. The campaign diagnostic has only its allowlisted citation metadata, without claim excerpt, available IDs, or project hash.
- **Project isolation and persistence:** Campaign generation still passes the project context’s `evidence_ids` to validation; the existing cross-project rejection regression remains. Inspection confirms whole-batch validation precedes `save_generated_batch()`. The brief store update remains after citation validation and guarded by the existing project/profile/brief revision condition.
- **Provider safeguards:** The brief integration uses a fake router, campaign tests use a fake provider, and the direct probes call only the local detector. **Real model/provider calls: 0.** No Northstar data was read or changed.
- **Diff hygiene:** `git diff --check` returned clean. No out-of-scope source/test files or unrelated changes were found.

## Independent test run

Exact command:

```text
python -m pytest -q --tb=short --basetemp=development/runs/DEV-036/pytest-qa-remediation app/tests/test_dev031_business_workspace.py app/tests/test_dev032_campaign_ai.py app/tests/test_dev033_campaign_validation.py
```

**Result:** `128 passed, 1 warning in 81.09s (0:01:21)`.

The warning is the existing Starlette `DeprecationWarning` at `starlette/testclient.py:53`: `anyio.abc.BlockingPortal` is deprecated in favor of `anyio.from_thread.BlockingPortal`. The first sandboxed attempt stalled during Windows TestClient local socket-pair setup and was interrupted without results. The exact command then passed using local-only escalation for the local TestClient socket access. The suite used temporary databases and fake routers/providers; no external network access occurred.

## Decision

The prior privacy finding is resolved at the detector, brief API, and campaign failure boundaries, with regression assertions for the exposed token forms. Behavioral parity, diagnostic bounds, no-write/preservation guarantees, project scoping, and the complete targeted suite pass. No blocking finding remains for this QA gate.
