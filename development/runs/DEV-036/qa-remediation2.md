# DEV-036 Independent QA After Second Remediation

**Role:** QA (`general` fallback execution; specialized `qa` lane remains unavailable, disclosed under `docs/development-delegation-system.md` §10)
**Execution:** Separate independent QA; no source or test files edited
**Source commit:** `b7284427acf947c16bf4c46591edcf1e1ae555a4`
**Started:** `2026-10-12 18:36:58 UTC`
**Completed:** `2026-10-12 18:41:40 UTC`
**Verdict:** **PASS**

## Scope

Reviewed `remediation.md` (both remediation rounds), `review-remediation.md`, the current full source/test diff, prior QA findings, the baseline citation validator, and the deterministic regression assertions. Independently checked the sensitive-token detector outputs, integration error payloads, storage behavior, allowed-ID scoping, and provider boundaries.

The code diff remains limited to the approved files:

- `app/routes/business_workspace.py`
- `app/services/campaign_production.py`
- `app/tests/test_dev031_business_workspace.py`
- `app/tests/test_dev032_campaign_ai.py`
- `app/tests/test_dev033_campaign_validation.py`

No schema or persistence implementation changed. QA records are confined to `development/runs/DEV-036/`.

## Findings and evidence

- **Both rounds of privacy findings resolved:** At the shared detector, I checked email, GitHub, Slack, formatted phone, and SSN-shaped bracketed IDs. Each returns `token: "[redacted]"`; each remains rejected with `unknown_bracketed_id`. The boolean API returns `True` for the same inputs. The second remediation counts seven or more digit characters, including digits separated by punctuation or spaces, and preserves the 96-character cap.
- **Surfaced failure privacy:** The brief API and campaign batch tests iterate all six identifier examples (email, GitHub, Slack, international phone, domestic phone, SSN). They assert the raw identifier and rejected completion marker are absent from response/error/diagnostic data. The brief test compares the complete saved object before and after rejection; the campaign test verifies rows remain identical and storage-call count does not increase. No rejected completion text is persisted.
- **Behavioral parity and precedence:** Compared the current detector to baseline `b7284427acf947c16bf4c46591edcf1e1ae555a4`. The bracketed-ID-first pass, source-like-token second pass, regexes, URL/time exemptions, and canonical-span condition are unchanged. The existing boolean API delegates to the shared detector and returns a boolean. Golden-copy tests cover canonical allowed IDs, unknown IDs, noncanonical forms, time/URL exemptions, and bracket-first selection. Sanitization runs only after the legacy predicate has found a rejection.
- **Diagnostic precision:** The reason codes remain `unknown_bracketed_id` and `noncanonical_source_like_token`, with token forms `bracketed_id` and `source_like_token`. Tests assert the first-failure reason/form/token and the allowlisted metadata keys; bounded-token assertions remain in place.
- **Project isolation and atomic no-write:** Campaign validation continues to receive the project-scoped `context["evidence_ids"]`; the existing cross-project citation rejection test remains. Inspection confirms the whole generated batch is validated before `save_generated_batch()`. Brief persistence remains after validation and guarded by the prior project/profile/brief revision condition.
- **Provider safeguards:** Integration tests use a fake brief router and fake campaign provider; direct detector probes used no provider. **Real model/provider calls: 0.** No Northstar data was read or changed.
- **Diff hygiene:** `git diff --check` is clean. No unrelated source/test files or schema/persistence edits were found.

## Independent test run

Exact command:

```text
python -m pytest -q --tb=short --basetemp=development/runs/DEV-036/pytest-qa-remediation2 app/tests/test_dev031_business_workspace.py app/tests/test_dev032_campaign_ai.py app/tests/test_dev033_campaign_validation.py
```

**Result:** `131 passed, 1 warning in 79.67s (0:01:19)`.

The warning is the existing Starlette `DeprecationWarning` at `starlette/testclient.py:53`: `anyio.abc.BlockingPortal` is deprecated in favor of `anyio.from_thread.BlockingPortal`. The sandboxed attempt stalled in Windows TestClient local socket-pair setup and was interrupted without results; the exact command then passed with local-only escalation for loopback access. The suite uses local temporary databases and fake routers/providers; no external network access occurred.

## Decision

The prior email/GitHub/Slack finding and the second formatted-phone/SSN finding are resolved in the shared detector and both surfaced failure paths. Behavioral parity, first-failure reporting, 96-character bounds, privacy, project scoping, and no-write guarantees pass. No blocking finding remains for this QA gate.
