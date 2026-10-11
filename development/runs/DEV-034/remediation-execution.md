# DEV-034 Remediation Execution

**Role:** implementer (general-lane fallback)
**Scope:** remediate only independent QA/reviewer findings B1 and B2.
**Started:** 2026-10-11T18:30:35Z
**Completed:** 2026-10-11T18:36:52Z (UTC clock capture)
**Provider/network:** Deterministic local tests only. No real inference, paid service, network, or GitHub access.

## Changes

- `app/services/campaign_production.py`
  - Removed `brief_md` from factual validation source texts. The Business Brief remains in the generation context and provider payload as a summary aid. User request, selected Business Profile string fields, and project-scoped retrieved evidence remain the allowed source pool.
  - Extended business-place claim detection to catch first-person location/base statements, “find us in/at”, address/location statements, and “we have a location” claims. The validator still requires the complete claim fragment to be supported by one allowed source before it passes.
  - The existing process-location regression continues to reject an unsupported Maadi café claim when scoped evidence only says beans are roasted in Maadi and the café separately hosts tastings.
- `app/tests/test_dev033_campaign_validation.py`
  - Added deterministic rejection cases for all five reported forms: “We are located in Maadi,” “We are based in Maadi,” “Find us in Maadi,” “Our address is Maadi, Cairo,” and “We have a location in Maadi.”
  - Grounded the supported café-location acceptance case in the selected Business Profile.
  - Added separate brief-only rejection cases for a persisted fact and a location claim in suggestions. Updated prior supporting fixtures to use the Business Profile rather than the derived Brief.

No Business Profile, Brief, project knowledge, Campaign record, schema, storage path, provider routing, installer, or architecture was changed. Existing citation, exact fact matching, numeric, high-risk, project isolation, atomic persistence, idempotency, no-filter, and no-auto-retry code was not weakened or modified by this remediation.

## Verification

- `python -m pytest app/tests/test_dev033_campaign_validation.py -q` — **PASS**, 29 passed.
- `python -m pytest app/tests/test_dev032_campaign_ai.py app/tests/test_dev033_campaign_validation.py -q` — **PASS**, 95 passed in 27.36s. Includes deterministic fake-provider prompt capture and existing atomicity/retry/project-scope regressions.
- `python -m pytest app/tests/test_automatic_tools_campaigns.py -q` — **PASS**, 18 passed in 11.85s.
- `python -m compileall -q app/services/campaign_production.py app/tests/test_dev033_campaign_validation.py` — **PASS**, exit 0.
- `git diff --check` — **PASS**, exit 0.

## Limitations

This improves generation validation but does not independently verify or correct inaccurate stored client data. The previously observed Maadi café association in the Northstar Business Profile and derived Brief remains an unresolved client-data issue and was left untouched. Because a fact explicitly present in the selected profile is an allowed source under the approved policy, this change cannot determine that such stored profile text is false. Real-model behavior was not tested; no claim that real campaign generation is fixed is made. **Actual inference calls during this remediation: 0.**
