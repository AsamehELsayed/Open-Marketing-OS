# DEV-034 Remediation Execution 2

**Role:** implementer (general-lane fallback)
**Scope:** address only independent QA findings B3 and B4.
**Started:** 2026-10-11T18:44:18Z
**Completed:** 2026-10-11T18:47:51Z (UTC clock capture)
**Provider/network:** Deterministic local tests only. No real inference, paid service, network, or GitHub access.

## Changes

- `app/services/campaign_production.py`
  - Removed the broad location-negation bypass. Once text matches the business-place claim detector, a separate unknown/absence phrase in the same claim fragment can no longer skip source matching.
  - Standalone unknown wording such as “Our business location is unknown.” remains allowed because it does not match the positive business-place claim detector.
- `app/graphs/account_manager_graph.py`
  - Changed persisted-fact failure wording to list only the current user request, selected Business Profile, and project-scoped retrieved evidence.
  - Removed the Business Brief from the list of factual validation inputs. The Brief remains available to generation as a summary aid.
- `app/tests/test_dev033_campaign_validation.py`
  - Added deterministic regressions: the mixed positive/unknown location claim is rejected and a standalone unknown business location is accepted.
- `app/tests/test_dev032_campaign_ai.py`
  - Updated the validation-failure UX regression to require the accurate factual-source list and assert that the Business Brief is not named as a checked source.

The Northstar Business Profile/Brief discrepancy remains an unresolved client-data issue: the stored profile associates the café with Maadi, while the raw note separately states Maadi roasting and a weekly café tasting. The profile, Brief, project knowledge, Campaign records, schema, storage, and provider routing were not changed. Prompt/validation changes do not independently verify or correct inaccurate stored business facts. Existing citation, project-isolation, numeric/high-risk, atomic-persistence, and retry protections were not relaxed.

## Verification

- `python -m pytest app/tests/test_dev033_campaign_validation.py app/tests/test_dev032_campaign_ai.py -q` — **PASS**, 97 passed in 35.44s. Uses deterministic local fixtures/fake provider only.
- `python -m compileall -q app/services/campaign_production.py app/graphs/account_manager_graph.py app/tests/test_dev032_campaign_ai.py app/tests/test_dev033_campaign_validation.py` — **PASS**, exit 0.
- `git diff --check` — **PASS**, exit 0.

## Limitations

This execution verifies deterministic behavior and wording, not real-model behavior. The unverified Northstar profile assertion still exists in the client data and can be treated as profile-supplied context by the existing validation policy. No inference requests were made during this remediation; **actual inference calls: 0**.
