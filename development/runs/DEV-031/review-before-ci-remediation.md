# DEV-031 Phase A independent SOL re-review

VERDICT: APPROVE

**Execution:** Separate `gpt-6.1-sol` re-review after remediation and independent QA PASS. Applied the previously loaded `code-review-and-quality` skill and supplied operating contract. Implementation files were read-only; no delegation, provider call or implementation edit occurred.

**Started:** 2026-10-09 17:28:07 UTC
**Completed:** 2026-10-09 17:30:19 UTC
**Baseline:** public main `efbcb7582ca43ef35db70f462ee52a1d9136789b`

No required changes remain in the reviewed Phase A implementation. This approves the code review gate; required GitHub Actions and merge verification remain separate release gates.

## Prior findings

| Finding | Disposition and concrete evidence |
|---|---|
| F1 — unsupported citations | **CLOSED.** `app/routes/business_workspace.py:25-50` checks every bracketed colon reference against the exact scoped evidence set and rejects noncanonical source-like tokens. Generation calls it before persistence at lines 167-173. Re-executed the original production-route reproduction with zero evidence and `[invented.source:reference]`: now returns 503/`invalid_model_output`; the complete saved brief response, including revision and provenance, remains unchanged. Canonical `[doc-a:chunk-a]` is accepted when allowlisted. Updated backend regression covers unknown bracketed, bare, parenthesized and punctuated references. |
| F2 — optional fields lost at creation | **CLOSED.** `app/services/business_workspace.py:13-25` is the shared type/length/normalization boundary; updates call it at line 50 and creation at `app/routes/business_workspace.py:69-86`. Creation persists all supplied supported fields and validates before inserts. Re-executed the production route with all eight profile fields: every value round-trips. A non-text website returns 422 and leaves project count unchanged. |
| F3 — A→B→A stale actions | **CLOSED.** `frontend/src/routes/Workspace.tsx:58-66`, `:70-78`, and `:83-92` capture both project ID and selection epoch and guard success, error and finalization. `workspaceSelection.ts:1-8` requires both to match; the effect at Workspace lines 37-52 invalidates prior epochs. Inspected every handler and independently ran the selection regression: old A epoch is rejected after switching back to A. This check tests the helper plus handler source guards, not a mounted React/browser delay sequence. |
| F4 — concurrent lazy initialization | **CLOSED.** `app/services/business_workspace.py:32-38` now inserts with `INSERT OR IGNORE`, then reads the winning row. Repeated the original controlled interleaving: it returns the existing profile without UNIQUE failure and leaves one row. The reviewed synchronized two-reader regression additionally exercises real separate SQLite connections; latest independent QA reports it passed. |

The earlier QA findings also remain closed: v13's profile project key is explicitly `NOT NULL`; original unsupported citation forms remain rejected; regeneration confirmation accurately states immediate saved replacement and discarded unsaved editor edits.

## Four founder-approved UX requirements

1. **Name-only, short client creation:** explicit client mode keeps only business name and optional website visible; name-only creation remains valid and every other profile field can be added later. Existing first-run onboarding retains its prior API/navigation path. Evidence: `frontend/src/routes/CreateBusiness.tsx`, `app/routes/business_workspace.py:63-88`, recorded browser acceptance.
2. **Generation state, actual route and plain-language failures, with manual editing available:** Workspace shows running/generated/failed state and returned provider/model metadata; structured error handling covers missing credentials, unavailable model, authentication, timeout and generic provider failures. Failed generation leaves the saved brief intact and re-enables editing. Evidence: `Workspace.tsx:80-92`, `:118-119`; generation error handling in `app/routes/business_workspace.py:194-255`; latest QA and browser acceptance.
3. **Explicit confirmation before replacing an edited brief:** Workspace line 82 checks saved or draft content and uses the corrected confirmation. Backend lines 136-138 enforce saved-brief overwrite authorization and snapshot profile/brief revisions; lines 187-191 conditionally persist. The independently rerun confirmation assertion passed.
4. **Separate facts, evidence, suggestions and unknowns; supported citations; insufficient Knowledge:** the prompt and required section checks at backend lines 143-152 and 174-185 separate categories; the UI labels them at Workspace line 120 and displays sources at line 121. F1 closes the reported citation validation gap. Missing evidence is explicitly described as insufficient in the prompt/UI. These structural safeguards do not establish the semantic truth of every sentence a real model might produce.

## Five-axis review

| Axis | Assessment |
|---|---|
| Correctness | Prior failures resolved. Partial profiles, separate brief/profile revisions, unknown-project handling, conflicting body-ID rejection, generation validation and conditional persistence fit the approved contract. Recorded migration, restart and isolation evidence inspected. |
| Readability | Shared profile validation and dedicated citation/selection helpers give the new boundaries clear owners. New modules remain small; no required readability finding. |
| Architecture | Projects remain the client identity; the additive SQLite table owns profile/brief state. Existing scoped retrieval, ModelRouter, files, campaigns and navigation are reused. No replacement-style profile writes or new provider stack. |
| Security | Path IDs scope access, SQL values are parameterized, editable column names come from a fixed whitelist, creation is validated, untrusted generated citation IDs are allowlisted, and isolated clients skip root ingestion/import. Single-operator project isolation is not represented as multi-user authentication. |
| Performance | Profile fields are bounded, retrieval is limited to eight excerpts of 1,200 characters, and one tools-disabled model call requests at most 1,800 output tokens. No new unbounded list query or dependency change. |

## Scope and regression assessment

Reviewed the complete tracked baseline diff and all untracked implementation/test files, including `business_workspace.py` service/router, `Workspace.tsx`, `workspaceSelection.ts` and the new backend/frontend checks. Read the approved plan, original blocked review, remediation, latest QA, acceptance and current execution records.

Phase A stays within client workspaces, editable profiles/briefs and project isolation. No CRM, billing, client portal, multi-user platform, publishing integration, installer changes, redesign or Phase B implementation was identified. Existing Account Manager, Campaigns, Knowledge, Files, model selection, Markdown export and approval paths are preserved; the relevant regression evidence supports this assessment.

## Validation and remaining limits

**Independently performed during this re-review:**

- In-memory production-route checks verified all supplied creation fields, invalid creation without partial rows, rejection of the original unsupported citation with complete prior brief/provenance preservation, and canonical allowlisted citation acceptance.
- Controlled lazy-initialization interleaving verified the race remedy returns the winning row.
- Both frontend DEV-031 confirmation/selection regression commands passed.
- `git diff --check` against the public-main baseline passed.

**Inspected independent QA evidence, completed 2026-10-09 17:27:06 UTC:**

- Focused backend batch: **40 passed, 1 skipped**.
- Both frontend regressions, typecheck and production build: passed.
- Broader earlier acceptance evidence records **124 passed**.

The two-client browser scenario records name-only creation, scoped upload, manual brief editing/saving, restart persistence, isolation, replacement confirmation and friendly provider failure. This reviewer read that evidence and did not rerun the browser. The delayed-selection test is not a mounted-component end-to-end test.

Real AI generation remains **unverified/blocked in acceptance**: no provider/model was available. Fake provider checks are deterministic test evidence only. GitHub Actions, remote commit/PR status and merge readiness were not checked in this execution and are not implied by APPROVE. Keep generated pytest/smoke runtime files out of the implementation commit as appropriate.

**Final disposition:** APPROVE the remediated Phase A code. Continue the user-authorized PR/CI flow, preserve the generation limitation in the handoff, and require actual CI success plus public-main merge verification before reporting the release complete.
