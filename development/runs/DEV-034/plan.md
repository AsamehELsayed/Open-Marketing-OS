# DEV-034 — Improve Campaign Generation Quality

**Classification:** MEDIUM — focused multi-file generation, account-manager failure UX, and regression-test work. It does not change provider, storage, campaign, or validation architecture.

**Checkout:** `codex/campaign-generation-quality`, based on the public-main checkout supplied for this run.
**Execution roles:** technical planner (this artifact), one implementer, independent QA, independent reviewer. No SOL planning/review gate applies; no provider or network calls are part of implementation or verification.

## Diagnosis and evidence

The read-only copied Northstar workspace in `development/acceptance/DEV-033-REAL-CAMPAIGN-2026-10-11/attempt-01/workspace/data/marketing.db` provides the following turn inputs. The turn’s stored request asks for a seven-day review draft for two coffees, four deliverable types (strategy, social posts, creative brief, content calendar), and says to use the profile and notes as source of truth, keep facts separate from recommendations, and avoid invented claims. It does not claim that the café is in Maadi.

| Input | Observed content and evidence |
|---|---|
| Business Profile | `description` says “a small Maadi café”; `location` is “Maadi, Cairo, Egypt”; `differentiators` mentions Maadi roasting and the café’s weekly tasting flight. These fields are already in the model’s allowed source set. |
| Business Brief | The generated brief repeats “small Maadi café” under Persisted user facts and repeats Maadi as Location. It cites project note chunks for other facts, but the Maadi café association is not supported by the cited raw source wording. |
| Retrieved project knowledge | `northstar-coffee-notes.md`, source `01cb9b37f0e7431c8b3e70a6dd424c15:c000`, says “Beans are roasted in small batches in Maadi” and then separately “The café hosts a weekly tasting flight.” It does not state that the café is located in Maadi. Other retrieved chunks cover audience and claim limits. All retrieved context must remain project scoped. |
| Campaign title | The saved campaign title is “Campaign: Northstar Coffee to introduce its two documented 250 g coffees to home brewers in Cairo.” `build_generation_context()` uses the title in its retrieval query, but does not return `campaign_title`; `build_generation_prompt()` therefore serializes an empty title. |
| Generation system prompt | `_SCHEMA_RULES` already requires four sections and says to copy persisted facts exactly and not paraphrase, combine, translate, or infer. It labels recommendations, but does not explicitly name the three requested buckets as SUPPORTED FACTS / UNKNOWN INFORMATION / CREATIVE RECOMMENDATIONS or explicitly prohibit invented locations, branches, and stores. It also calls any profile/brief text an allowed source, so an upstream bad profile statement can be repeated as if verified. |
| Requested generation scope | The stored request asks for four deliverable types, but persisted campaign intake records `ad_copy` as a fifth type. `_TYPE_ALIASES` maps bare `ad` to `ad_copy`, and `requested_types()` matches “no ad spend” as an ad-copy request. The historical call used 2,713 input tokens and produced 12,785 output tokens; a needless fifth type makes the batch longer. |

**Origin assessment:** There is confirmed source contamination before campaign generation: the Business Profile and its derived Brief contain the Maadi café claim, while the raw project note only supports Maadi roasting and a café tasting flight as separate facts. Therefore the campaign model was supplied that claim as profile/brief text; it was not necessarily invented during campaign generation. The original assistant turn records that deliverable 1 failed persisted-fact source matching and that no deliverables were saved, but neither the literal rejected sentence nor the raw provider prompt/output was retained. The exact failed sentence cannot be attributed conclusively. DEV-033’s new acceptance attempt was blocked in preflight and made zero inference requests; it is not evidence of a new model response. Do not claim the exact rejected claim is known or silently “fix” the Business Profile as part of this code change.

**Boundary:** Under the current source policy, any exact Maadi café statement in the selected Business Profile is treated as a supported profile fact even though the supplied note does not corroborate the relationship. Generation improvements can stop the model from combining separate source facts and can make validation failures actionable, but they cannot establish that an explicitly stored profile field is false. Keep the regression fixture’s profile free of a café-location assertion when testing the unsupported-combination case; surface the existing profile inconsistency as a data correction users can make.

## Goal

Reduce campaign generation’s tendency to turn missing or weakly related information into business facts. Make prompt source categories explicit, keep generation limited to the requested deliverable types, preserve all current validation and storage gates, and give an actionable failure message when a generated batch is rejected.

## Non-goals

- No application redesign, new agent framework, provider change, schema change, or new persistence path.
- No edits to the user’s stored Business Profile, Brief, project files, or existing saved Campaign Drafts.
- No automatic fact removal, output repair, second generation attempt, or persistence of a partially valid batch.
- No publishing, ad spend, live credentials, real model calls, or network access.
- No installer/release changes.

## Implementation decisions

1. **Prompt contract:** State three explicit categories: SUPPORTED FACTS (only text grounded in the selected profile or this project’s verified evidence), UNKNOWN INFORMATION (facts absent from those sources; leave them unspecified and do not fill gaps), and CREATIVE RECOMMENDATIONS (ideas explicitly labeled as suggestions). Keep the existing validated Markdown section schema or update its validator and tests in lockstep. Do not combine facts across separate clauses/fields to infer that a process location is a shop/café/branch location. Explicitly prohibit invented business locations, branches/stores, product prices, discounts, certifications, delivery promises, results, and testimonials. Preserve creativity in suggestions without presenting them as factual claims.
2. **Context and scope:** Carry the provided `campaign_title` into the returned generation context and the provider user payload. Preserve explicit requested types exactly. Fix the `ad spend` false positive so operational/negative references to ads do not request `ad_copy`. For an otherwise explicit campaign-content request with no deliverable type, use one existing type (`social_post`) as the bounded default; users can still request any supported type or explicit set. An explicit multi-type request remains one atomic batch, not multiple provider calls.
3. **Validation and failure result:** Extend, never relax, deterministic rejection coverage for business-location / branch / store / café assertions, including claims placed in recommendations. Continue to validate before persistence and return the original diagnostic. Do not strip rejected content and retry. Provide a clear validation-specific failure message and diagnostic details through the existing account-manager final answer.
4. **Failure UX:** For an output-validation failure, start with the exact headline: “Campaign content was generated, but it contains an unverified business claim. Nothing was saved.” Include the unsupported claim excerpt, affected deliverable ordinal/type, plain-language validation reason, relevant project source IDs (or that none were returned), and next steps: review/correct the Business Profile or add verified project evidence, then revise/remove the claim and retry. Keep the user informed if a campaign record was created, but never describe campaign content as successfully produced when no deliverables were saved. The chat renderer already displays the account-manager Markdown answer; no frontend redesign is needed.

## Implementation packet

### W1 — Prompt, scope, failure UX, deterministic regression tests

One implementer owns the following four files only:

- `app/services/campaign_production.py` — explicit prompt taxonomy and claim restrictions; preserve campaign title in context; narrow default type selection and fix `ad spend` parsing; strengthen location/branch claim rejection without changing any existing rule or source scope; retain diagnostic fields and fail-before-save behavior.
- `app/graphs/account_manager_graph.py` — render the validation-specific headline, affected deliverable/claim/reason/source references/next step; keep the campaign-shell versus generated-content outcome truthful.
- `app/tests/test_dev032_campaign_ai.py` — deterministic fake-router capture of the exact system/user strings passed to the provider interface; requested-type parsing and small default; atomicity, retry, and project retrieval integration regressions.
- `app/tests/test_dev033_campaign_validation.py` — focused location/branch/café, supported-location, certification, missing-information, and fact/suggestion classification cases.

Do not edit `app/services/campaign_deliverables.py`: its existing atomic save and idempotent replay contract is an invariant to test, not replace. Do not edit persistence schema, routes, or frontend files. This packet keeps all coordinated code changes within four files and one owner; no integration worker is needed.

### QA and review

After W1 completes, an independent QA execution reads the plan and diff and runs the focused backend regressions plus the required nearby campaign suites, using only deterministic fake providers. QA checks source scoping, prompt capture at the provider interface, rejected-claim UX, unchanged validation, atomic no-write behavior, and retry preservation; it writes `qa.md` with PASS / PASS_WITH_CHANGES / FAIL and evidence.

After QA, an independent reviewer execution inspects the diff, acceptance evidence, tests, and `qa.md` without reusing the implementer or QA execution. It writes `review.md` with APPROVE / APPROVE_WITH_CHANGES / BLOCK. Any remediation is a new execution and must receive an independent re-review before completion.

## Dependencies and parallelism

- W1 is the only implementation worker; its changes are deliberately grouped because prompt/context selection and UX all depend on the same generation result and diagnostic contract. No worker overlap is planned; record `PARALLELISM: NO` honestly and do not use the scheduler for a single worker.
- QA depends on W1 completion. Reviewer depends on QA artifact completion. Remediation, if needed, follows reviewer findings and is followed by a separate re-review.
- No nested delegation. No SOL planner or reviewer; this is a routine generation-quality correction and does not change positioning, offer, pricing, spend, or major brand direction.

## Acceptance criteria

All model-boundary tests use a deterministic fake router/provider. No tests use a real model, provider credentials, HTTP, or network.

1. **Unsupported business location:** With no explicit café/store location in the request/profile/evidence, a fake response that places “our Maadi café” in either Supported Facts or recommendations is rejected before persistence when the only evidence says roasting is in Maadi and a café separately hosts a tasting flight. The diagnostic identifies the offending claim and rule.
2. **Invented branch/café:** With no establishment or branch fact in the selected profile/evidence, generated claims such as “visit our Maadi branch” or a new physical café/store are rejected, including if put under recommendations.
3. **Supported location:** A claim restating an explicit business location from the selected profile or cited project evidence is accepted; the test distinguishes a business location from a roasting/process location.
4. **Unsupported certification:** “Certified organic” (and existing protected certification terms) remains rejected when absent from the allowed sources. Existing certification/high-risk behavior remains intact.
5. **Missing information:** If location, branch, price, discount, delivery terms, or results are absent, they are left out of business facts and identified as unknown where relevant; no plausible value is supplied.
6. **Facts versus suggestions:** The exact provider-captured prompt names and explains SUPPORTED FACTS, UNKNOWN INFORMATION, and CREATIVE RECOMMENDATIONS; generated format retains required validated sections. Creative copy remains allowed when labeled as a recommendation, while business facts in that section still receive factual validation.
7. **Atomic batch and retry:** A batch with any invalid item saves none of that batch, preserves pre-existing user-edited/approved deliverables byte-for-byte, and does not call storage with filtered content. A retry with the same turn/idempotency key returns the saved original rows before calling the fake provider, preserving content/version/status and producing no duplicate rows.
8. **Project-scoped retrieval:** A real local/fake retrieval fixture with two projects proves the prompt includes only the selected project’s retrieved text and citation IDs; missing `project_id` fails closed. Business Profile and Brief reads also use that same immutable project id.
9. **Actual prompt capture and type scope:** Capture the system and user strings at the fake provider boundary (not only the builder return). Assert the captured user JSON contains this turn’s request, selected profile, brief, scoped evidence, campaign title, and only the requested type(s). An operational phrase such as “no ad spend” does not add `ad_copy`; a generic content request uses only the bounded default; an explicit multi-type request produces exactly those types.
10. **Failure UX and success integrity:** A rejected generation final answer starts with the required sentence and includes the claim excerpt, deliverable ordinal/type, validation reason, relevant source IDs, and actionable correction/retry step. The answer says no deliverables were saved and does not claim successful campaign content production. If the campaign shell itself was saved, that fact is described separately and truthfully.

## Safety invariants to preserve

- Continue exact supported-fact source matching; no paraphrase/merge loophole and no silent accepted rewrite.
- Continue citation integrity against only IDs returned by this project’s `retrieve_scoped` result.
- Continue immutable `project_id` isolation for profile, brief, retrieval, campaign, and deliverables; no fallback to another project.
- Continue numeric and high-risk claim checks, including price, discount, certification, delivery promise, testimonial, and performance/results claims. New location checks may strengthen coverage only.
- Continue required section/type checks, reject missing/duplicate/unexpected types and malformed output, and enforce existing limits.
- Validate the complete batch before calling `save_generated_batch`; preserve its transactionality, ownership checks, deterministic idempotency key, same-turn replay precedence, and existing saved records.
- On provider or validation failure, do not save new deliverables, do not remove only the offending text, and do not automatically call the provider again.
- Keep provider selection and strict zero-cost route controls unchanged. No publishing or advertising is triggered.

## Verification and completion proof

Run the targeted DEV-034 tests, `app/tests/test_dev032_campaign_ai.py`, `app/tests/test_dev033_campaign_validation.py`, and the relevant campaign graph/integration regression modules. QA runs these independently and records exact commands/results. The reviewer independently verifies the final diff and QA artifact. No real inference request occurs; report `REAL MODEL CALLS: 0`.

The run may be marked COMPLETE only with real planner, implementer, QA, and reviewer executions, a latest independent review of APPROVE, all acceptance criteria above met, and no unresolved required lane. If remediation occurs, a separate re-review must also APPROVE.
