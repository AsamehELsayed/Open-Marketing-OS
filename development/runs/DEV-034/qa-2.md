# DEV-034 Independent Post-Remediation QA

**Verdict: FAIL** — both findings from the first QA/review are fixed, and the requested verification commands pass. Two additional blockers remain: contradictory location wording can bypass location validation, and the failure explanation still says the Business Brief was checked even though validation no longer uses it as a factual source. Do not merge until both are addressed and independently rechecked.

**Execution lane:** General-lane fallback (`role=qa, agent=general`). The dedicated QA lane was unavailable. This is a separate post-remediation QA execution, following `docs/development-delegation-system.md` §10.

**Run:** DEV-034, branch `codex/campaign-generation-quality`
**QA started:** 2026-10-11T18:37:46Z
**QA completed:** 2026-10-11T18:42:58Z
**Provider/network:** Deterministic local validation and fake-provider tests only. No real inference, paid service, network, or GitHub operation. No implementation edits were made by QA.

## Remediation verification

Both original blocking findings are fixed in the current diff:

- The five exact previously accepted phrases — “We are located in Maadi,” “We are based in Maadi,” “Find us in Maadi,” “Our address is Maadi, Cairo,” and “We have a location in Maadi” — now fail with `business_place_source_match` when no source supports them, even under AI suggestions.
- A location stated in the selected Business Profile passes. A separate deterministic probe also accepted a location stated in scoped project evidence with a valid citation.
- A Business Brief-only persisted fact fails with `persisted_fact_source_match`; a Business Brief-only location suggestion fails with `business_place_source_match`.
- The source note “Beans are roasted in small batches in Maadi. The café hosts a weekly tasting flight.” does not support “Visit our Maadi café for a tasting”; it fails with `business_place_source_match`.

These probes exercised `validate_generated_items()` directly with local deterministic inputs. No provider was invoked.

## Blocking findings

### B3 — Contradictory location fragment bypasses validation

At `app/services/campaign_production.py:902`, `_validate_business_place_claims()` skips the entire fragment when `_LOCATION_NEGATION_RE` finds any negation/unknown term. A direct probe with empty request, profile, brief, and evidence accepted this unsupported positive assertion:

```text
We are located in Maadi, but our location is unknown.
```

The fragment contains both a concrete location claim and an “unknown” phrase, so the broad skip permits the positive claim through. It must reject unsupported positive establishment/location assertions even if another clause in the same fragment says the location is unknown. Keep ordinary absence statements such as “Our business location is unknown” valid.

### B4 — Failure UX names an unconsulted source

`app/graphs/account_manager_graph.py:574` says a persisted fact was not stated in the request, selected Business Profile **or Brief**, or project evidence. Lines 605–606 also list the Business Brief as an “Other checked source.” After remediation, `_source_texts()` intentionally excludes `brief_md` from factual validation. These statements therefore inaccurately describe the validation inputs and can mislead users troubleshooting the failure. Keep the Brief available to the provider as summary context, but describe only the request, profile, and scoped evidence as factual validation sources.

## Safety coverage verified

The requested tests remain covered in passing suites:

- Fake-provider captures the exact provider-boundary system/user prompts and checks the three prompt categories, campaign title, request, selected profile/brief, scoped evidence, and bounded type set.
- Deterministic validation tests cover unsupported branches/cafés/locations, supported profile location, missing information, Business Brief-only rejection, unsupported certification/high-risk claims, citations, and persisted-fact rules.
- Campaign integration tests cover project-scoped retrieval and fail-closed behavior, atomic batch rejection with existing-record preservation, retry/idempotent replay before the provider, no-save validation failure UX, and truthful campaign-shell reporting.
- The Northstar Business Profile and Brief contamination remains an unresolved client-data issue: the profile/derived brief assert a Maadi café, while the raw note separately says beans are roasted in Maadi and the café hosts weekly tastings. The stored client data must remain unchanged; the prompt/validation changes do not establish whether that profile assertion is accurate.

Changed tracked files are limited to `app/services/campaign_production.py`, `app/graphs/account_manager_graph.py`, and the two focused campaign test files. No Business Profile, Brief, project knowledge, Campaign record, schema, installer, or client-data file appears in the tracked diff. DEV-034 proof artifacts are also untracked as expected.

## Commands and results

- `python -m pytest app/tests/test_dev032_campaign_ai.py app/tests/test_dev033_campaign_validation.py -q` — **PASS**, 95 passed in 28.79s.
- `python -m pytest app/tests/test_automatic_tools_campaigns.py -q` — **PASS**, 18 passed in 13.31s.
- `python -m compileall -q app/services/campaign_production.py app/graphs/account_manager_graph.py app/tests/test_dev032_campaign_ai.py app/tests/test_dev033_campaign_validation.py` — **PASS**, exit 0.
- `git diff --check` — **PASS**, exit 0.
- Deterministic direct location/brief-source probes — expected original rejection/acceptance outcomes **PASS**; evidence-only supported-location probe **PASS**.
- Additional contradictory-location probe — **FAIL**, unsupported positive claim was accepted (B3).

**Actual inference calls during this QA execution: 0.**
