# DEV-031 initial GitHub CI remediation record

## Initial PR run

- PR: https://github.com/AsamehELsayed/Open-Marketing-OS/pull/4
- Workflow run: https://github.com/AsamehELsayed/Open-Marketing-OS/actions/runs/37967435582
- Head commit: `bedcb0c22866b1a9eb7676ae3dac13fa56e6500e`
- Failed: Backend tests (Python 3.11), Backend tests (Python 3.12), and Windows tests (DPAPI credential vault).
- Passed: packaging, skills provenance, secret hygiene, frontend typecheck/build, and locked runtime.

## Findings and changes

1. The Windows CI command exposed a latent test-gate bug: `requires_internal_runs` treated any `development/runs/` directory as proof that all legacy private fixtures existed. Committing the required DEV-031 role evidence made that directory present, while the public repository intentionally omits the DEV-005 parity and DEV-007 migration fixtures. The gate now checks the exact fixture files consumed by the gated tests. `test_internal_fixtures.py` verifies a DEV-031 directory alone does not enable them.
2. A local full-suite run after fixing the fixture gate passed 412 tests before reaching `test_react_route_inventory_is_exactly_the_frozen_set`. The approved `/app/workspace` route was missing from that frozen expected set. Added it to the assertion.

## Local verification after changes

- Exact Windows CI test set: **102 passed, 2 skipped in 38.05 seconds**.
- DEV-031 focused backend suite plus fixture-gate regression: **41 passed, 1 skipped**.
- Frozen route inventory regression: **1 passed**.
- The first PR Actions run remains failed until a new run reports success. No claim of final CI success is made here.

The targeted tests used the repository's normal test fixtures. Windows TestClient initialization required elevated local access under the workspace sandbox. No product runtime code changed in this CI remediation.
