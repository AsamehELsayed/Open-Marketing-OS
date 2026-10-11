# DEV-034 Independent Final QA (QA-3)

**Verdict: FAIL** — the second remediation fixed the previously reported mixed-positive/unknown bypass and corrected the validation-source wording. A new blocking regression remains: a business location supported by scoped project evidence is rejected when that evidence is correctly cited in the generated Retrieved evidence section. Do not merge until remediated and independently rechecked.

**Execution lane:** General-lane fallback (role=qa, agent=general). The dedicated QA lane was unavailable. This is a separate final QA execution from the implementer, both remediation executions, and prior QA/reviewer executions, consistent with docs/development-delegation-system.md §10.

**Run:** DEV-034, branch codex/campaign-generation-quality
**QA started:** 2026-10-11T18:48:36Z
**QA completed:** 2026-10-11T18:54:28Z
**Provider/network:** Deterministic local validation and fake-provider tests only. No real inference, paid services, network, or GitHub operations. No implementation or client-data files were edited by QA.

## Reviewed inputs

Inspected the approved plan.md, initial qa.md and review.md, both remediation plans and execution reports (remediation.md, remediation-execution.md, remediation-2.md, remediation-execution-2.md), qa-2.md, workers/w1.md, execution ledger, run status, and the full final application/test diff.

The tracked diff remains limited to the four planned files:

- app/services/campaign_production.py
- app/graphs/account_manager_graph.py
- app/tests/test_dev032_campaign_ai.py
- app/tests/test_dev033_campaign_validation.py

The only additional paths are DEV-034 run artifacts. No Northstar Business Profile, Business Brief, project knowledge, Campaign record, acceptance fixture, persistence schema, installer, or release file is modified.

## Blocking finding

### B5 — Properly cited evidence-supported location is rejected

The validator accepts an exact location statement supplied by the selected Business Profile. It also accepts a location statement copied into Persisted user facts from the scoped evidence source when no citation-bearing evidence statement is emitted.

However, a normally formatted and correctly cited location statement in the generated Retrieved evidence section fails validation. Direct deterministic probe:

- Scoped evidence source ID: doc-a:chunk-1
- Scoped evidence text: Northstar cafe is located in Cairo, Egypt.
- Retrieved evidence output: Northstar cafe is located in Cairo, Egypt [doc-a:chunk-1].
- Citation parsing and citation-integrity validation passed.
- _validate_business_place_claims() then rejected it with rule=business_place_source_match; diagnostic excerpt: Northstar cafe is located in Cairo, Egypt [doc-a:chunk-1].

The place validator compares the full fragment, including citation markup, against source text. The citation is valid, and the factual claim is directly supported, so this fails plan acceptance criterion 3. It can make the model's instructed, cited presentation of a supported business location fail generation. Remediate by comparing the factual text with citation syntax removed while retaining the existing citation-integrity check; add a deterministic positive test for a cited evidence-supported location. Do not relax any other source or citation rule.

## Direct deterministic checks

- **PASS:** Eight unsupported location/establishment forms rejected: “We are located in Maadi,” “We are based in Maadi,” “Find us in Maadi,” “Our address is Maadi, Cairo,” “We have a location in Maadi,” “Visit our Maadi branch,” “The Northstar cafe is located in Maadi,” and “We are opening a new cafe in Maadi.”
- **PASS:** “We are located in Maadi, but our location is unknown.” rejected with business_place_source_match.
- **PASS:** Standalone “Our business location is unknown.” accepted.
- **PASS:** Exact location supported by the selected Business Profile accepted.
- **FAIL:** A directly supported location with a valid scoped citation in the Retrieved evidence section is rejected (B5).
- **PASS:** Business Brief alone does not support either a persisted location fact (persisted_fact_source_match) or a location recommendation (business_place_source_match).
- **PASS:** The note “Beans are roasted in small batches in Maadi. The cafe hosts a weekly tasting flight.” does not support “Visit our Maadi cafe for a tasting.”
- **PASS:** Unsupported certification claim rejected before persistence by exact persisted-fact source matching. The main regression suite also retains and passes unsupported certification/high-risk claim coverage.

## Prompt, UX, and preservation review

- The provider-boundary fake-provider test passed. It captures the actual system and user strings and checks the explicit fact/unknown/suggestion categories, campaign title, request, selected profile/brief, requested deliverable types, scoped evidence, and absence of other-project/private marker content (Harbor, SECRET-BRIEF-XYZ).
- The validation-failure graph regression passed. It checks the exact required headline, unsupported claim, affected deliverable type/ordinal, validation reason, scoped source ID, next-step guidance, no Business Brief listed as a factual validation source, no deliverable save, and no misleading campaign-content success claim. A separately saved campaign shell is described as such.
- Existing batch atomicity and retry/replay regressions passed, including test_incomplete_or_duplicate_batch_preserves_existing_deliverables and test_saved_batch_replay_precedes_provider_error_or_invalid_output.
- Project isolation and fail-closed regressions passed, including test_generation_reads_only_the_turn_project_profile_brief_and_evidence and test_generation_without_a_turn_project_fails_closed.
- No weakening of citation integrity, numeric/high-risk checks, project-scoped retrieval, atomic persistence, or retry behavior was found in the diff.

The Northstar Business Profile/Brief discrepancy remains an unresolved client-data issue: the stored profile and derived Brief associate the café with Maadi, while the raw note separately records Maadi roasting and a weekly café tasting. This QA confirms no client records were modified. Prompt and code changes do not independently establish whether the stored profile statement is accurate.

## Commands and results

All tests used deterministic fake providers. No real model inference request was made.

    python -m pytest app/tests/test_dev032_campaign_ai.py app/tests/test_dev033_campaign_validation.py -q
    PASS — 97 passed in 29.65s

    python -m pytest app/tests/test_automatic_tools_campaigns.py -q
    PASS — 18 passed in 13.49s

    python -m compileall -q app/services/campaign_production.py app/graphs/account_manager_graph.py app/tests/test_dev032_campaign_ai.py app/tests/test_dev033_campaign_validation.py
    PASS — exit 0

    git diff --check
    PASS — exit 0

Additional direct probes are detailed above. git status showed only the four planned tracked code/test changes plus development/runs/DEV-034/ artifacts.

## Release disposition

**Do not approve or merge until B5 is remediated and independently re-reviewed.** This offline QA establishes deterministic validator behavior only; real-model campaign behavior remains unverified. **Actual inference calls during this QA execution: 0.**
