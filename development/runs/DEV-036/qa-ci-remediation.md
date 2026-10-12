# DEV-036 Independent QA — CI Sentinel Allowlist Remediation

**Verdict: PASS_WITH_CHANGES**

## Execution

- **Started:** 2026-10-12 19:00:18 UTC
- **Completed:** 2026-10-12 19:05:16 UTC
- **Checkout:** isolated `development/worktrees/DEV-036-citation-diagnostics`, branch `codex/dev-036-citation-diagnostics`
- **Scope:** independently inspect the exact-path secret-scan sentinel exception, run the public-repository secret scanner, and run the three-file citation regression suite. No application code, tests, or sentinel allowlist were edited by this QA execution. No commit or push was made.

## Sentinel exception and scope

The CI remediation adds two `(path, literal, reason)` entries in `scripts/package/secret_scan.py`: the same synthetic credential-shaped privacy-test sentinel is allowed in exactly `app/tests/test_dev031_business_workspace.py` and `app/tests/test_dev032_campaign_ai.py`.

This matches the scanner's documented policy at lines 14–20: sentinel exceptions are per file and literal. `_sentinel_exemptions()` selects entries by exact repository-relative path, and `scan()` suppresses a finding only when the matched candidate exactly equals an exempt literal. The scanner patterns and matching logic are unchanged. The allowance does not exempt a pattern, filename glob, directory, or other value. The two test files contain deterministic redaction regressions that need a format-realistic synthetic value.

The inspected working diff contains no changes to citation detection, persistence, provider safeguards, tests, project scoping, or atomic-write behavior. Thus this CI-only adjustment has no application behavior surface. Prior independent DEV-036 evidence in `qa-remediation2.md` records 131 targeted tests passing after the application remediation and before this sentinel-only adjustment.

## Verification

- `python scripts/package/secret_scan.py --root .` — **PASS**. Scanned 549 text files; 14 allowlisted sentinels; 0 real-secret hits; 0 home-path hits.
- Exact requested command, `python -m pytest -q app/tests/test_dev031_business_workspace.py app/tests/test_dev032_campaign_ai.py app/tests/test_dev033_campaign_validation.py` — **85 passed, 46 setup errors, 1 warning** in 107.63 seconds. There were no assertion failures. Every setup error was pytest failing to create a numbered `tmp_path` directory under the sandboxed Windows AppData temp root (`could not create numbered dir ... after 10 tries`); the first affected test was `test_v13_migration_and_client_profile_revision`.
- Retried the same three files with `--basetemp development/runs/DEV-036/pytest-qa-ci-remediation-independent` to use the authorized worktree. The run produced no progress output for 102 seconds and was interrupted; it has no passing result and is not counted as a successful suite run.
- The existing `starlette.testclient` / `anyio.abc.BlockingPortal` deprecation warning appeared in the completed run.

The temp-root setup errors and the interrupted retry are environment limitations, not failures attributable to the sentinel-only change. However, this execution did not independently complete the full suite after the CI adjustment, so the verdict is PASS_WITH_CHANGES and the prior 131-pass evidence is preserved separately rather than represented as this run's result.

## Safety and regression scope

- **Behavioral parity:** The current CI-only diff changes only exact sentinel allowlist entries; the approved parity evidence remains the pre-adjustment 131-pass run in `qa-remediation2.md`. No application/test source changed in this QA execution.
- **Privacy:** Secret scan found no real secrets or home paths. The two exception entries match only the exact synthetic regression sentinel in the two exact test files. No rejected model prose or completion was generated or stored by this QA.
- **Project isolation / no-write safeguards:** No application or persistence code changed in the CI remediation. This QA made no Northstar data changes and invoked no Northstar acceptance attempt.
- **Providers:** Tests use deterministic/fake providers; QA initiated no provider request or model inference. **Real model/provider calls: 0.**

## Outstanding QA note

Required CI remains the authoritative full-suite check for this PR. The local rerun limitation should be reconciled against GitHub CI before merge; this QA did not edit source to work around the sandbox temp-root restriction.
