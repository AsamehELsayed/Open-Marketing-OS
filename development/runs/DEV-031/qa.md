# DEV-031 Phase A Independent Final QA — CI Remediation

**Execution:** Separate general QA execution substituting for the unavailable OpenCode `qa` lane, under `docs/development-delegation-system.md §10`. This is independent from the implementation/remediation execution. Implementation files were read-only; this execution wrote only this QA report.

**Started:** 2026-10-09 19:56:07 UTC
**Completed:** 2026-10-09 20:04:37 UTC
**Verdict: PASS** for the requested local QA. The first GitHub Actions run remains failed; this report does not claim CI green or release completion.

## Scope reviewed

Read the supplied workspace operating contract, `docs/development-delegation-system.md`, approved `plan.md`, `acceptance.md`, current `qa-brief.md`, `remediation.md`, `remediation-ci.md`, earlier QA failures, `qa-before-ci-remediation.md`, and `review-before-ci-remediation.md`. Reviewed the complete current working diff and new `app/tests/test_internal_fixtures.py`. Current implementation changes are limited to test infrastructure: exact private-fixture detection and the frozen React route expectation; run metadata and the QA/remediation artifacts also changed. No product runtime code changed in this CI remediation.

The recorded first PR Actions result (run [37967435582](https://github.com/AsamehELsayed/Open-Marketing-OS/actions/runs/37967435582), commit `bedcb0c22866b1a9eb7676ae3dac13fa56e6500e`) says Python 3.11, Python 3.12, and Windows test jobs failed, while packaging, skills provenance, secret hygiene, frontend, and locked-runtime jobs passed. I inspected the detailed local record in `remediation-ci.md`; a direct fetch of the Actions page returned a cache miss, so I did not independently refresh the remote run. No successful rerun is recorded.

## CI remediation verification

- **Fixture gate — CLOSED.** `internal_run_fixtures_available` returns true only when all seven required paths are files: DEV-005 parity `chat_corpus.json`, `rag_golden.json`, `state_cases.json`, and DEV-007 migration fragments `w2.sql`, `w4.sql`, `w5.sql`, `w6.sql`. Directory presence alone, including a DEV-031 run directory, leaves the gate false. The new regression creates only `DEV-031/` and asserts false, then creates the complete fixture set and asserts true. The Windows run also reports the intended internal-artifact skip for the gated DEV-007 vault test.
- **Frozen route assertion — CLOSED.** The exact inventory now includes `/app/workspace` after `/app/business/new`; the requested assertion passes against `frontend/src/App.tsx`.
- **First-run failure causes addressed in the local diff.** The fixture gate no longer treats any `development/runs/` directory as proof that all withheld fixtures are available. The frozen route list now reflects the approved workspace route. These are test-gating/expectation corrections, not product runtime changes.

Earlier Phase A issues remain closed on the current code: the v13 profile key is explicitly non-null; create/update share profile validation and supplied optional fields persist; lazy legacy profile initialization uses conflict-safe insert-and-reload; unsupported citations are rejected before brief/provenance persistence; and profile save, brief save, and generation each use project ID plus selection epoch guards. The current frontend confirmation accurately states that successful generation immediately replaces saved content and discards unsaved edits. The focused tests and prior accepted review/QA records support these checks.

No new scope issue was found. The reviewed scope remains Phase A; no CRM, campaign deliverables, Phase B, multi-user authentication, billing, portal, publishing integration, installer change, or provider stack was introduced.

## Commands and results

Routine pytest required elevated access because Windows TestClient socketpair initialization is blocked in the workspace sandbox.

- Focused backend batch, including the fixture-gate regression: `python -m pytest --basetemp=.pytest-tmp/basetemp -p no:cacheprovider -q app/tests/test_dev008g_publish_gate.py app/tests/test_dev031_business_workspace.py app/tests/test_dev014_integration.py app/tests/test_dev030_chat_backend.py app/tests/test_internal_fixtures.py` — **41 passed, 1 skipped, 13 warnings in 13.32s**.
- Exact Windows test set from `.github/workflows/ci.yml`: `python -m pytest -q -rs app/tests/test_dev007_hardening.py app/tests/test_dev007_vault.py app/tests/test_dev008_distribution.py app/tests/test_dev008_launcher.py app/tests/test_dev008_public_export.py app/tests/test_dev008_public_urls.py` — **102 passed, 2 skipped, 21 warnings in 42.62s**. Skips were the gated private DEV-007 migration test (fixtures are absent in this public checkout) and the private publication runbook test.
- Frozen route assertion: `python -m pytest --basetemp=.pytest-tmp/route -p no:cacheprovider -q app/tests/test_dev007rfinal_surface.py::test_react_route_inventory_is_exactly_the_frozen_set` — **1 passed, 1 warning in 1.34s**.
- From `frontend`, `node tests/dev031-workspace-confirmation.test.mjs` — **passed**.
- From `frontend`, `node --experimental-strip-types tests/dev031-workspace-selection.test.mjs` — **passed**.
- From `frontend`, `npm run typecheck` — **passed**.
- From `frontend`, `npm run build` — **passed**; Vite transformed 97 modules and built in 32.08s. Elevated access was used for the known Windows Vite realpath sandbox restriction.
- `git diff --check HEAD` — **passed**. Git emitted only existing LF-to-CRLF working-copy notices for `qa-brief.md` and `remediation.md`.

## Acceptance and limitations

The browser acceptance record describes the Northstar/Harbor two-client scenario, scoped upload, restart persistence, brief/file isolation, and friendly provider-failure behavior. I inspected that record and did not operate the browser in this QA execution. The recorded environment had no ready local model and no OpenAI/OpenRouter credentials; real generation did not occur, and this QA made no provider call. Fake-provider coverage is deterministic test evidence only.

Local requested checks pass, but the initial PR Actions run is still a failed run until GitHub reports a successful rerun. This QA does not establish full Linux matrix success, remote CI status, merge status, or real AI generation.
