# DEV-033 Mini-plan — Campaign generation validation diagnostics

## Goal
Resolve the October 11 AI Campaign Deliverables acceptance failure narrowly, preserve strict factual safety, and make the next rejection diagnosable. Base: public-main commit `a08acb0d14c9267290afc797ca41ac7e757d4fd8`.

## Evidence and current diagnosis
- Acceptance artifacts record Strategy deliverable 1 failing the persisted-fact exact-value rule; no raw rejected draft was retained.
- Source rule is `app/services/campaign_production.py::_validate_persisted_facts`, which requires each normalized claim to be a substring of joined, project-scoped allowed sources.
- This identifies the failed rule, but not whether the model invented a claim or the matcher rejected an equivalent paraphrase. Root cause is currently UNDETERMINED.

## Scope
- Reproduce exact-match, semantic paraphrase, safe numeric-format/unit equivalence, unsupported business claim, invented commercial/high-risk claims, and supported non-verbatim project evidence without provider calls.
- Add safe, actionable validation diagnostics and bounded project-scoped acceptance evidence for a future rejection.
- Change matching only for equivalence that deterministic evidence proves safe. Keep unsupported, numeric/commercial, citation, provenance, atomicity, isolation, idempotency, existing-save, and approval guards intact.
- No architecture or feature expansion; no provider call before focused regressions pass.

## Verification and stop conditions
- Run the requested focused campaign tests and adjacent persistence/isolation/approval regressions.
- Permit at most one real OpenRouter campaign generation call, only after rechecking model price, endpoint routing, ZDR/retention, and paid-route exclusion; total cap 8, prior count 2, this attempt max 1, cost $0.00. Stop before inference if any check is not verified.
- Inspect actual persisted deliverables and retain only bounded sanitized, project-scoped rejection evidence.
- Push a focused branch and open a PR only if a product defect is confirmed and fixed. Merge only after required CI passes; otherwise report the blocker honestly.
