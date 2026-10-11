# DEV-034 Remediation 2

## Trigger

Post-remediation independent QA returned FAIL. The original B1/B2 findings are fixed. This follow-up addresses B3 and B4 only.

## Findings and fixes

### B3 — Unknown wording masks a positive location claim

**Evidence:** QA passed “We are located in Maadi, but our location is unknown.” through full validation with no allowed sources; it was accepted because the validator skips an entire text fragment whenever any unknown/negation term occurs.

**Fix:** Treat a location statement as absent/unknown only when the whole claim fragment is an explicit absence statement, never when a fragment also contains a positive location assertion. Add a deterministic rejection regression for the mixed positive/unknown sentence and a passing regression for a standalone “Our business location is unknown.” statement.

### B4 — Failure UX cites the Brief as a checked factual source

**Evidence:** The failure report says the Business Brief was checked for persisted-fact support and lists it among other checked sources, while validation now excludes it.

**Fix:** Update the validation failure explanation to name only the user request, selected Business Profile, and selected project's retrieved evidence as factual validation sources. Keep the Brief in generation context as summary aid. Add/update deterministic UX assertions to ensure the Brief is not represented as a validation source.

## Scope and invariants

- Change only `app/services/campaign_production.py`, `app/graphs/account_manager_graph.py`, and focused campaign tests.
- Keep all supported-fact/source validation, citation checks, project isolation, numeric/high-risk checks, atomic persistence, idempotent replay, no-filter/no-retry behavior, and provider routing intact.
- Do not edit Northstar Business Profile, Brief, project knowledge, Campaign records, schema, storage path, installer, or architecture.
- Deterministic fake-provider tests only. No real inference, paid services, network, GitHub, or release actions in remediation.
- After the separate remediation execution, run a fresh independent QA execution, then a separate independent re-review. Do not merge until both approve.
