# DEV-034 Remediation Execution 3

**Role:** implementer (general-lane fallback)
**Scope:** address only independent QA-3 finding B5.
**Started:** 2026-10-11T18:55:41Z
**Completed:** 2026-10-11T18:59:00Z (UTC clock capture)
**Provider/network:** Deterministic local tests only. No real inference, paid service, network, GitHub access, or subdelegation.

## Changes

- `app/services/campaign_production.py`
  - Removed bracketed citation markup from the business-place candidate fragment only before exact-source normalization. The factual words must still match an allowed request, selected Business Profile, or project-scoped evidence source.
  - Left `_validate_evidence_claims()`, `_has_unsupported_citations()`, and all citation-ID integrity rules unchanged.
- `app/tests/test_dev033_campaign_validation.py`
  - Added a positive deterministic regression: a Cairo café-location statement in scoped evidence passes when output cites its allowed `doc-a:chunk-1` ID.
  - Added a negative deterministic regression: identical supported text with an `other-project:chunk-1` citation fails `scoped_citation_integrity`.

No client/Northstar data, Campaign records, schema, storage, provider routing, architecture, installer, or release files were changed. Existing validation, project isolation, atomic persistence, retry/replay, and privacy protections were not relaxed. The discrepancy between the stored Northstar Business Profile/Brief and raw project note remains an unresolved client-data issue; this code change does not verify or correct the stored facts.

## Verification

- `python -m pytest app/tests/test_dev033_campaign_validation.py -q` — **PASS**, 33 passed.
- `python -m pytest app/tests/test_dev032_campaign_ai.py app/tests/test_dev033_campaign_validation.py -q` — **PASS**, 99 passed in 35.75s.
- `python -m pytest app/tests/test_automatic_tools_campaigns.py -q` — **PASS**, 18 passed in 14.90s.
- `python -m compileall -q app/services/campaign_production.py app/tests/test_dev033_campaign_validation.py` — **PASS**, exit 0.
- `git diff --check` — **PASS**, exit 0.

All tests used deterministic local/fake-provider behavior. Actual inference calls during this remediation: **0**.

## Limitations

The tests establish the citation-normalization behavior and preservation of local regressions. They do not establish real-model campaign behavior. Fresh independent QA and reviewer re-review are still required; this execution did not commit, push, contact GitHub, or merge.
