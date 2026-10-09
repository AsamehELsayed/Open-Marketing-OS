# DEV-031 Phase A Independent QA — Second CI Remediation

**Execution:** Separate general QA execution substituting for the unavailable OpenCode `qa` lane, under `docs/development-delegation-system.md §10`. This execution is independent from the remediation execution. No implementation files were edited. The report is the only source artifact updated; the requested build may generate ignored frontend output.

**Started:** 2026-10-09 20:27:34 UTC
**Completed:** 2026-10-09 20:36:09 UTC
**Verdict: PASS** for the requested local remediation QA. The second public GitHub Actions run is recorded as failed; this is not a claim of green CI or release completion.

## Scope reviewed

Read the supplied workspace operating contract, delegation policy, approved `plan.md`, full current diff, `remediation.md`, `remediation-ci.md`, updated `qa-brief.md`, current `acceptance.md`, and prior QA and review reports, including the prior final PASS and APPROVE. The working diff changes three test files plus run artifacts. No product runtime or frontend source code changed in this remediation. The working tree also contains untracked DEV-008 run artifacts, which I left untouched.

The local CI record says run `37985032794` at `5dc8c3f0251553822ddf7420da657b431cab0ff2` failed in both Linux Python matrix jobs; Windows and five other checks passed. Its pytest artifact downloads returned HTTP 401 per the local report. A direct fetch of the Actions page also returned a cache miss. The recorded local full-suite reproduction was **2,601 passed, 99 skipped, 3 failed**; the three failures are the exact regressions rerun below. No new public run was checked or reported green.

## Remediation review

- **Website audit determinism — CLOSED.** The formerly network-dependent DEV-012 test monkeypatches `web_tools._default_website_transport` to return a deterministic successful Example Domain response. The website audit tool uses that transport when no explicit transport is supplied. The test asserts a successful `website_marketing_audit` run, no campaign row, no `propose_campaign` run, and that campaign remains in the forbidden actions. The exact test passed; this preserves the allowed analysis path while verifying campaign creation is blocked.
- **Schema-version assertions — CLOSED.** Both formerly hard-coded assertions now compare the database user version to `SCHEMA_VERSION`; the current canonical constant is 13. The v10 migration regression still performs the attachment-table round-trip and validates the post-upgrade version, and the fresh-schema test validates the schema and table set.

No new scope issue was found. The current code changes are confined to deterministic and version-resilient regression tests; no CRM, Phase B, runtime behavior, frontend changes, or provider changes were introduced.

## Commands and results

Pytest used elevated host access for Windows TestClient/socketpair setup.

- Three exact second-run failures:
  - `test_dev015_i1_migration.py::test_v10_to_v11_attachment_tables_migrate_idempotently_and_round_trip`
  - `test_w2_sqlite.py::test_schema_version_and_tables`
  - `test_dev012_write_constraints.py::test_allowed_website_read_runs_with_campaign_suggestion_and_no_write`

  The first invocation with an explicit `.pytest-tmp/ci-second-exact` base could not create that path because its parent directory was absent; no assertions ran. Rerunning the same three tests using pytest's default temp root passed: **3 passed in 4.27s**.
- Focused Phase A batch plus fixture regression (DEV-008G, DEV-031, DEV-014, DEV-030, and `test_internal_fixtures.py`): **41 passed, 1 skipped, 13 warnings in 7.73s**.
- Exact Windows workflow set from `.github/workflows/ci.yml`: **102 passed, 2 skipped, 21 warnings in 58.17s**. The skips are the private DEV-007 artifact test and private publication runbook test.
- Frozen React route inventory assertion: **1 passed, 1 warning in 1.82s**.
- From `frontend`: both `node tests/dev031-workspace-confirmation.test.mjs` and `node --experimental-strip-types tests/dev031-workspace-selection.test.mjs` passed.
- From `frontend`: `npm run typecheck` passed; `npm run build` passed (Vite transformed 97 modules; build step completed in 13.99s). No frontend code changed in this remediation.
- `git diff --check HEAD` passed. Git emitted working-copy LF-to-CRLF notices for the run artifacts only.

## Acceptance and limitations

The recorded Northstar/Harbor browser acceptance documents client isolation, persistence, scoped files, and the provider-failure flow. I inspected the record and did not rerun the browser. The acceptance environment lacked a ready model/provider; no real AI generation is claimed, and this QA made no provider call.

The exact remediated tests and requested focused/Windows/frontend checks pass locally. The full pytest suite was not rerun after these narrow test fixes, and public CI still needs a successful rerun. This QA does not establish Linux matrix success, browser behavior beyond the existing record, real AI generation, merge status, or release completion.
