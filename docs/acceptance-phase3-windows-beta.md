# Phase 3: Windows Beta Acceptance (Open Marketing OS 0.1.0-beta.2)

**Decision: NO-GO for a limited Windows beta pilot until one real-AI campaign completes end to end.**
Everything that can be tested without a real model is GO-ready. The real-model path was exercised once with an
authorized OpenRouter key and **did not complete** (section 5b). The decision flips to GO when checklist item 4
(on a funded account with a capable model) passes, items 1-3 and 5 pass, and you accept the unsigned-installer
SmartScreen warning. This is not a public-release decision.

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
| Run 40406294249 installer (final) | 080299cfa594...bcd3b5 | match | 5b9d75fa...-40406294249-1 |

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
| 3 | High | A real reasoning model reported `reasoning_tokens` above `output_tokens`; the strict ModelCall check threw away the provider's answer and failed the generation | Fixed in `model_router.extract_usage` (a subset leg larger than its parent is treated as unknown); regression test added; build 5b9d75fa (run 40406294249, Windows and Linux green) |


## 5. Chat, plan, generate, revise, approve, export
- **From source, deterministic provider, real Chromium against the real app:** `test_dev044_ui_journey.py` 2/2 PASS (includes revise, approved-only export, persistence after reload, cross-project 404).
- **Installed app:** only chat, plan, and the no-provider generate failure were executed. **Generate, revise, approve and export in the installed app are NOT executed.** They need a real OpenRouter key, and the provider URL is hard-coded and the sidecar environment allowlisted, so no deterministic provider can be injected without weakening the security boundary. The owner authorized one real run; it did not complete (section 5b).

## 5b. Real OpenRouter run (authorized, executed, did NOT complete)
Installed app, isolated profile, key entered by the owner in Settings (never read or logged by me; profile and key
deleted afterwards). Provider test: Credential Configured, Provider Healthy, Capability Verified.
Chat to campaign card and plan worked with the real app. Then "Write this content":

| Attempt | Build | Model | Result |
|---|---|---|---|
| 1 | 88f24e1d+fix1 (1809cec9) | recursive/flint-v1.0:free | **Bug**: provider answered but `reasoning_tokens > output_tokens` failed the strict ModelCall contract, so the whole generation was rejected. Fixed (defect 3). |
| 2 | 5b9d75fa | recursive/flint-v1.0:free | Model answered; app refused strategy_brief: "Persisted user facts" held a paraphrase not found in the owner's words/profile (persisted_fact_source_match). Nothing saved. |
| 3 | 5b9d75fa | recursive/flint-v1.0:free | Same gate refused again (quoted its own campaign title as a "fact"). Nothing saved. |
| 4 | 5b9d75fa | anthropic/claude-haiku-5.5 (paid) | "Provider request failed" after 5 s; no model_calls row. Cause not visible (message is sanitized). Likely no OpenRouter credit, unconfirmed. |
| 5 | 5b9d75fa | nvidia/nemotron-3.5-super-vl:free | Gate refused: "5 feed posts per week plus 3 stories" flagged as an unsupported number (numeric_source_match) although labelled a creative suggestion. Nothing saved. |

Cost: the free-model calls were recorded at 0.0; the Haiku request did not complete. No deliverable was ever
produced, so **revise, approve and export in the installed app are NOT executed**, and the second-project check
with real content was not done (isolation was proven without content in section 3).

What this shows:
- The refusals are the product's honesty gates working as designed and failing safe (nothing fabricated, nothing saved, clear message, app stays up).
- But all 3 free-model generations that reached the gates were refused, so the first-run experience on a weak or free model is "it never writes anything". A beta user will hit this. Either recommend a capable model in the OpenRouter edition (default is `openrouter/auto`, which may route anywhere), or make the gates and the prompt tolerant of clearly labelled suggestions. I did not loosen the gates: that is a product decision.
- Defect 3 was invisible to every deterministic test and only a real provider exposed it.

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
3. The fix is on branch `claude/windows-beta-acceptance-phase-3-ad1d9b` and is **not merged** into the integration branch. The build you ship must contain commit 5b9d75fa (the usage fix) on top of 1809cec9.
4. The native Retry dialog was exercised by a synthetic Enter keypress on the default button, not by a human click.

## Exact release-readiness status
- Build: green (Windows and Linux, run 40406294249 at 5b9d75fa; earlier 40403299813 and 40273844902)
- Artifact integrity: verified
- Windows runtime, automated, no real AI: PASS
- Windows runtime, real AI: ATTEMPTED, NOT COMPLETED (usage bug fixed; evidence gates refused all 3 free-model generations that reached them; Haiku call failed at the provider, cause unconfirmed). Blocks GO.
- Merge / tag / publish: not done, awaiting your approval
