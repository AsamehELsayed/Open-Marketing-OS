# Phase 3: Windows Beta Acceptance (Open Marketing OS 0.1.0-beta.2)

**Decision: CONDITIONAL GO for a limited Windows beta pilot** (build 03c4ebfa, CI run 40417018537).
The full journey (chat, plan, generate, revise, approve, export, second project, restart) completed in the installed
app with a real OpenRouter model, once, on one free model. It becomes GO when the manual checklist (items 1-3, 5)
passes on a clean Windows account, you accept the unsigned-installer SmartScreen warning, and the pilot is told to use a
capable model (free models are very slow, section 5b). Not a public-release decision.

Build success and runtime acceptance are reported separately. Nothing is marked PASS unless it was executed.

## 1. Build status (CI)
| Run | Commit | Windows | Linux | Note |
|---|---|---|---|---|
| 40273844902 | 88f24e1d | success | success | Phase 2 baseline (given) |
| 40403299813 | 1809cec9 | success | success | Phase 3 crash-recovery fix build (this branch, workflow_dispatch) |
| 40406294249 | 5b9d75fa | success | success | + usage-accounting fix |
| 40412126804 | 2dca08a3 | success | success | + labelled-suggestion validator and prompt |
| 40417018537 | 03c4ebfa | success | success | **Final**: + call-to-action validator fix |

CI success means the installer builds and unit tests pass. It says nothing about runtime behaviour (section 3).
The regular `CI` workflow (backend 3.11 and 3.12, frontend, secret hygiene, packaging, skills provenance, Windows DPAPI) runs on pull requests. On PR #17 it first failed on 6 items that came from the Phase 2 integration code (a fake test key not allowlisted in the secret scan; Linux-only assumptions in 5 tests that had only ever run on Windows). Test and allowlist fixes only; no product code changed. **All 8 checks pass on commit 54a0493a.** No product or installer file changed after build 03c4ebfa, so that installer is still the tested candidate.

## 2. Artifact integrity (executed)
| Artifact | SHA-256 | vs SHA256SUMS | Embedded build id |
|---|---|---|---|
| Run 40273844902 installer | 51c1fdbc3aef...84570cb8 | match (also matches candidate-manifest) | 88f24e1d...-40273844902-1 |
| Run 40403299813 installer | c1843338ae0f...ae8f866 | match | 1809cec9...-40403299813-1 |
| Run 40406294249 installer | 080299cfa594...bcd3b5 | match | 5b9d75fa...-40406294249-1 |
| Run 40412126804 installer | a2ced1954f27...125162 | match | 2dca08a3...-40412126804-1 |
| Run 40417018537 installer (final) | c804d6f1fa28...5cd9d6 | match | 03c4ebfa...-40417018537-1 |

These are the real CI bytes downloaded with `gh`, not a local rebuild. Both installers are **not code-signed**
(`Get-AuthenticodeSignature`: NotSigned), so SmartScreen will warn.

## 3. Runtime acceptance (executed on the installed app; run-40403299813 build, repeated unchanged on the final 03c4ebfa build)
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


| 4 | High | The evidence gate refused any figure in a labelled creative suggestion (e.g. posting cadence), and the prompt did not tell models how to label suggestions or fill "Persisted user facts" | Fixed (commit 2dca08a3): labelled suggestion lines may carry content-plan figures (posts, stories, emails, days, weeks); prices, discounts, percentages, dates, results, outcome targets and figures that could restate a source are still refused; prompt updated; tests added |
| 5 | Medium | A labelled call to action naming "followers" was refused only because the word is on the high-risk list | Fixed (commit 03c4ebfa): exempt only labelled lines that start with a call-to-action verb, contain no figure, no other high-risk term and no outcome verb; "followers have doubled", "boost followers", "reach 10k followers" are still refused; tests added |

## 5. Chat, plan, generate, revise, approve, export
- **From source, deterministic provider, real Chromium against the real app:** `test_dev044_ui_journey.py` 2/2 PASS (includes revise, approved-only export, persistence after reload, cross-project 404).
- **Installed app:** only chat, plan, and the no-provider generate failure were executed. **Generate, revise, approve and export in the installed app are NOT executed.** They need a real OpenRouter key, and the provider URL is hard-coded and the sidecar environment allowlisted, so no deterministic provider can be injected without weakening the security boundary. The owner authorized one real run; see section 5b for the executed result.

## 5b. Real OpenRouter run (authorized, executed)
Installed app, isolated profile, key entered by the owner in Settings (never read or logged by me; profile and key
deleted afterwards). Provider test: Credential Configured, Provider Healthy, Capability Verified.

Attempts, in order. Each failure exposed a real defect that no deterministic test could:
| # | Build | Model | Result |
|---|---|---|---|
| 1 | 1809cec9 | recursive/flint-v1.0:free | Bug: `reasoning_tokens > output_tokens` failed the ModelCall contract, rejecting the whole answer. Fixed (defect 3). |
| 2-3 | 5b9d75fa | recursive/flint-v1.0:free | Evidence gate refused the strategy brief: model restated the campaign title under "Persisted user facts". Prompt now says how to fill that section. |
| 4 | 5b9d75fa | anthropic/claude-haiku-5.5 | "Provider request failed" after 5 s, cause not visible (likely no account credit, unconfirmed). Haiku was not tested further. |
| 5 | 5b9d75fa | nvidia/nemotron-3.5-super-vl:free | Gate refused "5 feed posts per week plus 3 stories" in a labelled suggestion. Gate now accepts content-plan figures on labelled suggestion lines (defect 4). |
| 6 | 2dca08a3 | nvidia/nemotron-3.5-super-vl:free | Chat to campaign card OK (551 s). Generate OK: 3 AI drafts saved (276 s), export disabled until approval. Revise refused: "Suggestion: Invite followers to share a childhood bakery memory..." (only "followers" tripped the gate). Fixed (defect 5). |
| 7 | 03c4ebfa | nvidia/nemotron-3.5-super-vl:free | **Revise OK, send to review OK, approve OK, approved-only export OK.** |

Evidence from attempt 7 (final build):
- Export zip (HTTP 200, 999 bytes) held only the approved social post (approved version 4) and the summary; the other two items were listed as "NOT INCLUDED (not approved yet)".
- Second project created through the GUI: its campaign list, chat list and campaign page showed none of the first project's content; the first project's work package and approved export requested through the second project's id returned 404.
- After a clean quit and relaunch the 3 deliverables persisted (strategy_brief DRAFT, ad_copy DRAFT, social_post APPROVED).
- Cost: all runs used free models (recorded cost 0.0). The Haiku request did not complete.

Caveats, stated plainly:
- One successful run, on one free model. This proves the plumbing end to end; it does not prove output quality or reliability across models. Haiku or any paid model was not verified.
- The free model is slow: chat turn 551 s, generation 276 s. A pilot user will think the app hung unless told. Recommend a capable paid model.
- The saved "deliverable" is the model's four-section working document (Persisted user facts, Retrieved evidence, AI suggestions, Unknown information), not ready-to-post copy. That is a product-quality observation, not a defect in the release gate.
- Export was fetched through the same API the Download button uses; the native Save dialog was not driven.

## 6. Other automated tests run
Full backend suite on commit 2dca08a3: 3080 passed, 0 failed, 85 skipped (one file, `test_w2_langchain_first.py`, excluded: chromadb import mismatch in this machine's Python env). Validator tests on the final commit: 106/106; campaign/deliverable/journey subset 322 passed. Desktop unit tests 9/9. Earlier router subset: 624 passed, 0 failed, 7 skipped
(`test_w2_langchain_first.py` excluded: chromadb import mismatch in this machine's Python env, same as Phase 2).
CI ran the full backend suite on Linux (3.11 and 3.12) at 54a0493a: pass.

## 7. NOT executed
A paid or capable model (Haiku failed at the provider; cause unknown); output quality across models; the native Save dialog for downloads; uninstall and credential persistence re-test on the fix build; clean-VM or Windows 10 install; SmartScreen flow; reboot persistence; sleep/resume;
antivirus interaction; the full backend CI on this branch. Linux was not tested and is not required for this decision.

## 8. Remaining blockers and risks
No code blocker. Open items:
1. Manual checklist items 1-3 and 5 on a clean Windows account (item 4, the real-key run, was executed by me on one free model; repeat with a capable paid model if the pilot will use one).
2. Unsigned installer.
3. The fix is on branch `claude/windows-beta-acceptance-phase-3-ad1d9b` and is **not merged** into the integration branch. The build you ship must contain commit 03c4ebfa (usage fix, labelled-suggestion and call-to-action validator changes, crash-recovery fix).
4. The native Retry dialog was exercised by a synthetic Enter keypress on the default button, not by a human click.

## Exact release-readiness status
- Build: green (Windows and Linux, run 40417018537 at 03c4ebfa; installer sha256 c804d6f1...5ac5cd9d6 matches SHA256SUMS)
- Windows runtime, automated, no real AI: PASS (30 of 31; the one FAIL is the harness's own cleanup `taskkill` finding nothing to kill)
- Windows runtime, real AI, installed app: PASS once, on one free model (chat, plan, generate, revise, approve, export, second project, restart)
- Not verified: paid/capable model, output quality, clean-machine/Windows 10, human-click recovery dialog, SmartScreen flow
- Merge / tag / publish: not done, awaiting your approval
