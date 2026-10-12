# DEV-036 CI Secret Hygiene Remediation

**Role:** Remediation worker (general lane)
**Started:** 2026-10-12 18:54:25 UTC
**Completed:** 2026-10-12 18:59:32 UTC
**Trigger:** PR #10 required CI failed `Secret hygiene`; its repository scanner identified the deliberate synthetic GitHub token sentinel in three citation privacy test locations. Gitleaks had found no leaks.

## Fix

Added the exact synthetic token literal to the existing path-and-literal-scoped sentinel allowlist for the two affected test files. The exception cannot suppress other strings or findings. Scanner regexes, citation validation, application code, and persistence behavior are unchanged.

## Verification

- `python scripts/package/secret_scan.py --root .` — **PASS**, 14 allowlisted sentinels, 0 real secret hits, 0 home-path hits.
- Focused deterministic citation/privacy tests across the three approved test files — **17 passed, 114 deselected**, one existing Starlette/anyio deprecation warning.
- A full-suite rerun was started locally but stopped after more than three minutes without further output; the earlier pre-remediation full suite passed 131 tests. Independent QA is tasked to rerun the complete three-file suite.
- Real model/provider calls: **0**. Northstar data was not read or changed.

## Changed files

- `scripts/package/secret_scan.py`
- `development/runs/DEV-036/remediation.md`
- `development/runs/DEV-036/ci-remediation.md`

Independent QA and independent re-review remain required before updating PR #10.
