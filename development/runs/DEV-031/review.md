VERDICT: APPROVE

# DEV-031 independent SOL re-review after CI remediation

**Execution:** Separate `gpt-6.1-sol` review execution using the previously loaded code-review-and-quality rubric. Read-only on implementation; no delegation or provider calls.

**Started:** 2026-10-09 20:04:13 UTC
**Completed:** 2026-10-09 20:06:27 UTC
**Reviewed baseline:** public main `efbcb7582ca43ef35db70f462ee52a1d9136789b`
**CI-remediation base:** `bedcb0c22866b1a9eb7676ae3dac13fa56e6500e`

No required code changes remain. APPROVE applies to the local code-review gate. Remote CI success and merge completion are separate requirements and have not been verified by this reviewer.

## CI remediation

### Exact private-fixture detection — approved

`app/tests/internal_fixtures.py:50-58` explicitly enumerates these seven files:

- DEV-005 parity: `chat_corpus.json`, `rag_golden.json`, `state_cases.json`.
- DEV-007 migrations: `w2.sql`, `w4.sql`, `w5.sql`, `w6.sql`.

At lines 97-109, the existing skip marker now depends on every required path being a file rather than the parent directory existing. Checked the actual consumers: `test_w0_contracts.py:341-369`, `test_dev007_files.py:486-497`, `test_dev007_tool_registry.py:436-440`, `test_dev007_vault.py:469-485`, and `test_dev007_mcp.py:712-715`. The enumerated paths match their private artifact reads.

The new `app/tests/test_internal_fixtures.py:7-16` verifies that a DEV-031 directory alone does not enable those tests, then verifies that supplying all required files enables them. This corrects a pre-existing availability assumption exposed by committing the required public run evidence. It does not skip general product or DPAPI tests merely because they are old; the existing decorated private-artifact tests keep their explicit skip reason, and execute when the required fixtures are present. Missing artifact contents are not fabricated.

### Workspace route inventory — approved

`app/tests/test_dev007rfinal_surface.py:150` adds only `/app/workspace` to the exact expected route sequence. It matches the approved route at `frontend/src/App.tsx:68` and retains the exact-list assertion. There is no wildcard relaxation or removal of route coverage.

### Minimality and five-axis assessment

The entire runtime/implementation tree is unchanged relative to the previously reviewed Phase A commit. The current code diff is two existing test infrastructure files plus the new fixture-gate regression; remaining changes are run evidence/metadata.

- **Correctness:** file-based fixture availability matches the consumed private artifacts; the route expectation matches the approved product route. Independently executed both affected regressions successfully.
- **Readability:** explicit fixture paths, one small availability helper, and one route-list insertion make the causes and remedies clear.
- **Architecture:** preserves the existing skip-marker mechanism and exact route-inventory test. No product/provider/migration architecture change in this remediation.
- **Security:** no secret, access-control, permission, publishing or DPAPI runtime behavior changed. The Windows product tests remain enabled; the private migration-artifact test is the intended skip when its unpublished fixtures are missing.
- **Performance:** seven bounded file checks at test-module initialization; no product hot-path impact.

## Prior fixes and approved requirements

The previously reviewed runtime code is unchanged, so the substantive findings remain closed. Rechecked its relevant boundaries and the earlier `review-before-ci-remediation.md` evidence:

| Prior finding | Current disposition |
|---|---|
| F1 unsupported citations | CLOSED: `app/routes/business_workspace.py:25-50`, `:167-173` validate source candidates against retrieved IDs before persistence. Original punctuated-citation rejection and saved provenance preservation were independently reproduced in the prior review; focused QA still passes. |
| F2 optional creation fields | CLOSED: `app/services/business_workspace.py:13-25` supplies the shared validator; creation at `app/routes/business_workspace.py:69-86` persists supplied fields after validation. |
| F3 A→B→A responses | CLOSED: `Workspace.tsx:58-92` guards action success, errors and finalization with both selected ID and epoch; `workspaceSelection.ts:1-8` remains unchanged. |
| F4 lazy initialization race | CLOSED: `app/services/business_workspace.py:32-38` uses conflict-safe insert and reload. Prior controlled reproduction and current QA's synchronized-reader regression support the fix. |

All four founder UX requirements remain represented: short name-only creation with optional fields; generation status and actual returned provider/model with understandable failures and manual editing available; explicit saved/draft replacement confirmation plus revision checks; and separated user facts, evidence, AI suggestions and missing information with supported citations.

The implementation still reuses Projects, existing Knowledge/files, SQLite and ModelRouter; preserves first-run onboarding, Account Manager, campaigns, automatic tools, model selection, Markdown export and approval guards; and stays inside Phase A. No CRM, Phase B, client portal, billing, multi-user platform, publishing integration, installer change, redesign or provider stack was added.

## Evidence and verification

Read the approved plan, supplied operating contract, repository delegation policy, original blocked review, prior approved re-review, `remediation.md`, `remediation-ci.md`, current `qa.md`, acceptance and execution records. Reviewed the current tracked code diff and new fixture regression. The final QA artifact was reread after its authoritative completion at **2026-10-09 20:04:37 UTC**, and its execution record now agrees. Initial context reading began against the earlier provisional PASS artifact; the final verdict uses the completed QA record.

**Independently performed in this review:**

`python -B -m pytest --basetemp=.pytest-tmp/review-ci -p no:cacheprovider -q app/tests/test_internal_fixtures.py app/tests/test_dev007rfinal_surface.py::test_react_route_inventory_is_exactly_the_frozen_set`

Result: **2 passed, 1 warning in 2.56 seconds**. The warning is the existing Starlette/AnyIO deprecated alias. `git diff --check HEAD` also passed with working-copy LF/CRLF notices only.

**Inspected final independent QA evidence:**

- Focused backend and fixture gate: **41 passed, 1 skipped**.
- Exact Windows workflow test set: **102 passed, 2 skipped**.
- Frozen route inventory: **1 passed**.
- Frontend confirmation/selection regressions, typecheck and production build: passed.

These local results support the remediation; they do not establish the full remote Linux matrix result.

## Limitations and release gates

The recorded two-client browser scenario covers creation, scoped upload, manual brief save/edit, restart persistence, isolation and provider-failure behavior. This reviewer did not rerun the browser. Real generation remains unverified because no model/provider was available; deterministic fake output is test evidence only. The selection regression is a helper/source-guard check, not a mounted React end-to-end delay test.

The first GitHub Actions run is recorded as failed. This execution did not independently fetch or verify a successful rerun, push, PR status or merge. Continue the user-authorized PR/CI flow, obtain actual required CI success, and verify the merge on public main before reporting release completion. Preserve the real-generation limitation in that report.
