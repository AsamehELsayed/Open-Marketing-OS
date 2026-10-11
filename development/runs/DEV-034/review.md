# DEV-034 Independent Reviewer

**Verdict: BLOCK**

## Review scope

Reviewed `development/runs/DEV-034/plan.md`, the full application and test working-tree diff, the W1 handoff, and independent QA report. I also ran deterministic local probes through `validate_generated_items()` to reproduce QA findings B1 and B2. No application code, tests, or client data were modified by this reviewer. No real inference, paid service, network, or GitHub operation was used.

## Blocking findings

### B1 — Common unsupported business-location claims pass validation

`app/services/campaign_production.py:850` detects selected establishment/location formulations, but misses ordinary first-person business-location statements. I independently passed each of these through the full `validate_generated_items()` path with an empty request/profile/brief/evidence source set and placed the text under `AI suggestions`; all were accepted:

- “We are located in Maadi.”
- “We are based in Maadi.”
- “Find us in Maadi.”
- “Our address is Maadi, Cairo.”
- “We have a location in Maadi.”

This leaves a direct path for the unsupported location fabrication that DEV-034 is intended to prevent. The current regression cases cover visit/café/branch forms but not these common forms. Extend the location detector and deterministic tests so these claims fail before persistence, while a single-source-supported business location remains accepted.

### B2 — Business Brief is still accepted as independent factual evidence

`_source_texts()` at `app/services/campaign_production.py:707` appends `brief_md` to the same source list used by persisted-fact and business-place validation. I independently verified that a business-place claim supported only by a Business Brief passes `validate_generated_items()` with an empty profile, request, and project evidence.

This contradicts the approved plan’s rule that the Brief is a summary aid and cannot independently establish facts. The new positive test at `app/tests/test_dev033_campaign_validation.py:108` makes this gap explicit: it supplies the café location only via `brief_md` and expects acceptance. Keep the Brief in the generation context, but ensure factual validation requires the current request, selected profile, or scoped project evidence. Add a negative brief-only case and ground the positive location case in a profile value or scoped evidence.

## Other reviewed behavior

The diff otherwise follows the focused scope: it carries the campaign title into the provider payload, bounds implicit generation to one deliverable, avoids treating “no ad spend” as an ad-copy request, makes the prompt categories explicit, and improves validation-failure messaging. The plan and QA report show deterministic fake-provider coverage for batch atomicity, replay, project scoping, and provider-boundary prompt capture. The Northstar Business Profile/Brief contamination remains an unresolved client-data issue; these prompt/validation changes do not establish that stored profile facts are accurate, and those client records must remain unchanged.

QA reports 88 targeted campaign tests and 18 campaign integration tests passing. Those results do not offset the two reproduced blocking acceptance failures. No separate full-suite rerun was performed by this reviewer.

## Release disposition

Do not approve, merge, or mark DEV-034 complete while B1 and B2 remain unresolved. After in-scope remediation, require a new independent QA pass and a separate independent re-review. No claim that real-model campaign generation is fixed is supported by this offline review. **Actual inference calls during this reviewer execution: 0.**
