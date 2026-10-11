# DEV-034 Independent QA-4

**Verdict: PASS**

**Execution lane:** Dedicated QA lane was unavailable. This independent QA used the general-lane fallback and is recorded as `role=qa, agent=general` under `docs/development-delegation-system.md` §10. It is separate from the implementer, all remediation executions, prior QA executions, and reviewers.

**Run:** DEV-034, branch `codex/campaign-generation-quality`
**Started:** 2026-10-11T18:59:59Z
**Completed:** 2026-10-11T19:02:35Z
**Provider/network:** Deterministic tests and local inspection only. No real inference, paid service, network, GitHub operation, or client-data change occurred.

## Reviewed scope

Reviewed the approved `plan.md`; W1 implementation handoff; all three remediation plans and execution reports; initial `qa.md`, `qa-2.md`, and `qa-3.md`; initial blocking `review.md`; current run status and execution ledger; and the final tracked diff. The diff is limited to the four planned files: `app/services/campaign_production.py`, `app/graphs/account_manager_graph.py`, `app/tests/test_dev032_campaign_ai.py`, and `app/tests/test_dev033_campaign_validation.py`. DEV-034 proof artifacts are the only additional untracked paths. No Business Profile, Business Brief, project knowledge, Campaign record, persistence schema, installer, or release file is changed.

## Prior findings and acceptance criteria

- **B1 — Unsupported business location/establishment forms: PASS.** The deterministic parameterized test rejects all eight prior forms in recommendations: “We are located in Maadi,” “We are based in Maadi,” “Find us in Maadi,” “Our address is Maadi, Cairo,” “We have a location in Maadi,” “Visit our Maadi branch,” “The Northstar café is located in Maadi,” and “We are opening a new café in Maadi.” Unsupported business-place claims are compared against one allowed source.
- **B2 — Brief-only facts: PASS.** `_source_texts()` uses only the current request, selected profile, and scoped project evidence. The Business Brief remains prompt context but cannot independently substantiate a persisted fact or location suggestion. Both brief-only negative tests pass.
- **B3 — Mixed positive/unknown statement: PASS.** “We are located in Maadi, but our location is unknown” is rejected. A standalone “Our business location is unknown” remains valid.
- **B4 — Validation failure UX: PASS.** The graph test verifies the exact required headline, unsupported claim, affected deliverable ordinal/type, plain-language reason, source references, correction/retry guidance, no deliverable save, and no false campaign-content success claim. The factual-source description does not list the Business Brief. A separately saved campaign record is described separately.
- **B5 — Cited supported location/citation integrity: PASS.** `test_properly_cited_scoped_evidence_location_is_accepted` accepts an exact Cairo café location from scoped evidence with its allowed `doc-a:chunk-1` citation. `test_matching_location_text_with_other_project_citation_is_rejected` rejects the same claim with `other-project:chunk-1` under `scoped_citation_integrity`. Citation validation itself was not altered; citation markup is removed only from the business-place exact-source comparison candidate.
- **Other acceptance checks: PASS.** A location directly stated in the selected Business Profile is accepted. The raw-note wording that says roasting is in Maadi and separately mentions a café tasting does not support a Maadi café location. Missing location, branch, price, discount, certification, delivery, results, or testimonial information can remain unknown. Unsupported “certified organic” and existing numeric/high-risk validation remain covered.
- **Prompt/provider boundary: PASS.** The graph integration test captures the system and user prompt strings at the deterministic fake-provider boundary. It checks the three explicit categories, request, selected profile and Brief context, title, requested deliverable types, scoped evidence, and absence of another project's `Harbor` marker and `SECRET-BRIEF-XYZ`. The prompt explicitly distinguishes supported facts, unknown information, and creative recommendations, and prohibits fabricating locations, branches/stores, prices, discounts, certifications, delivery promises, performance results, and testimonials.
- **Atomicity, retry, and project scope: PASS.** The focused suites retain regressions proving a failed batch saves no generated deliverables and preserves existing rows, same-turn replay happens before provider invocation without duplicates, retrieval uses only the turn's immutable project profile/evidence, and missing project scope fails closed. Prompt privacy markers remain excluded.

## Client-data limitation

The Northstar Business Profile and derived Brief still associate the café with Maadi, while the raw project note separately supports Maadi roasting and a weekly café tasting. This remains an unresolved client-data issue. No client records were modified. The prompt changes and source-match validator do not independently prove or correct an inaccurate statement already stored in the selected Business Profile.

## Commands and results

All test providers were deterministic/local fakes; no inference request was made.

```text
python -m pytest app/tests/test_dev032_campaign_ai.py app/tests/test_dev033_campaign_validation.py -q
PASS — 99 passed in 46.07s

python -m pytest app/tests/test_automatic_tools_campaigns.py -q
PASS — 18 passed in 19.07s

python -m compileall -q app/services/campaign_production.py app/graphs/account_manager_graph.py app/tests/test_dev032_campaign_ai.py app/tests/test_dev033_campaign_validation.py
PASS — exit 0

git diff --check
PASS — exit 0
```

**QA disposition: PASS.** No blocking findings remain against the reviewed DEV-034 plan and current diff. This offline deterministic QA does not verify real-model behavior; do not claim real campaign generation is fixed without separately authorized real-model verification. **Actual inference calls in this QA execution: 0.**
