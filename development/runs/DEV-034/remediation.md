# DEV-034 Remediation

## Trigger

Independent QA returned FAIL and the first independent review returned BLOCK. Founder approval authorizes remediation within the approved DEV-034 scope.

## Findings and fixes

### B1 — Common unsupported business locations bypass validation

**Evidence:** QA and reviewer reproduced unsupported claims such as “We are located in Maadi,” “We are based in Maadi,” “Find us in Maadi,” “Our address is Maadi, Cairo,” and “We have a location in Maadi” passing in AI suggestions with no allowed sources.

**Fix:** Extend the additive business-place validator to recognize common first-person business-location, address, and “find us” forms. Require the complete claim to match one allowed source. Add deterministic rejection coverage for these forms, preserve acceptance for an explicitly profile/evidence-supported business location, and ensure process-location statements such as beans roasted in Maadi do not imply a café location.

### B2 — Business Brief is treated as independent fact evidence

**Evidence:** QA and reviewer reproduced a location claim accepted when the only matching text was in `brief_md`. This contradicts the approved source policy.

**Fix:** Keep Business Brief in the provider prompt as a summary aid, but exclude `brief_md` from the factual validation source pool. Persisted-fact and business-place claims must match the current user request, selected Business Profile, or this project's retrieved evidence. Add a failing brief-only fact case and ground the positive supported-location test in a selected profile field or retrieved project evidence.

## Scope and invariants

- Change only the generation service and focused campaign validation tests unless a direct test dependency requires otherwise.
- Do not edit the Northstar Business Profile, Brief, project knowledge, Campaign records, persistence schema, storage path, or provider routing.
- Preserve exact fact/source matching, citations, project isolation, numeric/high-risk checks, atomic batch validation/persistence, idempotent replay, and no-filter/no-auto-retry behavior.
- Use deterministic fake providers only. No inference, paid services, network, GitHub, installer, or release operations in remediation.
- After the separate remediation execution, independent QA must rerun the focused and campaign graph suites. A separate independent reviewer must review the final diff and QA report and write a re-review verdict before approval.
