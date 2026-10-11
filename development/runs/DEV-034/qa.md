# DEV-034 Independent QA

**Verdict: FAIL** — two blocking findings remain against the approved acceptance criteria. The implementation is not ready for reviewer approval or merge until they are remediated and independently rechecked.

**Execution lane:** General lane fallback (`role=qa, agent=general`). The dedicated QA lane was unavailable for this execution. This is a separate execution from the implementer and planner, consistent with `docs/development-delegation-system.md` §10.

**Run:** DEV-034, branch `codex/campaign-generation-quality`
**QA started:** 2026-10-11T18:23:11Z (dispatch timestamp)
**QA completed:** 2026-10-11T18:26:07Z
**Provider/network:** Deterministic local fixtures and fake provider only. No real model inference, paid service, network, or GitHub access. No implementation files or client data were edited.

## Blocking findings

### B1 — Common unsupported business-location wording passes validation

The approved plan requires unsupported business locations to be rejected in any generated section. The new `_BUSINESS_PLACE_CLAIM_RE` recognizes establishment nouns and several visit/store formulations, but misses ordinary first-person/location claims.

I ran the full `validate_generated_items()` path with an empty profile, brief, retrieved evidence, and source-ID set. The claim was placed under `AI suggestions`; it passed validation:

```text
We are located in Maadi.
```

Direct deterministic probes against `_validate_business_place_claims()` also showed the following unsupported formulations were accepted with no allowed sources:

```text
We are located in Maadi.
We are based in Maadi.
Find us in Maadi.
Our address is Maadi, Cairo.
We have a location in Maadi.
```

The existing new tests reject “Visit our Maadi café/branch” and accept the independent negative location case only because those phrases match the implemented pattern. This leaves a clear path for unsupported city/location claims in natural copy and does not meet acceptance criteria 1–2. Extend the deterministic cases to cover common business-location forms and verify they fail before storage.

### B2 — Business Brief remains an independent factual validation source

The approved plan says the Business Brief is a summary aid, not an independent source; validated facts must be supported by the current request, selected profile, or scoped project evidence. `_source_texts()` in `app/services/campaign_production.py` still appends `brief_md` and both persisted-fact and business-place validation use that combined list.

The test `test_explicitly_supported_cafe_location_is_accepted` supplies the location only through `brief_md` while profile and evidence are empty, then expects validation to accept it. This demonstrates a brief-only business-location claim is accepted even though it has no direct profile or verified project evidence. The generation prompt’s instruction to treat the brief as a summary aid does not enforce that rule at the validation boundary.

Adjust the source matching so a claim cannot become supported solely because it appears in the Brief, while preserving the requested profile/evidence/request sources. Add a negative brief-only test and keep the positive supported-location case grounded in a profile field or scoped evidence.

## Verified behavior that passed

- The provider-boundary fake-router test captures the actual system and user strings. It verifies the three prompt categories, user request, selected profile, Business Brief, scoped evidence/citation ID, campaign title, and requested deliverable types. Other-project marker `Harbor` and private brief marker `SECRET-BRIEF-XYZ` were absent.
- Type resolution tests pass for “no ad spend” (only bounded `social_post` default), a generic unsupported request (bounded `social_post` default), and explicit “Create an ad” (`ad_copy`).
- The prompt distinguishes facts, unknowns, and recommendations. Unsupported certification/high-risk checks remain covered by existing deterministic tests.
- Existing fake-storage tests pass for whole-batch validation before save, preserving existing rows on rejected content, and same-turn idempotent replay preserving user edits/status/version without a second provider or storage call.
- Project-scope integration tests verify only the current project’s profile/evidence is retrieved and serialized; missing project scope fails closed.
- Validation failure UX tests pass: exact requested headline, unsupported claim excerpt, deliverable, reason, scoped source ID, corrective next step, no saved deliverables, and no success claim for campaign content. Provider failure also no longer reports successful content production.
- The current Northstar acceptance workspace and existing campaign records were not changed. `git status` showed only the four planned application/test files and the DEV-034 run artifacts.
- The reported historical Maadi profile/brief contamination remains unresolved in client data. This code change does not independently establish that stored business facts are accurate.

## Commands and results

All suites used local fake providers; no inference requests were made.

```text
python -m pytest app/tests/test_dev032_campaign_ai.py app/tests/test_dev033_campaign_validation.py -q
PASS — 88 passed in 25.27s

python -m pytest app/tests/test_automatic_tools_campaigns.py -q
PASS — 18 passed in 12.10s

python -m compileall -q app/services/campaign_production.py app/graphs/account_manager_graph.py app/tests/test_dev032_campaign_ai.py app/tests/test_dev033_campaign_validation.py
PASS — exit 0

git diff --check
PASS — exit 0
```

The two additional direct deterministic validator probes for B1 confirmed the failure mode above. No remediation was performed by QA.

## Release disposition

**Do not approve or merge while B1 and B2 remain unresolved.** The real model behavior remains unverified; no claim that real campaign generation is fixed is supported by this offline QA run. **Actual inference calls during this QA execution: 0.**
