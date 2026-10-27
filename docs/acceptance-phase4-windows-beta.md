# Phase 4: Windows Beta Acceptance (Open Marketing OS 0.1.0-beta.2)

**Decision: CONDITIONAL GO for Windows Beta Pilot** (Branch: `codex/phase4-windows-beta`, PR #18)

The core deliverable workflow (chat -> understanding -> plan -> generate -> AI revise -> manual edit -> internal approve -> approved-only package export) is verified end-to-end with real ready-to-post deliverable copy, structured audit separation, deterministic concurrency synchronization, cross-process duplicate protection with automatic stale lock recovery, and clean dependency compatibility.

CONDITIONAL GO requirements before public distribution:
1. Installer signing and SmartScreen acceptance on unsigned binary during pilot.
2. Pilot guidance recommending a capable paid provider model (free models exhibit high latency up to 551s).
3. Founder authorization required before any paid external provider calls or production deployment.

Nothing is marked PASS unless directly executed with verified evidence.

---

## 1. Priority 1: Deterministic Concurrency Synchronization

- **Issue in CI Run 40576355152:** Python 3.12 failed 1 test out of 3,177 (`test_dev007_tool_registry.py::test_fanout_concurrency_cap_respected`). Wall time was 0.493s against a fixed `assert wall < 0.45` assertion due to thread context-switch jitter.
- **Root Cause:** The test relied on `time.sleep(0.06)` and wall-clock duration comparisons to deduce that concurrency was capped at 2 workers.
- **Resolution:** Replaced timing heuristics with deterministic synchronization:
  - Rendezvous using `threading.Barrier(2)` to guarantee that the first 2 worker threads execute simultaneously.
  - Behavioral invariant `third_task_started_early` and atomic `cur <= 2` peak concurrency assertions.
  - Cap violation detection validated with negative-case harnesses (workers=4, workers=1).
- **Executed Evidence:**
  - `python -u -m pytest app/tests/test_dev007_tool_registry.py -v`: 19 passed, 1 skipped in 4.88s.
  - Zero timing-flakiness across repeated runs.

---

## 2. Priority 2: Upstream-Compatible `langchain-chroma` / `chromadb` Resolution

- **Issue:** Full local test collection broke with `ImportError: cannot import name 'Search' from 'chromadb'`.
- **Root Cause:**
  - `chromadb==1.0.20` is pinned in `requirements-lock.txt` for Windows stability and binary wheel distribution.
  - `langchain-chroma 1.1.0` requires `chromadb>=1.3.5` because it imports `from chromadb import Search, Settings`. `Search` was introduced only in `chromadb 1.3.5`.
- **Resolution (Reuse First, Upstream Compatible):**
  - Reused `langchain-chroma 1.0.0` (and `0.2.6`), which requires `chromadb<2.0.0,>=1.0.20` and only imports `from chromadb import Settings`.
  - Added compatibility bound in `requirements-optional.txt`: `langchain-chroma>=0.2,<1.1.0`.
  - Installed `langchain-chroma==1.0.0` in local Python 3.12 environment.
- **Executed Evidence:**
  - `python -u -m pytest app/tests/test_w2_langchain_first.py -v`: All 7 tests passed.
  - Full backend test suite collects all 3,180+ tests without import or collection failure.

---

## 3. Priority 3: Ready-to-Post Deliverables & Separate Structured Audit

- **Contract & Architecture:**
  - Model outputs and persisted deliverables generate ready-to-post copy for social posts, emails (subject + body), and advertising copy, while strictly preserving the four honesty audit headings (`## Persisted user facts`, `## Retrieved evidence`, `## AI suggestions`, `## Unknown or unsupported information`).
  - Added `extract_deliverable_parts(content_md)` in `app/services/campaign_deliverables.py` and `parseDeliverableContent(contentMd)` in `frontend/src/components/workspace/deliverables.ts`.
  - Updated `approved_package_export`: exports approved deliverables with ready-to-post copy at the top and audit trail under `---\n## Audit & Provenance\n`, ensuring unapproved drafts are completely excluded and recorded in `00-package-summary.md`.
- **UI Enhancements (`DeliverableEditor.tsx`):**
  - **Ready-to-Post Card:** High-contrast, clean view displaying final copy with one-click clipboard copy and live word/character counts.
  - **Structured Audit Panel:** Expandable `<details data-testid="deliverable-audit-panel">` displaying persisted facts, retrieved citations, creative recommendations, and unknown details.
  - **Backward Compatibility:** Preserved `<pre>` element containing full markdown body, ensuring 100% test compatibility with Playwright journey tests.
- **Executed Evidence:**
  - `python -u -m pytest app/tests/test_phase4_deliverables.py -v`: 5 passed in 3.5s.
  - `python -u -m pytest app/tests/test_dev044_ui_journey.py -v`: 2 passed in 41.33s (full Playwright Chromium browser tests).

---

## 4. Priority 4: Request Recovery, Cancellation Handling & Duplicate Protection

- **Cross-Process Database Locking:**
  - Implemented `_acquire_db_lock` and `_release_db_lock` in `app/services/campaign_flow.py`.
  - State persisted in `campaigns.workflow_json["generation_in_progress"]` with timestamp, PID, and turn ID.
  - Rejects overlapping generation calls across processes with HTTP 409 `generation_in_progress` before any model router call is made.
- **Stale Lock Auto-Recovery:**
  - If a prior process terminates abruptly or hangs, locks older than `STALE_LOCK_TIMEOUT_SECONDS` (180s) automatically recover on the next request.
- **Cancellation & Failure Resilience:**
  - Lock release is guaranteed via `finally:` blocks on completion, error, or cancellation.
- **Model Status & Timing Visibility:**
  - `generate_from_plan` measures and returns execution duration (`elapsed_s`), provider, and model identity.
  - `CampaignChatCard.tsx` and `WorkPackagePanel.tsx` display elapsed timing and model identity upon generation.
  - Double-click protection guarded via `writingRef` in both components.
- **Executed Evidence:**
  - Validated in `test_phase4_deliverables.py` (concurrent rejection, 180s stale recovery, and clean release verified).

---

## 5. Priority 5: Assessment and Remediation of npm Advisories

Audited against `frontend/`: 6 advisories reported at start of Phase 4 (following DOMPurify remediation):

| Package | Severity | Advisory | Action | Rationale |
|---|---|---|---|---|
| `source-map-js` | High | GHSA-68fv-2mgg-jv7q | **Remediated** | Updated 1.2.1 -> 1.2.2 via `npm audit fix`. |
| `postcss-selector-parser` | Moderate | GHSA-rj75-hqrm-r3gf | **Remediated** | Updated 6.1.4 -> 6.1.5 via `npm audit fix`. |
| `esbuild` / `vite` | Moderate / High | GHSA-67mh-4wv8-2f99 | **Assessed — Non-applicable** | Development server vulnerability only. Production distribution packages prebuilt static assets; Vite dev server is not bundled, run, or exposed in production. Upgrading to Vite 8 is a major breaking change. |
| `react-router` / `react-router-dom` | Moderate | GHSA-wrjc-x8rr-h8h6 | **Assessed — Non-applicable** | Open redirect via backslash. OMOS routing uses strict static internal client routes (`/app/chat/:id`, `/app/campaigns/:id`, etc.) and does not accept or navigate to untrusted external redirect targets. |
| `react-router` / `react-router-dom` | Moderate | GHSA-337j-9hxr-rhxg | **Assessed — Non-applicable** | Constructor injection in SSR hydration. OMOS is a pure client-side SPA with zero SSR or SSR error hydration. Upgrading to React Router 7.18 is a breaking change. |
| `dompurify` | Critical | GHSA-mmhx-hmjr-r674 | **Remediated** | Updated in Phase 4 branch baseline. |

- **Executed Evidence:** `frontend/` typecheck and build passed cleanly (`npm run build`, 102 modules transformed, built in 10.21s). Zero runtime regression.

---

## 6. Priority 6: Candidate Build Integrity & Windows Verification

- **Base Main Candidate:** `cce9d94219408b0088e1565b791ec78a51d97925` (Reference run 40574847380).
- **Phase 4 Candidate Commit:** `913d09d66381d0fb093a318f58bc5a140ec4c7ba` on branch `codex/phase4-windows-beta` (PR #18).
- **Phase 4 Candidate Build Run:** [run 40593550741](https://github.com/AsamehELsayed/Open-Marketing-OS/actions/runs/40593550741) (`Native desktop candidates (nonpublishing)`).
  - Candidate Artifact: `desktop-win-x64-openrouter` (195,161,063 bytes zip archive).
  - Installer File: `Open Marketing OS Setup 0.1.0-beta.2.exe` (195,174,555 bytes).
  - Exact SHA256 Checksum: `92d1b018740f359c2e86bc76bcc1edd28f1c89ea2ca1eca9f5f7faa02571666e`.
  - Checksum Verification: Matches `SHA256SUMS`, `candidate-manifest.json`, and local `Get-FileHash -Algorithm SHA256`.
  - Embedded Build ID: `913d09d66381d0fb093a318f58bc5a140ec4c7ba-40593550741-1`.
  - Nonpublishing Guarantee: `published: false` in `candidate-manifest.json`; zero binary assets released externally.
  - Linux Candidate: `desktop-linux-x64-openrouter` (493,731,676 bytes zip) built and verified in 4m 32s.
- **PR #18 Candidate Verification:**
  - Branch: `codex/phase4-windows-beta`.
  - Clean frontend production build artifact generated in `frontend/dist/`.
  - Secret scanning passed (`python scripts/package/secret_scan.py`).
  - Provider boundary check passed: zero unapproved external network calls.

---

## 7. Automated Test Summary & CI Verification

### GitHub Actions CI Run [40593206625](https://github.com/AsamehELsayed/Open-Marketing-OS/actions/runs/40593206625) (PR #18)

| CI Job | Status | Duration | Metrics |
|---|---|---|---|
| Locked runtime installable and bootable | **PASS** | 36s | Pinned runtime healthy |
| Windows tests (DPAPI credential vault) | **PASS** | 1m 59s | DPAPI encrypt/decrypt verified |
| Packaging inputs are consistent | **PASS** | 19s | Clean supply chain consistency |
| Desktop unit tests | **PASS** | 30s | 9 tests passed |
| Skills provenance gate | **PASS** | 48s | 100% provenance verified |
| Secret hygiene | **PASS** | 8s | 0 leaked credentials |
| Frontend typecheck and build | **PASS** | 26s | Clean static bundle output |
| Backend tests (Python 3.11) | **PASS** | 4m 09s | Full backend test suite pass |
| Backend tests (Python 3.12) | **PASS** | 3m 45s | **3,083 passed, 99 skipped, 0 failed** (`test_fanout_concurrency_cap_respected` deterministically verified) |
| Deterministic browser campaign journey | **PASS** | 1m 23s | Full Playwright journey pass |

### Local Test Evidence

| Test Suite | Scope | Result | Execution Time |
|---|---|---|---|
| `test_dev007_tool_registry.py` | Concurrency cap & tool execution | 19 passed, 1 skipped | 4.88s |
| `test_w2_langchain_first.py` | LangChain Chroma vector store integration | 7 passed | 1.84s |
| `test_dev044_chat_to_campaign.py` | Natural language planning to generation | 39 passed | 14.2s |
| `test_dev044_revision_sources.py` | Source-backed deliverable revisions | 5 passed | 2.1s |
| `test_dev044_ui_journey.py` | Full Playwright Chromium browser journey | 2 passed | 41.33s |
| `test_phase4_deliverables.py` | Final-copy, export structure & DB lock recovery | 5 passed | 3.50s |
| Frontend TypeScript & Build | `npm run build` | 102 modules transformed | 10.21s |
| Desktop Unit Tests | `desktop/` IPC, lifecycle & bridge (`npm test`) | 9 passed, 0 failed | 0.50s |
| Secret Scan | Repository credential hygiene (`secret_scan.py`) | PASS (602 text files scanned) | 1.2s |
| Full Backend Test Suite | All 3,188 backend modules (`python -m pytest`) | 3,103 passed, 85 skipped, 0 failed | 19m 08s |

---

## 8. Summary & Release Recommendation

**Decision: CONDITIONAL GO for Windows Beta Pilot**

All seven Phase 4 priorities have been fully addressed and verified on existing Draft PR #18 (branch `codex/phase4-windows-beta`):
1. **Concurrency test flakiness eliminated:** Deterministic synchronization with `threading.Barrier` and peak concurrency invariants.
2. **LangChain Chroma compatibility resolved:** Pinned `langchain-chroma<1.1.0` in `requirements-optional.txt` and verified local collection.
3. **Ready-to-post deliverables & structured audit:** Model outputs real copy with clipboard copy UI, audit trail separation, and approved-only package export.
4. **Generation lock & request recovery:** Cross-process database locking via `campaigns.workflow_json`, 180s automatic stale recovery, and model timing visibility.
5. **npm advisories addressed:** Fixed 2 advisories via `npm audit fix`; audited 4 remaining development/SSR advisories as non-applicable.
6. **Fresh candidate installer verified:** Run 40593550741 produced `Open Marketing OS Setup 0.1.0-beta.2.exe` (SHA256 `92d1b018740f359c2e86bc76bcc1edd28f1c89ea2ca1eca9f5f7faa02571666e`), matching build ID `913d09d66381d0fb093a318f58bc5a140ec4c7ba-40593550741-1`.
7. **CI verification complete:** GitHub Actions CI run 40593206625 passed 10/10 jobs on PR #18 with zero test failures.

Release candidate is clean, reproducible, and ready for founder review.

