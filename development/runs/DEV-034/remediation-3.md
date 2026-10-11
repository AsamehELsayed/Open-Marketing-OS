# DEV-034 Remediation 3

## Trigger

Independent QA-3 returned FAIL on B5 after the previous blockers were fixed.

## Finding and fix

### B5 — Validly cited, evidence-supported location is rejected

**Evidence:** QA used scoped evidence `Northstar cafe is located in Cairo, Egypt.` and output `Northstar cafe is located in Cairo, Egypt [doc-a:chunk-1].` Citation integrity passed, but `_validate_business_place_claims()` rejected the fact because the citation identifier remained in the exact-source comparison text.

**Fix:** Remove citation markup from the candidate text used only for business-place source matching, then require the remaining full factual claim to match one allowed request/profile/scoped evidence source. Keep `_validate_evidence_claims()` and citation-ID validation unchanged. Add a deterministic positive test for a correctly cited, project-scoped evidence location; also assert an invalid/other-project citation continues to fail citation integrity.

## Scope and invariants

- Change only `app/services/campaign_production.py` and focused campaign validation tests if needed.
- Preserve the source pool (request, selected profile, scoped evidence; Brief remains summary context only), existing citation integrity, exact source matching, project isolation, numeric/high-risk checks, atomic persistence, idempotent retry, no-filter/no-auto-retry, and user-facing diagnostics.
- Do not edit Northstar/client data, schema, storage, provider routing, architecture, installer, or release files.
- Deterministic fake/local tests only. No real inference, paid services, network, GitHub, or subdelegation.
- Follow with a fresh independent QA execution and separate independent reviewer re-review before approval or merge.
