# Direct technical validation

VERDICT: PASS (direct validation only; NOT independent QA)

The founder's `DONT USE DELEGATION` instruction stopped the worker and superseded future delegated validation packets. The primary agent ran these checks itself. No independent QA execution is claimed.

## Final acceptance on the delivered source

```powershell
python -m pytest app/tests/test_phase45_marketing_mcp.py app/tests/test_dev007_mcp.py app/tests/test_dev007_tool_registry.py app/tests/test_dev007_vault.py app/tests/test_phase4_deliverables.py app/tests/test_phase4_generation_lock.py app/tests/test_dev033_campaign_validation.py app/tests/test_dev032_deliverables_api.py app/tests/test_dev008_public_export.py -q -p no:cacheprovider --basetemp development/runs/DEV-048-PHASE45/direct-test-tmp-final
```

**217 passed, 24 skipped in 215.61s (0:03:35), exit 0.** `tests-acceptance.txt` is the final acceptance output after the network guard was strengthened to deny new sockets, UDP sends and name-resolution paths. **All 27 prototype cases executed; none skipped.** The 24 skips remain existing withheld-fixture checks. Earlier runs below preserve the implementation chronology; the final acceptance result takes precedence.

## Exact test commands and results

```powershell
python -m pytest app/tests/test_phase45_marketing_mcp.py app/tests/test_dev007_mcp.py app/tests/test_dev007_tool_registry.py app/tests/test_dev007_vault.py app/tests/test_phase4_deliverables.py app/tests/test_phase4_generation_lock.py app/tests/test_dev033_campaign_validation.py app/tests/test_dev032_deliverables_api.py -q -p no:cacheprovider --basetemp development/runs/DEV-048-PHASE45/regression-tmp
```

**204 passed, 24 skipped in 191.27s (0:03:11), exit 0.** `tests-final.txt` preserves output/exit. All 26 prototype cases in this collection executed. Existing legacy MCP tests are skipped because main excludes private DEV-007 migration fixtures; remaining skips are existing internal-fixture checks. These skips are not evidence of passing legacy MCP behavior.

```powershell
python -m pytest app/tests/test_phase45_marketing_mcp.py::test_public_export_keeps_optional_proof_dependencies_and_excludes_scratch app/tests/test_dev008_public_export.py -q -p no:cacheprovider --basetemp development/runs/DEV-048-PHASE45/export-test-tmp
```

**13 passed in 17.72s, exit 0.** `tests-export.txt` preserves output. This runs the 27th prototype test and 12 existing exporter tests. An ignored pyreadline3 Windows console-destructor invalid-handle error appeared after the result; the process still exited 0. Do not represent that console cleanup as clean.

The predecessor prototype suite (`tests-prototype.txt`) recorded 1 failed/24 passed before the safe LookupError refusal fix and later test additions. An earlier development run recorded 14 failed/11 passed when the overbroad network guard prevented Windows asyncio initialization. These are remediation history, not passing evidence. No unapproved/cross-project content reached a formatter in the failed denial case.

## Behavioral evidence

- Official pinned SDK real stdio initialize/list/call, exact discovered single tool/schema, isolated fixed executable/arguments and no provider environment keys.
- Default-deny before explicit fixture opt-in; DRAFT/IN_REVIEW/stale/pending/current-version mismatch, forged execution approval, wrong project/type/platform, malformed final copy and mismatched history refusals before tools/call.
- No artifact released after a concurrent editorial revision; temporary gateway approval revoked even on failure.
- Canonical repeated draft equality; final-copy-only output; malformed discovery, SDK errors and conflicting/non-object results fail closed.
- Parent and child network guards reject before connection. Windows stdlib event-loop setup uses private loopback IPC before guard activation; no provider call is present. This is trusted-fixture application enforcement, not hostile-plugin OS isolation.
- Test observers captured actual SDK process objects and verified process exit after successful, error and timeout exchanges. The host active-session counter also returns to zero.
- Real SQLite `tool_runs` rows, gateway audit metadata with no final-copy/token sentinel, inline credential rejection through existing vault-reference validation.

`python -m prototypes.marketing_mcp.demo` exited 0. `demo-evidence.json` includes synthetic output, schemas/protocol receipts and sanitized audit. The canonical draft hash is `[synthetic SHA-256 value omitted]`. Audit IDs/timestamps are operational evidence and intentionally variable.

`git diff --check` and `python -m py_compile` of the four prototype modules/new test passed. The prototype-only secret scan passed with zero secret/home-path findings; repository scan is recorded separately.

Captured logs are preserved with trailing whitespace normalized where necessary for Git whitespace checks; test messages, results and exit codes are unchanged.

## Limits

## Security/CI finalization follow-up

After GitHub flagged the synthetic digest and the Linux startup-race assertion, direct finalization validation passed **218 tests, 24 skipped in 233.67s**, exit 0 (`tests-finalization.txt`). The final all-prototype rerun passed **28 tests in 171.26s**, exit 0, with none skipped (`tests-finalization-prototype.txt`). A fresh SDK demo verified its full internal digest and omitted that value from public output. See `finalization.md` for source verification, unchanged-rule scans, PR-history remediation requirements and the independent human review process. These are direct tests only; independent QA/review remains BLOCKED.

Windows Python 3.12 execution is proven. No Linux, installed Electron, frozen executable, OAuth, live provider schema, real-account tenant mapping, or production performance acceptance is claimed. Test/dev pin is deliberate SDK-v1 compatibility debt. The production gateway's existing generic registry bridge and HTTP protocol completeness remain unchanged; this bounded prototype explicitly contains those reuse gaps.
