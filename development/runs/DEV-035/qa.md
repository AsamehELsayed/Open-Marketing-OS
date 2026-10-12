# DEV-035 Independent QA

**Status:** PASS
**Role:** QA
**General-lane fallback:** `role=qa, agent=general` — no dedicated QA lane was exposed in this environment.
**Start:** 2026-10-12 14:32:47 UTC
**Completion:** 2026-10-12 14:46:22 UTC
**Base:** `3c84e33a81a1a3333a0b131503a3bbae9b8635f5`

## Review findings

The checkout HEAD is the requested public-main base. The tracked diff is limited to:

- `app/services/campaign_production.py`
- `app/tests/test_dev033_campaign_validation.py`

The parser change normalizes Markdown around a recognized field label before the existing value comparison. It preserves the value text, leaves unknown bold labels intact, and does not change semantic matching or source scoping. Citation checks, numeric/high-risk validation, batch persistence, and existing-deliverable handling are unchanged.

The focused tests assert that generated Markdown remains unchanged after acceptance. They cover all four requested formats, an invented business name, an unsupported location, unsupported price/certification claims, a supported name combined with an invented location, and an unknown bold label with a claim.

The DEV-035 plan records the deterministic pre-fix reproduction and its root cause. This independent QA run reviewed that record and the final diff; it did not execute the old parser from base separately.

## Commands and results

Interpreter used: `C:\Users\User\Documents\open-marketing-os\.venv\Scripts\python.exe`. Pytest's default temp location was not writable in the sandbox; successful reruns used a worktree-local `--basetemp`.

1. Initial first-suite attempt, without `--basetemp`:
   `python -m pytest app/tests/test_dev032_campaign_ai.py app/tests/test_dev033_campaign_validation.py -q`
   **69 passed, 40 errors.** All 40 errors were pytest setup failures because pytest could not create its numbered temp directory under the sandbox's default AppData temp path; they were not test assertion failures.

2. First suite rerun:
   `python -m pytest app/tests/test_dev032_campaign_ai.py app/tests/test_dev033_campaign_validation.py -q --basetemp=.pytest-tmp-dev035-qa`
   **109 passed in 151.50s.** This covers the focused DEV-033 validation tests and existing campaign AI regressions, including citation integrity, project scoping, numeric/high-risk rejection, batch atomicity, and preservation of existing deliverables.

3. Combined second suite:
   `python -m pytest app/tests/test_automatic_tools_campaigns.py app/tests/test_dev032_integration.py -vv --basetemp=.pytest-tmp-dev035-qa`
   **19 of 20 collected tests passed before interruption.** All 18 automatic-tools cases passed, as did `test_generation_persists_through_w1_and_is_visible_only_in_scoped_api`. The last test reached was `test_campaign_detail_projects_w2_intake_fields_without_exposing_workflow`; it remained blocked in its in-process HTTP `TestClient` section with no output for over 30 seconds. I interrupted it. No assertion failure was reported.

4. Automatic-tools module isolated:
   `python -m pytest app/tests/test_automatic_tools_campaigns.py -q --basetemp=.pytest-tmp-dev035-qa`
   **18 passed in 35.17s.**

5. Diff hygiene:
   `git diff --check 3c84e33a81a1a3333a0b131503a3bbae9b8635f5`
   **Passed; no whitespace errors.**

## Requested safety regressions

- Four supported field-label formats: **PASS**.
- Invented business name, unsupported location, price, or certification: **PASS** (rejected).
- Supported name plus invented location: **PASS** (rejected).
- Unknown Markdown label cannot hide claim text: **PASS**.
- Citation integrity, project isolation, numeric/high-risk checks: **PASS** in the completed campaign validation/AI suite.
- Batch atomicity and existing-deliverable preservation: **PASS** in the completed campaign AI suite.
- DEV-033 and DEV-034 regression coverage: **PASS** in the 109-test campaign validation/AI run.
- Real inference/provider calls: **0**. Tests used deterministic stubs; no provider or account settings were changed.

## Limitation

The final campaign-detail HTTP integration test did not complete in this environment. It is outside the changed parser path; the campaign generation/persistence integration case immediately before it passed. The interrupted test should not be represented as passing.
