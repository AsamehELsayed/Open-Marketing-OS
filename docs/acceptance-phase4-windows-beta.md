# Phase 4: Windows Beta Acceptance (Open Marketing OS 0.1.0-beta.2)

**Final Review Decisions:**
- **PR #18 MERGE DECISION: GO** (Architecture, lease fencing, export hardening, and 38-step installed acceptance suite verified).
- **WINDOWS BETA PILOT RELEASE DECISION: NO-GO** (Awaiting explicit founder authorization before tagging, publishing binaries, or external distribution).

Per the strict policy governing Open Marketing OS Phase 4, the pilot release decision remains **NO-GO** until explicit founder authorization is given to tag the release, publish production binaries, and launch pilot distribution. No merge, tag, release, deploy, or paid external provider calls will occur without explicit founder authorization.

---

## 1. Candidate Build Lineage & Artifact Integrity

To ensure complete build reproducibility and prevent artifact drift, every candidate artifact is tracked with its git commit, build identifier, file size, and SHA256 digest:

| Artifact Attribute | Commit `eac46fd5` Candidate | Commit `1faa5e71` Base | Rebuilt Final Candidate (`5a478cb4-final`) |
|---|---|---|---|
| **Git Commit** | `eac46fd59c7f6c67c67f605f03d6247a52935689` | `1faa5e71508a0f7626c1546a1c0f33bd52c7540e` | `5a478cb4b608f134f93bb21f1ed894b31f342048` |
| **Artifact Name** | `Open Marketing OS Setup 0.1.0-beta.2.exe` | `Open Marketing OS Setup 0.1.0-beta.2.exe` | `Open Marketing OS Setup 0.1.0-beta.2.exe` |
| **Artifact Path** | `dist/i1-candidates/windows/` | `dist/electron/` | `dist/electron/` (staged at `C:\omos-phase4\installer.exe`) |
| **File Size** | 195,178,470 bytes | 225,163,663 bytes | 225,732,061 bytes |
| **SHA256 Digest** | `82e604456fd0cc02c0e02871555beae1f626e245dfca867580fa4cf35c40e2b8` | `8f7b423c877ea7fad0dd1574f8a7631475778d1498d948484aca30fc6941c180` | `13f65a93aceb7d7ebaafccd76e978829242d2b6f20dc236b9ebff64839a51950` |
| **Packaging Spec** | PyInstaller + NSIS | PyInstaller + NSIS | PyInstaller 6.22.3 + electron-builder 26.15.3 NSIS |
| **Model Weights** | 0 bundled (clean) | 0 bundled (clean) | 0 bundled (clean) |
| **Published Status** | `published: false` | `published: false` | `published: false` |

---

## 2. Review Concerns & Technical Hardening

### Concern 1: Lease Fencing & SQLite Transaction Safety
- **Lease Expiration Without Replacement**: If a generation request exceeds its lease timeout without renewal and no new owner has acquired the lock, persistence is strictly fenced. Inside SQLite `BEGIN IMMEDIATE`, `_verify_active_lease_in_tx` re-verifies that `generation_in_progress` is not expired. If expired, `save_generated_batch` aborts with `DeliverableLeaseError` / HTTP 409 (`lease_expired`).
- **Owner Changes Between Validation and Persistence**: If Owner 1 validates deliverables but Owner 2 reclaims the expired lease prior to persistence, Owner 1's commit is aborted. The transaction rolls back cleanly with 0 persisted deliverables, and Owner 2's active lease remains uncorrupted.
- **Heartbeat Failure Fencing**: A background daemon thread (`_start_lease_heartbeat`) extends the lease every 30s. If the heartbeat fails or is blocked, the lease expires naturally, and subsequent persistence attempts are fenced and rejected.
- **SQLite Concurrency & Busy Retries**: Wrapped `_atomic(conn)` with retry logic for `sqlite3.OperationalError: database is locked / busy` (up to 10 attempts with exponential backoff) to prevent deadlocks during high-concurrency turns.
- **Automated Tests**: 12 dedicated tests in `app/tests/test_phase4_generation_lock.py` (all passed in 5.29s).

### Concern 2: Approved-Only Export Path & Copy Validation Hardening
- **Strict Final Copy Enforcement**: `extract_deliverable_parts` no longer falls back to unlabelled text when audit headings are present. It requires a dedicated `## Final copy` section (or heading-less copy with no audit headers), enforces all 4 audit sections (`## Persisted user facts`, `## Retrieved evidence`, `## Recommendations`, `## Missing information`), and enforces channel-specific contracts (e.g. Email `Subject:` + `Body:`).
- **Fail-Closed Editorial Approval**: In `campaign_deliverables.transition(..., target_status="APPROVED")`, deliverables with missing or malformed `final_copy` cannot transition to `APPROVED` and raise `DeliverableValidationError`.
- **Elimination of Legacy Export Fallback**: In `approved_package_export`, the legacy fallback `export_text = rev["content_md"]` was completely removed. Any deliverable missing valid `final_copy` is rejected and recorded in `00-package-summary.md` under:
  `- NOT INCLUDED (invalid or missing final copy - <reason>)`.
- **Automated Tests**: 11 dedicated tests in `app/tests/test_phase4_deliverables.py` (all passed).

### Concern 3: Real End-to-End Generation in Installed Acceptance Suite
- Replaced previous direct database seeding with an active, deterministic loopback mock server (`http://127.0.0.1:9090`).
- The real installed Electron app configures the OpenRouter endpoint to the loopback server, invokes campaign generation, receives and parses 3 deliverables (`strategy_brief`, `social_post`, `ad_copy`), validates ready-to-post copy and audit sections, executes an AI revision to Version 2, transitions the deliverable to "In review" and "Approved", and exports both Markdown and ZIP packages via native Win32 save dialogs.
- Total executed checks: **38 passed, 0 failed**.

---

## 3. Verification Matrix

| Area / Feature | Implemented | Automated-Tested | Installed-App-Tested | Evidence & Location |
|---|---|---|---|---|
| **Atomic Generation Lease & Locking** | ✅ `app/services/campaign_flow.py` (`BEGIN IMMEDIATE`, UUID4 token) | ✅ `app/tests/test_phase4_generation_lock.py` (12 tests, dual-connection & OS subprocesses) | ✅ Multi-click guard & duplicate prevention in GUI | `test_phase4_generation_lock.py`, step 4a-4c |
| **Long-Running Request Lease Heartbeat** | ✅ Daemon thread `_start_lease_heartbeat` extending lease every 30s | ✅ Regression test validating >180s protection (simulating 276s+ provider calls) | ✅ Heartbeat active during generation requests | `test_running_provider_request_longer_than_180s_remains_protected` |
| **Owner-Aware Lease Release & Cleanup** | ✅ Lock cleared only if token matches; rejected requests never clear lock | ✅ Regression tests: rejected request lease safety, crash recovery, CAS fencing | ✅ Clean failure without orphaned locks | `test_rejected_request_must_never_release_another_requests_lock` |
| **Pre-Persistence Lease Fencing** | ✅ `app/services/campaign_production.py` (`_verify_db_lock` & tx verification) | ✅ Regression tests: expired owner without replacement & owner replacement fencing | ✅ Prevents stale generation writes | `test_lease_expiration_without_ownership_replacement_fences_persistence` |
| **Real Ready-to-Post Contract** | ✅ Headline/post copy/subject/body cleanly extracted; audit preserved | ✅ `app/tests/test_phase4_deliverables.py` (social, email, ad copy contracts) | ✅ Ready-to-Post Copy Card (25 words · 192 chars) | Step 6a screenshot (`05_deliverable_ready_card_and_audit.png`) |
| **Audit & Provenance Separation** | ✅ Persisted facts, citations, recommendations separated under audit heading | ✅ Regression test asserting structured separation in markdown | ✅ Expandable `<details>` audit panel in GUI | Step 6b screenshot (`05_deliverable_ready_card_and_audit.png`) |
| **Non-Fallback Rule (No Suggestion Promotion)** | ✅ Prompt & parser fail closed; suggestions never promoted into final copy | ✅ Regression tests with incomplete & malformed responses | ✅ Clean validation failure rather than slop fallback | `test_extract_deliverable_parts_no_suggestion_fallback` |
| **Approved-Only Package Export** | ✅ Segregated ZIP export, malformed/unapproved excluded; 0 legacy fallback | ✅ `test_approved_export_blocks_malformed_or_missing_final_copy_legacy_fallback` | ✅ Native Win32 `showSaveDialog` handled for Markdown & ZIP | Steps 8a-8c screenshot (`08_native_save_and_export.png`) |
| **Live End-to-End Generation & AI Revision** | ✅ Generation pipeline through sidecar + loopback mock server | ✅ Integration tests with `DeterministicCampaignProvider` | ✅ Real installed app generates 3 items, validates, and revises to v2 | Steps 5a-5d (`phase4_acceptance_results.json`) |
| **Multi-Workspace Isolation** | ✅ Client & business project scoping (`starter`, client project UUIDs) | ✅ Scoped API regression tests (DEV-032, DEV-044) | ✅ GUI creation of Alpha Bakery & Beta Plumbing; zero leak; 404 on cross access | Steps 3a-3c, 9a-9b screenshot (`02_workspaces_created.png`) |
| **DPAPI Credential Encryption** | ✅ Windows Data Protection API (DPAPI) ciphertext storage | ✅ Windows tests (`test_dpapi.py`) in CI and local suite | ✅ Saved in GUI Settings; persisted across restarts | Step 10a screenshot (`09_credential_configured.png`) |
| **Clean Shutdown & Zero Orphan Processes** | ✅ Graceful teardown of Electron main, renderer, and sidecar pipe | ✅ Process enumeration assertion in acceptance test harness | ✅ 0 orphan processes verified via tasklist | Step 11a (`[]` orphans) |
| **Restart & State Persistence** | ✅ SQLite persistence in `%LOCALAPPDATA%\OpenMarketingOS\data\marketing.db` | ✅ Persistence regression suite | ✅ Projects, campaign, and approved deliverables intact on relaunch | Steps 11b-11d screenshot (`10_restart_persisted.png`) |
| **Crash Recovery & Sidecar Respawn** | ✅ Electron shell resilience against sidecar crash; Win32 modal retry | ✅ Desktop unit tests (`test/*.test.cjs`) for recovery & bridge | ✅ Sidecar killed; app survives; Win32 `&Retry` clicks; healthy recovery | Steps 12a-12c screenshot (`12_recovered_healthy.png`) |
| **Hard Process Termination** | ✅ Tree termination on unexpected death | ✅ Acceptance harness test | ✅ Immediate clean death with 0 orphan processes | Step 13a (`[]` orphans) |
| **Silent Uninstaller & Data Retention** | ✅ NSIS uninstaller (`Uninstall Open Marketing OS.exe /NCRC /S`) | ✅ Uninstaller execution and filesystem polling | ✅ Binary folder removed; user database preserved | Steps 14a-14b |

---

## 4. Executed Installed Windows Acceptance Suite Evidence (38 PASSED, 0 FAILED)

Execution ran against the rebuilt candidate artifact `Open Marketing OS Setup 0.1.0-beta.2.exe` (`13f65a93aceb7d7ebaafccd76e978829242d2b6f20dc236b9ebff64839a51950`, 225,732,061 bytes):

```text
=== Open Marketing OS Phase 4 Windows Acceptance Suite ===
PASS | 1. Installation verification | EXE=true, Uninstaller=true, Sidecar=true
PASS | 2. Application launch | Window ready in 3107ms at http://127.0.0.1:59617
PASS | 2b. Preload sandbox integrity | {"bridge":"object","req":"undefined","proc":"undefined","mod":"undefined"}
PASS | 2c. Desktop bridge status | {"version":"0.1.0-beta.2","edition":"openrouter","connected":true}
PASS | 2d. External network fetch blocked
PASS | 2e. Sidecar unauthorized request rejected | HTTP 401
PASS | 3a. GUI Create Business Workspace Alpha Bakery
PASS | 3b. GUI Create Client Workspace Beta Plumbing
PASS | 3c. Projects persisted in database | Alpha=starter, Beta=764b32b12b0d4908989e97e4f3cc9b02
PASS | 4a. Chat creates campaign proposal card
PASS | 4b. Alpha campaign created | ID=cc6c3dddc6411a7324471b9ba7adffb5
PASS | 4c. Work package proposal renders
PASS | 5a. Configure loopback OpenRouter provider
PASS | 5a2. Connect OpenRouter credential | {"target":"openrouter","status":"connected","message":"Saved securely in Credential Vault."}
PASS | 5b. End-to-end campaign generation executed | {"created":["del_dfbe43f579f3f1ce8e1ca81d4a65b0d3","del_3b081b05834ad68e120c95dc8d18399f","del_28d6961093075b476db6b18ce23dd6f3"],"titles":["Weekend Sourdough Promotion Strategy","Weekend Sourdough Feature","Morning Loaves Email"],"provider":"openrouter","model":"openrouter/auto","elapsed_s":1.23}
PASS | 5c. Generated deliverables validated with final copy and audit sections | Count=3
PASS | 5d. Deliverable AI revision executed and validated | Version=2
PASS | 6a. Ready-to-Post Copy Card visible with metrics | READY-TO-POST COPY: 25 words · 192 chars
PASS | 6b. Structured Audit & Provenance Panel separated
PASS | 7a. Deliverable edited and saved
PASS | 7b. Deliverable transitioned to In review
PASS | 7c. Deliverable approved internally
PASS | 8a. Export Markdown handled via Native Save dialog
PASS | 8b. Download approved package handled via Native Save dialog
PASS | 8c. Approved package API returns valid zip payload
PASS | 9a. Cross-project isolation: Beta campaigns page has no Alpha content
PASS | 9b. Cross-project direct access returns HTTP 404
PASS | 10a. DPAPI credential encryption saved in Settings
PASS | 11a. Clean shutdown leaves 0 orphan processes | []
PASS | 11b. Relaunch successful after clean shutdown
PASS | 11c. Projects persisted across restart
PASS | 11d. Campaign and approved deliverables persisted
PASS | 12a. Sidecar running before crash test | ["omos-sidecar.exe:42940"]
PASS | 12b. App shell survives sidecar termination | ["Open Marketing OS.exe:41088","Open Marketing OS.exe:28276","Open Marketing OS.exe:2568","Open Marketing OS.exe:36296"]
PASS | 12c. Sidecar recovered via Retry; data healthy
PASS | 13a. Hard kill of whole app leaves 0 orphans | []
PASS | 14a. Uninstallation removed application binaries
PASS | 14b. Uninstallation preserved user data directory
========================================
ACCEPTANCE SUMMARY: 38 PASSED, 0 FAILED
========================================
```

### GitHub Actions Remote Verification (10/10 PASS)

On PR #18 head commit `f9d77111e90be9aa2814449aaec5b879088d95ad` ([Run 41172695909](https://github.com/AsamehELsayed/Open-Marketing-OS/actions/runs/41172695909)):
- **Backend tests (Python 3.11)**: PASS (4m 12s)
- **Backend tests (Python 3.12)**: PASS (3m 48s)
- **Desktop unit tests**: PASS (30s)
- **Deterministic browser campaign journey**: PASS (1m 28s)
- **Frontend typecheck and build**: PASS (22s)
- **Locked runtime is installable and bootable**: PASS (41s)
- **Packaging inputs are consistent**: PASS (13s)
- **Secret hygiene**: PASS (11s)
- **Skills provenance gate**: PASS (47s)
- **Windows tests (DPAPI credential vault)**: PASS (2m 07s)

---

## 5. Remaining Known Limitations & Uninstaller Upstream Investigation

1. **NSIS Uninstaller Integrity Check (`/NCRC` Flag & Upstream Issue #10258 / PR #10262)**:
   - **Root Cause Analysis**: In electron-builder `26.15.3`, uninstaller generation executes the stub installer via `RunAsInvoker` to generate the uninstaller binary (`WriteUninstaller`). Because the uninstaller binary does not have its PE checksum / CRC updated during this extraction path, NSIS's built-in startup integrity check detects a CRC mismatch.
   - **Interactive Behavior**: Running `Uninstall Open Marketing OS.exe` directly opens a Windows message dialog titled `NSIS Error` ("Installer integrity check has failed").
   - **Silent Behavior**: Running `Uninstall Open Marketing OS.exe /S` aborts immediately with exit code `2` (the standard NSIS silent verification abort).
   - **Workaround Verification**: Running with `Uninstall Open Marketing OS.exe /NCRC /S` cleanly bypasses the integrity check, deletes all installed application binaries in 5 seconds, and preserves user data in `%LOCALAPPDATA%\OpenMarketingOS\data\marketing.db`.
   - **Upstream Fix**: electron-builder upstream PR #10262 (`ba65cf6`) resolves this by patching the uninstaller icon during extraction and verifying its CRC checksum before shipping.
   - **Policy**: `/NCRC` disables CRC integrity checking and is strictly treated as an acceptance/test workaround, NOT a permanent public release solution. The release remains blocked on upgrading electron-builder to a release incorporating upstream PR #10262.
2. **Zero Bundled Model Weights**:
   The installer bundles zero LLM weights by design (225 MB installer size). An active OpenRouter API key, custom OpenAI-compatible endpoint, or local Ollama instance is required for live generation in production.
3. **Loopback Provider in Acceptance Suite**:
   The end-to-end acceptance suite exercises the complete Electron -> sidecar -> provider client -> parsing -> persistence pipeline via a deterministic local HTTP mock server (`127.0.0.1:9090`) to prevent paid external API calls or network flakiness during acceptance testing.

---

## 6. Final Decisions

- **MERGE PR #18 DECISION: GO**
  - All review concerns have been fully addressed, verified, and hardened with regression tests.
  - SQLite compare-and-set transactions and lease fencing are verified against stale commits and heartbeat failures (12 tests in `test_phase4_generation_lock.py`).
  - The approved-only export path is hardened with zero fallback to legacy or malformed drafts (11 tests in `test_phase4_deliverables.py`).
  - The installed application acceptance suite on Windows passed 38/38 checks with 0 failures on the candidate build.
  - Code changes remain strictly bounded and preserve documentation integrity.

- **WINDOWS PILOT RELEASE DECISION: NO-GO**
  - The release candidate uninstaller suffers from the upstream electron-builder NSIS integrity bug (PR #10262 / issue #10258), causing interactive `NSIS Error` dialogs unless `/NCRC` is supplied.
  - Per the strict Phase 4 release governance policy, public distribution remains **NO-GO** pending upstream electron-builder fix integration and explicit founder authorization to tag and publish production binaries.
