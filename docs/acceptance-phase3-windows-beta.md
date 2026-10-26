# Phase 3: Windows Beta Acceptance (Open Marketing OS 0.1.0-beta.2)

**Decision: CONDITIONAL GO for a limited Windows beta pilot.** It becomes GO once items 1-4 of
`docs/windows-manual-acceptance-checklist.md` (about 45 min) pass on a clean Windows account and you accept the
unsigned-installer SmartScreen warning for the pilot. This is not a public-release decision.

Build success and runtime acceptance are reported separately. Nothing is marked PASS unless it was executed.

## 1. Build status (CI)
| Run | Commit | Windows | Linux | Note |
|---|---|---|---|---|
| 40273844902 | 88f24e1d | success | success | Phase 2 baseline (given) |
| 40403299813 | 1809cec9 | success | success | Phase 3 fix build (this branch, workflow_dispatch) |

CI success means the installer builds and unit tests pass. It says nothing about runtime behaviour (section 3).
The regular backend `CI` workflow only runs on pull requests; no PR was opened, so it did not run on this branch.

## 2. Artifact integrity (executed)
| Artifact | SHA-256 | vs SHA256SUMS | Embedded build id |
|---|---|---|---|
| Run 40273844902 installer | 51c1fdbc3aef...84570cb8 | match (also matches candidate-manifest) | 88f24e1d...-40273844902-1 |
| Run 40403299813 installer | c1843338ae0f...ae8f866 | match | 1809cec9...-40403299813-1 |

These are the real CI bytes downloaded with `gh`, not a local rebuild. Both installers are **not code-signed**
(`Get-AuthenticodeSignature`: NotSigned), so SmartScreen will warn.

## 3. Runtime acceptance (executed on the installed app, run-40403299813 build)
Method: silent NSIS install to a normal path, isolated LOCALAPPDATA/APPDATA, real Electron window driven over CDP.
Harness: `scripts/acceptance/windows/`. 30 of 31 checks PASS. The one FAIL is the harness's own cleanup `taskkill`
erroring because no sidecar was left (the expected state). The same suite on the 88f24e1d build had a real FAIL
(defect 1 below), now fixed.

| Area | Result |
|---|---|
| Launch: window ready 3.4-6.2 s; preload bridge only; no `require/process/module` in the page; bridge status correct | PASS |
| Network lockdown: external fetch and navigation from the UI blocked | PASS |
| Sidecar protocol: no token / query-string token / bad Host / foreign Origin all return 401 | PASS |
| GUI: create business, create client workspace | PASS |
| Chat, campaign card, plan renders (installed app, no AI key) | PASS |
| Generate with no provider: app stays up, 0 deliverables fabricated, honest error shown | PASS |
| Project isolation (GUI + API): other project's campaign list and sidebar empty; its work-package and generate return 404 | PASS |
| Clean quit leaves no processes; restart keeps both projects and the campaign | PASS |
| Crash recovery: kill sidecar, app stays up, Retry, new sidecar, data intact | PASS after fix (FAILED before, see section 4) |
| Hard kill of the whole app: no orphan sidecar; relaunch keeps data | PASS |

Proven in Phase 2 and not repeated (no code change touched them): DPAPI credential store and no plaintext secrets,
Settings, connect key, persistence across restart, install/uninstall behaviour, CSP headers.

## 4. Defects found in Phase 3
| # | Severity | Defect | Status |
|---|---|---|---|
| 1 | High | Killing the local service made the **whole app exit silently within 0.5 s**; the Retry/Quit dialog never appeared (`recover()` destroyed the window, which fired `window-all-closed`, which called `app.quit()`). Data was preserved. | Fixed in `desktop/main.cjs` (`recovering` flag); verified on the installed fix build; source-level regression test added |
| 2 | Low | The "no AI" error said "OpenAI" in the OpenRouter edition | Partly fixed: correct when OpenRouter is the active provider. With no provider configured at all the UI still says "Local AI is unavailable and OpenAI is not configured". Left as is because `business_workspace.py` pattern-matches that text. |

## 5. Chat, plan, generate, revise, approve, export
- **From source, deterministic provider, real Chromium against the real app:** `test_dev044_ui_journey.py` 2/2 PASS (includes revise, approved-only export, persistence after reload, cross-project 404).
- **Installed app:** only chat, plan, and the no-provider generate failure were executed. **Generate, revise, approve and export in the installed app are NOT executed.** They need a real OpenRouter key, and the provider URL is hard-coded and the sidecar environment allowlisted, so no deterministic provider can be injected without weakening the security boundary. This needs your authorization (checklist item 4).

## 6. Other automated tests run
Desktop unit tests 9/9. Router/model/campaign backend subset: 624 passed, 0 failed, 7 skipped
(`test_w2_langchain_first.py` excluded: chromadb import mismatch in this machine's Python env, same as Phase 2).
The full backend suite was not re-run locally this phase.

## 7. NOT executed
Real-model chat and the real OpenRouter path; installed-app generate/revise/approve/export; uninstall and credential
persistence re-test on the fix build; clean-VM or Windows 10 install; SmartScreen flow; reboot persistence; sleep/resume;
antivirus interaction; the full backend CI on this branch. Linux was not tested and is not required for this decision.

## 8. Remaining blockers and risks
No code blocker. Open items:
1. Manual checklist, especially the one real-key campaign run (needs authorization).
2. Unsigned installer.
3. The fix is on branch `claude/windows-beta-acceptance-phase-3-ad1d9b` and is **not merged** into the integration branch. The build you ship must contain commit 1809cec9.
4. The native Retry dialog was exercised by a synthetic Enter keypress on the default button, not by a human click.

## Exact release-readiness status
- Build: green (Windows and Linux, run 40403299813)
- Artifact integrity: verified
- Windows runtime, automated, no real AI: PASS
- Windows runtime, real AI, clean machine: NOT YET TESTED
- Merge / tag / publish: not done, awaiting your approval
