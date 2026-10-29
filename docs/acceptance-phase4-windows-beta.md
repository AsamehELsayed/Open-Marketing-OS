# Phase 4: Windows Beta Acceptance (Open Marketing OS 0.1.0-beta.2)

**Decision: NO-GO for Windows Beta Pilot Release** (Branch: `codex/phase4-windows-beta`, PR #18)  
*Status: Complete and Verified on Installed Windows Application; Awaiting Explicit Founder Authorization before Merge/Publishing.*

Per the strict policy governing Phase 4, the release decision remains **NO-GO** until explicit founder authorization is given to merge PR #18, tag the release, and publish production binaries. No merge, tag, release, deploy, or paid external provider calls will occur without explicit founder authorization.

---

## Executive Summary & Functionality Breakdown

To ensure absolute engineering transparency, all functionality is categorized across three distinct verification tiers:
1. **Implemented Functionality**: The production architecture, code, schema, and API contracts deployed to the codebase.
2. **Automated-Tested Functionality**: Headless unit, integration, concurrency, subprocess, and regression tests executed in CI and local test harnesses.
3. **Installed-App-Tested Functionality**: End-to-end user workflows executed exclusively on the real, packaged Windows binary (`Open Marketing OS Setup 0.1.0-beta.2.exe`) installed into the standard user directory on a Windows operating system.

### Verification Matrix

| Area / Feature | Implemented | Automated-Tested | Installed-App-Tested | Evidence & Location |
|---|---|---|---|---|
| **Atomic Generation Lease & Locking** | ✅ `app/services/campaign_flow.py` (`BEGIN IMMEDIATE`, UUID4 token) | ✅ `app/tests/test_phase4_generation_lock.py` (9 tests, dual-connection & OS subprocesses) | ✅ Multi-click guard & duplicate prevention in GUI | `test_phase4_generation_lock.py`, step 4a-4c |
| **Long-Running Request Lease Heartbeat** | ✅ Daemon thread `_start_lease_heartbeat` extending lease every 30s | ✅ Regression test validating >180s protection (simulating 276s+ provider calls) | ✅ Heartbeat active during generation requests | `test_running_provider_request_longer_than_180s_remains_protected` |
| **Owner-Aware Lease Release & Cleanup** | ✅ Lock cleared only if token matches; rejected requests never clear lock | ✅ Regression tests: rejected request lease safety, crash recovery, CAS fencing | ✅ Clean failure without orphaned locks | `test_rejected_request_must_never_release_another_requests_lock` |
| **Pre-Persistence Lease Fencing** | ✅ `app/services/campaign_production.py` (`_verify_db_lock` before save) | ✅ Regression test: expired owner preemption prevents saving batch | ✅ Prevents stale generation writes | `test_expired_owner_cannot_save_batch_if_lease_lost_during_generation` |
| **Real Ready-to-Post Contract** | ✅ Headline/post copy/subject/body cleanly extracted; audit preserved | ✅ `app/tests/test_phase4_deliverables.py` (social, email, ad copy contracts) | ✅ Ready-to-Post Copy Card (24 words · 185 chars) | Step 6a screenshot (`05_deliverable_ready_card_and_audit.png`) |
| **Audit & Provenance Separation** | ✅ Persisted facts, citations, recommendations separated under audit heading | ✅ Regression test asserting structured separation in markdown | ✅ Expandable `<details>` audit panel in GUI | Step 6b screenshot (`05_deliverable_ready_card_and_audit.png`) |
| **Non-Fallback Rule (No Suggestion Promotion)** | ✅ Prompt & parser fail closed; suggestions never promoted into final copy | ✅ Regression tests with incomplete & malformed responses | ✅ Clean validation failure rather than slop fallback | `test_extract_deliverable_parts_no_suggestion_fallback` |
| **Deliverable Editing & Status Progression** | ✅ Revision tracking with `provenance_json`, `save revision`, `in review`, `approved` | ✅ Frontend tests (`test:dev032-deliverables`), backend CAS tests | ✅ UI edit, save, transition to review, internal approval | Steps 7a-7c screenshots (`06_deliverable_edited.png`, `07_deliverable_approved.png`) |
| **Approved Package Export & Native Save Dialog** | ✅ Segregated ZIP export (`deliverables/`, `00-package-summary.md`), unapproved excluded | ✅ `test_approved_package_export_structure`, sidecar API test | ✅ Native Win32 `showSaveDialog` handled for Markdown & ZIP | Steps 8a-8c screenshot (`08_native_save_and_export.png`) |
| **Multi-Workspace Isolation** | ✅ Client & business project scoping (`starter`, client project UUIDs) | ✅ Scoped API regression tests (DEV-032, DEV-044) | ✅ GUI creation of Alpha Bakery & Beta Plumbing; zero leak; 404 on cross access | Steps 3a-3c, 9a-9b screenshot (`02_workspaces_created.png`) |
| **DPAPI Credential Encryption** | ✅ Windows Data Protection API (DPAPI) ciphertext storage | ✅ Windows tests (`test_dpapi.py`) in CI and local suite | ✅ Saved in GUI Settings; persisted across restarts | Step 10a screenshot (`09_credential_configured.png`) |
| **Clean Shutdown & Zero Orphan Processes** | ✅ Graceful teardown of Electron main, renderer, and sidecar pipe | ✅ Process enumeration assertion in acceptance test harness | ✅ 0 orphan processes verified via tasklist | Step 11a (`[]` orphans) |
| **Restart & State Persistence** | ✅ SQLite persistence in `%LOCALAPPDATA%\OpenMarketingOS\data\marketing.db` | ✅ Persistence regression suite | ✅ Projects, campaign, and approved deliverables intact on relaunch | Steps 11b-11d screenshot (`10_restart_persisted.png`) |
| **Crash Recovery & Sidecar Respawn** | ✅ Electron shell resilience against sidecar crash; Win32 modal retry | ✅ Desktop unit tests (`test/*.test.cjs`) for recovery & bridge | ✅ Sidecar killed; app survives; Win32 `&Retry` clicks; healthy recovery | Steps 12a-12c screenshot (`12_recovered_healthy.png`) |
| **Hard Process Termination** | ✅ Tree termination on unexpected death | ✅ Acceptance harness test | ✅ Immediate clean death with 0 orphan processes | Step 13a (`[]` orphans) |
| **Silent Uninstaller & Data Retention** | ✅ NSIS uninstaller (`Uninstall Open Marketing OS.exe /S`) | ✅ Uninstaller execution and filesystem polling | ✅ Binary folder removed; user database preserved | Steps 14a-14b |

---

## 1. Cross-Process Generation Locking & Pre-Persistence Lease Fencing

### Issue Analysis
1. `STALE_LOCK_TIMEOUT_SECONDS` was hardcoded to 180 seconds, but real provider LLM requests regularly take more than 276 seconds during peak congestion.
2. `generate_from_plan` released the database lock in a `finally` block even when lock acquisition raised an exception (meaning a rejected request cleared another active process's lock).
3. Lock acquisition used a read-modify-write sequence rather than an atomic compare-and-set (CAS) SQLite transaction.
4. An expired owner whose lock timed out during generation could still commit its batch to storage, overwriting a newer owner's deliverables.

### Architectural Solution (Proven SQLite Lease Fencing Pattern)
- **Atomic Compare-and-Set Lease Acquisition (`_acquire_db_lock`)**:
  - Uses `BEGIN IMMEDIATE` transactions in SQLite.
  - Generates a cryptographically random UUID4 `lease_token`.
  - Condition: acquires lease only if `generation_in_progress` is null, or `now > expires_at`.
  - Writes `{pid, turn_id, lease_token, acquired_at, expires_at}` atomically.
  - Rejects concurrent requests across separate processes with HTTP 409 `generation_in_progress`.
- **Background Lease Heartbeat (`_start_lease_heartbeat`)**:
  - A daemon thread extends `expires_at` every 30 seconds while generation is actively executing.
  - Protects requests running longer than 180s (tested beyond 276s).
  - Automatically stops on generation completion or error.
- **Owner-Aware Safe Release (`_release_db_lock`)**:
  - Only releases the database lock if the stored `lease_token` matches the acquiring caller's token.
  - A rejected request or mismatched owner NEVER clears an active lock.
- **Pre-Persistence Generation Lease Fencing (`_verify_db_lock`)**:
  - In `app/services/campaign_production.py`, right before executing `save_generated_batch`, atomic verification checks that the current connection still holds the valid lease token.
  - If the lease expired or was preempted by a recovered owner, the write is aborted with `failure_code="lease_expired"`, strictly protecting newer state from stale overwrites.

### Executed Regression Suite (`app/tests/test_phase4_generation_lock.py`)
All 9 test cases passed in 10.15s:
1. `test_rejected_request_must_never_release_another_requests_lock`: PASSED.
2. `test_running_provider_request_longer_than_180s_remains_protected`: PASSED (heartbeat extends lease for 276s+ calls).
3. `test_crashed_owner_recovers_safely`: PASSED (stale lock reclaimed cleanly after expiration).
4. `test_expired_owner_cannot_overwrite_or_delete_newer_owner`: PASSED (CAS token check prevents stale release).
5. `test_two_separate_connections_prevent_duplicate_calls`: PASSED (second connection gets 409).
6. `test_cross_process_db_lock_prevents_duplicate_generation`: PASSED.
7. `test_expired_owner_cannot_write_state`: PASSED.
8. `test_expired_owner_cannot_save_batch_if_lease_lost_during_generation`: PASSED (fencing prevents stale batch persistence).
9. `test_real_subprocesses_prevent_duplicate_generation`: PASSED (separate OS processes synchronized deterministically).

---

## 2. Real Ready-to-Post Contract & Audit Separation

### Contract Design & Validation
- **Publish-Ready Copy**: Real model prompts and extraction routines in `app/services/campaign_deliverables.py` (`extract_deliverable_parts`) explicitly require and validate ready-to-post copy for social posts, emails (subject + body), and advertising copy (headline + body + CTA).
- **Audit Separation**: Structured provenance (`## Persisted user facts`, `## Retrieved evidence`, `## AI suggestions`, `## Unknown or unsupported information`) is segregated from the final copy.
- **Strict Non-Fallback Rule**: The system NEVER promotes AI suggestions or creative recommendations into publish-ready copy as a fallback. If final copy is missing or malformed, the deliverable validator fails closed.
- **Approved Package Export**: Exported packages (`export_approved_package`) package only approved deliverables into a clean ZIP structure, placing ready-to-post copy at the top of each file and isolating audit trails under `---\n## Audit & Provenance\n`. Unapproved drafts are strictly excluded and summarized in `00-package-summary.md`.

### Executed Regression Suite (`app/tests/test_phase4_deliverables.py`)
All 9 test cases passed in 8.22s:
1. `test_extract_deliverable_parts_with_final_copy`: PASSED.
2. `test_extract_deliverable_parts_no_suggestion_fallback`: PASSED.
3. `test_social_post_ready_to_post_contract`: PASSED.
4. `test_email_subject_and_body_ready_to_post_contract`: PASSED.
5. `test_ad_copy_headline_and_cta_contract`: PASSED.
6. `test_malformed_and_incomplete_model_responses`: PASSED.
7. `test_approved_package_export_structure`: PASSED.
8. `test_cross_process_db_lock_and_recovery`: PASSED.
9. `test_generate_from_plan_returns_elapsed_and_model_status`: PASSED.

---

## 3. Installed Windows Acceptance Evidence

### Execution Context
- **Tested Binary**: Fresh, nonpublishing Windows installer candidate `Open Marketing OS Setup 0.1.0-beta.2.exe` (SHA256: `82e604456fd0cc02c0e02871555beae1f626e245dfca867580fa4cf35c40e2b8`).
- **Test Harness**: `development/acceptance/phase4_acceptance_suite.cjs` driven via Playwright CDP over real Electron and Win32 child window message automation (`development/acceptance/dismiss_dialog.ps1`).
- **Execution Log**: `development/acceptance/phase4_acceptance_results.json`.
- **Screenshots**: Saved in `development/acceptance/phase4-installed-evidence/`.

### Summary of 34 Executed Checks (34 PASSED, 0 FAILED)

| Step | Assertion | Result | Timing / Detail |
|---|---|---|---|
| **1** | Installation verification | **PASS** | `Open Marketing OS.exe`, `Uninstall...exe`, `omos-sidecar.exe` present |
| **2** | Application launch | **PASS** | Main window ready in 7,961ms at `http://127.0.0.1:57962` |
| **2b** | Preload sandbox integrity | **PASS** | `window.omosDesktop` present; `require`, `process`, `module` undefined |
| **2c** | Desktop bridge status | **PASS** | `version: 0.1.0-beta.2`, `edition: openrouter`, `connected: true` |
| **2d** | External network fetch blocked | **PASS** | Renderer fetch to external domain strictly rejected |
| **2e** | Sidecar unauthorized request rejected | **PASS** | HTTP 401 without bearer token |
| **3a** | GUI Create Business Workspace Alpha Bakery | **PASS** | Workspace created via GUI form |
| **3b** | GUI Create Client Workspace Beta Plumbing | **PASS** | Client workspace created via GUI form |
| **3c** | Projects persisted in database | **PASS** | `Alpha=starter`, `Beta=ceff5d67fc5e4bccae04fa8dafc23d0e` in SQLite |
| **4a** | Chat creates campaign proposal card | **PASS** | Deterministic prompt yields campaign proposal card in chat |
| **4b** | Alpha campaign created | **PASS** | Campaign created with ID `310986c7df434e2fe12466b6ffe1e72c` |
| **4c** | Work package proposal renders | **PASS** | Deliverable count, channels, and strategy summary rendered |
| **5a** | Seed realistic deliverables with ready-to-post contract | **PASS** | Deliverables seeded (`d1-b4d6ae988318`, `d2-4ceada762643`) |
| **6a** | Ready-to-Post Copy Card visible with metrics | **PASS** | "READY-TO-POST COPY", 24 words · 185 chars, copy-to-clipboard |
| **6b** | Structured Audit & Provenance Panel separated | **PASS** | `<details>` element with facts, citations, recommendations |
| **7a** | Deliverable edited and saved | **PASS** | Content modified, revision saved with `provenance_json` |
| **7b** | Deliverable transitioned to In review | **PASS** | Workflow status updated to "In review" |
| **7c** | Deliverable approved internally | **PASS** | Workflow status updated to "Approved" |
| **8a** | Export Markdown handled via Native Save dialog | **PASS** | Win32 `showSaveDialog` intercepts and writes markdown file |
| **8b** | Download approved package via Native Save dialog | **PASS** | Win32 `showSaveDialog` intercepts and writes package zip |
| **8c** | Approved package API returns valid zip payload | **PASS** | Valid ZIP header (`PK\x03\x04`) returned by sidecar |
| **9a** | Cross-project isolation: Beta has no Alpha content | **PASS** | Beta campaigns view displays 0 Alpha campaigns |
| **9b** | Cross-project direct access returns HTTP 404 | **PASS** | Cross-project deliverable URL request returns 404 |
| **10a** | DPAPI credential encryption saved in Settings | **PASS** | Settings form encrypted and stored via DPAPI |
| **11a** | Clean shutdown leaves 0 orphan processes | **PASS** | `[]` remaining processes after graceful exit |
| **11b** | Relaunch successful after clean shutdown | **PASS** | App boots and reaches ready state after restart |
| **11c** | Projects persisted across restart | **PASS** | Alpha Bakery and Beta Plumbing intact in database |
| **11d** | Campaign and approved deliverables persisted | **PASS** | Campaign `310986c7` and approved deliverable intact |
| **12a** | Sidecar running before crash test | **PASS** | `omos-sidecar.exe:54596` verified active |
| **12b** | App shell survives sidecar termination | **PASS** | Electron UI stays alive when sidecar is killed (`taskkill /F`) |
| **12c** | Sidecar recovered via Retry; data healthy | **PASS** | Win32 `#32770` dialog `&Retry` clicked, sidecar respawned, DB healthy |
| **13a** | Hard kill of whole app leaves 0 orphans | **PASS** | `[]` remaining processes after process tree kill |
| **14a** | Uninstallation removed application binaries | **PASS** | `Uninstall Open Marketing OS.exe /S` cleanly deleted binary directory |
| **14b** | Uninstallation preserved user data directory | **PASS** | `%LOCALAPPDATA%\OpenMarketingOS\data\marketing.db` retained |

---

## 4. Candidate Build Integrity & Nonpublishing Verification

- **Branch**: `codex/phase4-windows-beta` (PR #18).
- **Candidate Installer**: `Open Marketing OS Setup 0.1.0-beta.2.exe`.
- **SHA256**: `82e604456fd0cc02c0e02871555beae1f626e245dfca867580fa4cf35c40e2b8`.
- **Embedded Build ID**: `eac46fd59c7f6c67c67f605f03d6247a52935689-40605883598-1`.
- **Manifest Location**: `dist/electron/candidate-manifest.json`.
- **Published Status**: `published: false` strictly enforced.
- **Model Weight Bundling**: Strictly 0 model weights or credentials bundled in installer.

---

## 5. Summary & Final Release Recommendation

**Decision: NO-GO for Windows Beta Pilot Release**

- Cross-process generation locking with atomic SQLite compare-and-set transactions, lease heartbeats (>180s protection), owner-aware release, and pre-persistence fencing is fully implemented and proved with 9 regression tests.
- Ready-to-post deliverable contract, structured audit segregation, and the strict non-fallback rule (never promoting AI suggestions into publish-ready copy) are verified with 9 tests.
- Actual installed Windows acceptance testing on the packaged binary passed all 14 steps (34 individual assertions) with zero failures.
- In accordance with the project governance and safety rules, the release status remains **NO-GO** until explicit founder authorization is provided.
