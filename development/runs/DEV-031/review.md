VERDICT: APPROVE

# DEV-031 independent SOL re-review after second CI remediation

**Execution:** Fresh, separate `gpt-6.1-sol` review after final independent QA PASS. Applied the previously loaded code-review-and-quality skill and repository delegation policy. Read-only on implementation; no delegation or model call.

**Started:** 2026-10-09 20:37:34 UTC
**Completed:** 2026-10-09 20:40:44 UTC
**Reviewed public-main baseline:** `efbcb7582ca43ef35db70f462ee52a1d9136789b`
**Second-remediation base:** `5dc8c3f0251553822ddf7420da657b431cab0ff2`

No required code changes remain. APPROVE is the current local review verdict. It does not establish remote CI success, a merge, or release completion.

## Three test changes

### Canonical schema version — approved in both tests

`app/tests/test_dev015_i1_migration.py:2`, `:31` and `app/tests/test_w2_sqlite.py:4`, `:15` now import and compare against `SCHEMA_VERSION`, which is 13 at `app/database/sqlite.py:8`. The old literals expected 12 despite the approved v13 migration.

The migration test still reconstructs the v10 attachment-table boundary, applies the v10→v11 migration twice, persists attachment bindings/selections, reopens through the current `connect()`, and verifies both persistence and conversation scope. The fresh-schema test still checks its expected tables, message columns and conversation/turn model columns. No structural assertion was removed. Comparing the reopened current database with the canonical current schema is the appropriate contract; the change does not redefine the historical migration's behavior.

### Deterministic allowed-read/no-campaign-write test — approved

`app/tests/test_dev012_write_constraints.py:99-121` patches only `web_tools._default_website_transport` with a successful synthetic fetched-page response. It returns the page fields consumed by the existing audit, including URL, status, title/text, links and viewport flag. Pytest's `monkeypatch` restores the global after the test.

Inspected the real seam: `app/services/tools/web_tools.py:210-225` calls the injected transport with positional URL/timeout, and `:353-365` chooses that default for website audit when no transport is supplied. The fake signature matches this invocation. The test continues to execute the actual graph, registered audit, constraint handling and SQLite telemetry, rather than replacing their results.

The original assertions remain at test lines 129-134: an audit succeeds and returns a capability answer; campaign rows remain empty; `propose_campaign` was never run; and campaign creation remains explicitly forbidden. Live website availability was unrelated to those constraints. A deterministic response removes network variability without weakening the product behavior being tested.

## Minimality and five axes

The complete current implementation diff relative to the preceding commit is these three test files. Run metadata, QA and remediation records also changed. Relative to the original approved Phase A implementation commit, cumulative CI changes remain confined to tests: fixture detection/regression, route inventory, these two version assertions and the injected transport. No runtime or frontend source changed.

| Axis | Assessment |
|---|---|
| Correctness | All three exact full-suite failure tests now pass independently. Their substantive schema, attachment, audit and forbidden-write assertions remain intact. |
| Readability | Canonical version references remove stale literals; the synthetic page response is explicit and scoped to its one test. |
| Architecture | Uses the existing schema constant and transport injection seam. No product abstraction, provider, migration or workflow change. |
| Security | Campaign permission behavior remains tested through the real graph; no secret, publishing, auth, vault or runtime guard was altered. |
| Performance | Removes a live fetch from a focused regression. No production performance effect or dependency change. |

No required finding or scope expansion was identified.

## Prior Phase A fixes and founder requirements

Rechecked current boundary code and prior review evidence. All four original findings remain closed:

- **F1 citations:** `app/routes/business_workspace.py:25-50`, `:167-173` validates references against scoped retrieved IDs before persistence; prior independent reproduction confirmed rejection and complete saved brief/provenance preservation.
- **F2 partial creation:** shared validation at `app/services/business_workspace.py:13-25` is used by creation at `app/routes/business_workspace.py:69-86`; supplied supported optional fields persist.
- **F3 A→B→A actions:** `Workspace.tsx:58-92` guards action success/errors/finalization by ID and selection epoch through `workspaceSelection.ts:1-8`.
- **F4 concurrent initialization:** `app/services/business_workspace.py:32-38` uses conflict-safe insert and reads the winning row.

The earlier private-fixture detection and exact `/app/workspace` inventory corrections remain intact.

All four founder UX requirements remain represented: short name-only creation with optional details; generation state, actual returned route/model and understandable failures with manual editing available; explicit replacement confirmation with revision protection; and separated facts, evidence, suggestions and missing information with validated citation IDs.

Phase A continues to reuse Projects, scoped Knowledge/files, SQLite and the existing ModelRouter. First-run onboarding, Account Manager, automatic tools, campaign drafts, model selection, Markdown export and approval guards remain supported. No CRM, Phase B, multi-user platform, billing, portal, publishing integration, redesign, installer change or provider stack was introduced.

## Checks and evidence

Read the approved plan, supplied operating contract, delegation policy, current `qa-brief.md`, `qa.md`, `remediation-ci.md`, `remediation.md`, acceptance, earlier review records and current execution records. The final QA PASS completed **2026-10-09 20:36:09 UTC**, before this review began.

**Independently performed in this execution:**

`python -B -m pytest --basetemp=.pytest-tmp/ci2-review -p no:cacheprovider -q app/tests/test_dev015_i1_migration.py::test_v10_to_v11_attachment_tables_migrate_idempotently_and_round_trip app/tests/test_w2_sqlite.py::test_schema_version_and_tables app/tests/test_dev012_write_constraints.py::test_allowed_website_read_runs_with_campaign_suggestion_and_no_write`

Final elevated confirmation run: **3 passed in 4.10 seconds, exit 0**. The first sandbox attempt produced no result and was interrupted. A subsequent workspace-temp attempt printed 3 passes as the interruption arrived; that interrupted exit is not counted as the successful verification. No test or implementation file was edited by this reviewer.

`git diff --check HEAD` passed; Git emitted LF/CRLF notices for run artifacts only.

**Inspected final independent QA evidence:** the same three failures passed; focused Phase A/fixture checks reported **41 passed, 1 skipped**; Windows workflow set reported **102 passed, 2 skipped**; route inventory reported **1 passed**; frontend confirmation/selection regressions, typecheck and build passed.

## CI and acceptance limitations

`remediation-ci.md` records public run **37985032794** at `5dc8c3f` as failed in both Linux backend matrix jobs, with Windows and other checks passing. The recorded local full-suite reproduction had **2,601 passed, 99 skipped, 3 failed** before these fixes. The three reproduced local failures correspond to the three reviewed tests. Because remote pytest artifact downloads failed, this evidence does not prove these were the only causes of the Linux jobs.

A new successful public run is still pending/unverified. The full suite was not rerun by this reviewer or final QA after the narrow fixes. Obtain actual required CI success and verify the public-main merge before reporting release completion.

The existing Northstar/Harbor browser record documents creation, scoped upload, manual brief persistence, restart, isolation and friendly generation failure. This reviewer did not rerun the browser. Real AI generation remains unverified because no provider/model was available; deterministic fake output is test evidence only. The selection regression tests the helper and source guards rather than a mounted React delay sequence.

**Final disposition:** APPROVE the current remediated Phase A code for the local review gate; preserve these limitations and continue the authorized CI/merge flow.
