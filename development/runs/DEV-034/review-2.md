VERDICT: APPROVE

# DEV-034 Independent Final Re-review

**Verdict: APPROVE**

**Execution lane:** General-lane fallback (`role=reviewer, agent=general`), separate from implementation, each remediation, and QA. The dedicated review/SOL lane is not triggered for this routine prompt and validation fix.

**Run:** DEV-034, branch `codex/campaign-generation-quality`
**Started:** 2026-10-11T19:03:48Z
**Completed:** 2026-10-11T19:07:23Z
**Provider/network:** Local code review and deterministic fake-provider tests only. No real inference, paid service, network, GitHub operation, or client-data change occurred.

## Reviewed evidence

Read the approved `plan.md`, `workers/w1.md`, all three remediation plans and execution reports, `qa.md`, `qa-2.md`, `qa-3.md`, PASS `qa-4.md`, and the prior blocking `review.md`. Inspected the complete final tracked diff and relevant generation, validation, and account-manager response paths. Independently ran the focused DEV-034 campaign and campaign-graph suites and checked diff hygiene.

The tracked diff is limited to the four planned files:

- `app/services/campaign_production.py`
- `app/graphs/account_manager_graph.py`
- `app/tests/test_dev032_campaign_ai.py`
- `app/tests/test_dev033_campaign_validation.py`

No Business Profile, Business Brief, project knowledge, Campaign record, schema, storage module, provider routing, installer, release, or application architecture files are changed. DEV-034 run artifacts are the only additional paths. The Northstar Maadi café association remains an unresolved client-data issue: the stored profile and derived Brief state it, whereas the raw note supports Maadi roasting and a weekly café tasting separately. This change does not independently verify or correct stored facts, and no client records were modified.

## Acceptance and safety review

- **Prompt and scope: PASS.** The system prompt defines SUPPORTED FACTS, UNKNOWN INFORMATION, and CREATIVE RECOMMENDATIONS; explicitly prohibits fabricating locations/branches/stores, prices, discounts, certifications, delivery promises, performance results, and testimonials; and warns against joining a process location to a separate café statement. The campaign title is carried into the provider payload. `no ad spend` no longer requests `ad_copy`, explicit ad creation still does, and an otherwise generic request resolves to the bounded `social_post` default.
- **Actual provider-boundary prompt: PASS.** The deterministic fake-router test captures the system/user strings at the provider interface and checks request, profile, brief context, title, exact requested types, and scoped evidence, including exclusion of other-project/private markers. No live provider is involved.
- **Unsupported place assertions: PASS.** The added validator runs over deliverable content including recommendations, and checks each detected complete place claim against one allowed source. Tests reject the requested unsupported location/branch/café variants and the Maadi roasting plus separate café-tasting inference. A direct profile-supported location passes.
- **Citations and project isolation: PASS.** Citation markup is stripped only from the candidate used for the business-place exact-source comparison. Existing citation-integrity checks remain in place and execute before that comparison. A properly cited location supported by scoped evidence passes; an other-project citation fails `scoped_citation_integrity`. Profile and evidence retrieval remain bound to the turn's project; missing project scope fails closed.
- **Business Brief: PASS.** The Brief remains provider prompt context, but `_source_texts()` no longer treats it as independent evidence for persisted facts, place assertions, or other source matching. Brief-only positive claims fail. The user-facing failure explanation names only the request, selected profile, and scoped retrieved evidence as factual validation sources.
- **Unknown wording: PASS.** A positive location claim combined with “location is unknown” is still rejected. A standalone unknown location remains accepted.
- **Other factual checks: PASS.** Unsupported certification remains rejected. Numeric/high-risk validation code and citation validators are unchanged in the reviewed diff. No rejected fact is filtered or silently rewritten.
- **Persistence and retry: PASS.** Validation still completes before `save_generated_batch`; storage implementation and schema are untouched. The deterministic suites cover whole-batch atomic rejection, preservation of existing records, same-turn replay before provider invocation, and no duplicate rows.
- **Failure UX: PASS.** Validation failure starts with the exact required headline and presents the claim excerpt, affected deliverable, validation reason, relevant source references, and corrective/retry step. It distinguishes a separately saved campaign record from unsaved content and avoids saying campaign content was successfully produced.

## Independent verification

```text
python -m pytest app/tests/test_dev032_campaign_ai.py app/tests/test_dev033_campaign_validation.py app/tests/test_automatic_tools_campaigns.py -q
PASS — 117 passed in 54.62s

git diff --check
PASS — exit 0
```

All test paths use local fixtures/fake providers. No real model behavior was exercised. The business-place validator is a wording heuristic and cannot establish whether a source itself is true; the existing Northstar profile discrepancy remains. Do not claim real-model campaign generation is fixed until it is separately verified with a real model under new authorization.

## Disposition

**APPROVE for the DEV-034 review gate.** No blocking findings remain in the reviewed diff. GitHub PR checks and merge conditions remain separate gates to be completed by the orchestrator. **Actual inference calls during this reviewer execution: 0.**
