# DEV-036 Remediation Record

**Scope:** Address the privacy finding from independent QA and the initial independent review only.

## Findings

- QA and review independently reproduced that the citation diagnostic token sanitizer could return email-shaped identifiers and common GitHub/Slack credential-shaped tokens verbatim.
- The issue affects both the brief API validation failure and campaign `scoped_citation_integrity` diagnostic because both use the shared detector.

## Required changes

1. Keep the original validator predicates, two-pass order, and boolean wrappers unchanged.
2. Redact email-shaped values and common credential-shaped values before returning the diagnostic token; preserve the existing token length bound.
3. Add deterministic tests at the shared detector and surfaced brief/campaign failure boundaries proving redaction and absence of raw rejected content.
4. Keep all persistence, project scoping, provider, and Northstar data behavior unchanged.

## Verification plan

- Add regression tests first and demonstrate they fail against the current sanitizer.
- Implement only the shared diagnostic sanitization fix.
- Run targeted deterministic citation suites with no real model/provider inference.
- Repeat independent QA and independent review; require APPROVE before PR creation.

## Disposition

Pre-authorized, in-scope remediation following initial QA `PASS_WITH_CHANGES` and review `BLOCK`. No scope expansion, Northstar data updates, or model inference.

## Remediation execution result

**Execution:** separate remediation worker execution
**Started:** `2026-10-12 18:17:18 UTC`
**Completed:** `2026-10-12 18:23:07 UTC`
**Result:** Remediation complete; ready for independent re-QA and re-review.

The shared diagnostic sanitizer now redacts email-shaped values, GitHub token shapes (`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`, and `github_pat_`), Slack token shapes (`xoxb-`, `xoxa-`, `xoxp-`, `xoxr-`, and `xoxs-`), GitLab `glpat-`, and AWS `AKIA` access-key shapes. Existing sensitive-keyword and numeric checks remain. The diagnostic shape and 96-character bound are unchanged. Validator predicates, two-pass order, boolean wrappers, campaign validation/persistence paths, project scoping, and provider safeguards were not changed by this remediation.

### Files changed in this remediation

- `app/routes/business_workspace.py`
- `app/tests/test_dev031_business_workspace.py`
- `app/tests/test_dev032_campaign_ai.py`
- `development/runs/DEV-036/remediation.md`

### Failing-first evidence

- `python -m pytest -q --tb=short --basetemp=development/runs/DEV-036/pytest-remediation-red-direct app/tests/test_dev031_business_workspace.py -k redacts_email_and_credential_tokens` — **3 failed** (email, GitHub, and Slack raw tokens were returned).
- `python -m pytest -q --tb=short --basetemp=development/runs/DEV-036/pytest-remediation-red-campaign app/tests/test_dev032_campaign_ai.py -k scoped_citation_diagnostic_is_private_and_batch_is_not_written` — **1 failed** (campaign payload exposed the email token).
- `python -m pytest -q --tb=short app/tests/test_dev031_business_workspace.py -k generation_labels_scoped_evidence` — **1 failed** (brief API payload exposed the email token). This local TestClient run used the approved local-test elevation because Windows sandbox loopback socket-pair setup hangs before the test enters the endpoint.

### Targeted green verification

- `python -m pytest -q --tb=short --basetemp=development/runs/DEV-036/pytest-remediation-green-brief app/tests/test_dev031_business_workspace.py -k "citation_diagnostic or generation_labels_scoped_evidence"` — **13 passed, 4 deselected, 1 existing Starlette deprecation warning**. This checks detector parity/redaction and the brief failure payload for email, GitHub, and Slack shapes.
- `python -m pytest -q --tb=short --basetemp=development/runs/DEV-036/pytest-remediation-green-campaign app/tests/test_dev032_campaign_ai.py -k scoped_citation_diagnostic_is_private_and_batch_is_not_written` — **1 passed, 66 deselected**. It checks the campaign payload for all three shapes and verifies existing rows and the write-call count remain unchanged.
- `python -m pytest -q --tb=short --basetemp=development/runs/DEV-036/pytest-remediation-green-validation app/tests/test_dev033_campaign_validation.py -k scoped_citation_failure` — **1 passed, 43 deselected**.
- `git diff --check` — **clean**.

The API and campaign tests use fake routers/providers and local temporary databases; direct diagnostic tests call no provider. **Real model/provider calls: 0.** No Northstar data was read or changed. No commit or push was made. Independent re-QA and re-review remain required before PR creation.

## Follow-up remediation after second review

**Trigger:** The independent post-remediation reviewer confirmed the email and GitHub/Slack credential remediation, but blocked because formatted phone-number and SSN-shaped citation IDs still appeared in the bounded token field.

**In-scope fix:** Extend only shared diagnostic-token sanitization to redact common formatted phone and SSN shapes. Preserve citation decision predicates, scan order, the boolean API, diagnostic keys, and the 96-character cap. Add failing-first deterministic tests at the shared detector, business-brief error response, and campaign validation failure payload. Assert raw personal identifier values and rejected prose markers are absent, with no change to project scope or persistence behavior.

**Verification:** After the separate remediation execution, repeat the full targeted three-file suite through independent QA, then require a fresh independent review with `APPROVE` before PR creation. No Northstar data updates, source/test scope expansion, or real model/provider inference.

### Second remediation execution

**Execution:** separate second remediation worker execution
**Started:** `2026-10-12 18:31:19 UTC`
**Completed:** `2026-10-12 18:36:21 UTC`
**Result:** Complete; ready for independent re-QA and re-review.

Extended only `_safe_citation_token()` to redact any diagnostic token containing seven or more digit characters, including digits separated by `+`, spaces, periods, or hyphens. Existing sensitive-pattern redaction and the 96-character bound remain. This affects diagnostic display only; citation acceptance/rejection, scan order, boolean APIs, metadata shape, persistence, and project scoping are unchanged. The test cases use the review's international/domestic phone and SSN examples, and assert redaction in the shared detector, brief API response, and campaign failure payload.

#### Failing-first evidence

- `python -m pytest -q --tb=short --basetemp=development/runs/DEV-036/pytest-remediation2-red-direct app/tests/test_dev031_business_workspace.py -k redacts_sensitive_identifier_tokens` — **3 failed, 3 passed, 14 deselected**. The formatted international phone, domestic phone, and SSN returned as diagnostic tokens; email and GitHub/Slack shape cases passed after the first remediation.
- `python -m pytest -q --tb=short --basetemp=development/runs/DEV-036/pytest-remediation2-red-campaign app/tests/test_dev032_campaign_ai.py -k scoped_citation_diagnostic_is_private_and_batch_is_not_written` — **1 failed, 66 deselected** because the campaign failure payload exposed the formatted international phone token.
- `python -m pytest -q --tb=short app/tests/test_dev031_business_workspace.py -k generation_labels_scoped_evidence` — **1 failed, 19 deselected** because the brief API failure payload exposed the formatted international phone token. As in the first remediation, this local TestClient run used approved local-test elevation for Windows loopback socket-pair access.

#### Targeted green verification

- `python -m pytest -q --tb=short --basetemp=development/runs/DEV-036/pytest-remediation2-green-brief app/tests/test_dev031_business_workspace.py -k "citation_diagnostic or generation_labels_scoped_evidence"` — **16 passed, 4 deselected, 1 existing Starlette deprecation warning**. Covers detector behavior and brief payload redaction for email, GitHub, Slack, all three formatted phone/SSN examples, and the existing bound/parity cases.
- `python -m pytest -q --tb=short --basetemp=development/runs/DEV-036/pytest-remediation2-green-campaign app/tests/test_dev032_campaign_ai.py -k scoped_citation_diagnostic_is_private_and_batch_is_not_written` — **1 passed, 66 deselected**. Covers all six sensitive identifier shapes and unchanged campaign rows/write count.
- `python -m pytest -q --tb=short --basetemp=development/runs/DEV-036/pytest-remediation2-green-validation app/tests/test_dev033_campaign_validation.py -k scoped_citation_failure` — **1 passed, 43 deselected**.
- `git diff --check` — **clean**.

All surfaced tests use fake model/router providers and local test databases. **Real model/provider calls: 0.** No Northstar data was read or changed. No commit or push was made. Independent re-QA and re-review remain required before PR creation.

## CI sentinel allowlist remediation

**Trigger:** PR #10's required `Secret hygiene` job rejected the synthetic GitHub-token-shaped test value used by the new privacy regressions. The same run's Gitleaks scan reported no leaks; the repository-specific scanner flagged only the deliberate fixture in three test locations.

**In-scope fix:** Add the exact synthetic literal to the existing file-and-literal-scoped sentinel allowlist for the two test files that contain it. No scanner patterns, citation behavior, provider behavior, or production code changed.

**Verification:** `python scripts/package/secret_scan.py --root .` reports 14 allowlisted sentinels, zero real secret hits, and zero home-path hits. Focused deterministic citation/privacy tests pass (17 passed, 114 deselected, one existing Starlette/anyio deprecation warning). The independent QA must rerun the full targeted three-file suite and independently verify the bounded exception before re-review.

**Scope:** No real credentials, rejected model prose, Northstar data, or provider calls were used. The existing PR branch is updated only after independent QA and re-review approve this remediation.
