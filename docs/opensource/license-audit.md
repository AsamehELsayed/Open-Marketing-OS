# DEV-028 License and Attribution Audit — Candidate Snapshot

**Scope:** W1's 784-path owner-neutral candidate, updated only with documentation and notice outputs. Evidence snapshot: 2026-10-06. This is an attribution audit, not legal advice or complete release clearance. No final frozen DEV-028 payload has been built.

## Findings

| Area | Evidence | Status |
|---|---|---|
| Project license | Candidate `LICENSE` is Apache License 2.0; `NOTICE` identifies Open Marketing OS contributors and the project license. | **PASS** for source project license identity. |
| Local / llama.cpp | `config/local_models.json` pins llama.cpp b11429, commit `d81235049384534c167caea52b85a694f6103d14`, archive SHA-256 `1283323272b04cd07905816a597a0da810918102de958f4ff6f7bbaa70ed2efe`, MIT. DEV-029 staged runtime has MIT `LICENSE` and `LICENSE-LLVM-OpenMP`; exact MIT text is supplied at `scripts/package/notices/llama.cpp-LICENSE`. | **PASS** for catalog identity and staged source notices. Final payload preservation is pending. |
| Local model | Catalog pins Qwen2.5-7B-Instruct Q4_K_M, `Qwen/Qwen2.5-7B-Instruct-GGUF`, revision `293ca9a10157b0e5fc5cb32af8b636a88bede891`, and declares Apache-2.0. Model weights are downloaded only after user initiation and are absent from source candidate/installer. | **PASS** for source catalog disclosure and exclusion scope; no weight redistribution is authorized by this audit. |
| Python lock | `requirements-lock.txt` pins 114 packages. `LICENSE-INVENTORY.json/.md` and `THIRD-PARTY-NOTICES` record each exact lock version and declared license. Exact-version PyPI release metadata URLs replace mismatched/missing host metadata; lock-matching entries retain exact-version installed metadata provenance. | **BLOCKED** pending actual frozen payload membership and required package license/copyright text checks. `pyreadline3==3.5.6` metadata says BSD but does not establish an SPDX variant; confirm the locked wheel's own notice in payload QA. |
| Frontend | `frontend/package-lock.json` contains 11 production entries, including React ecosystem, Marked and DOMPurify 3.4.15 `(MPL-2.0 OR Apache-2.0)`. | **BLOCKED** pending emitted bundle membership and required retained package attributions/license files. |
| htmx | Candidate includes `app/static/htmx.min.js` version 2.0.4. Upstream v2.0.4 license is 0BSD; exact text is in `LICENSES/htmx-0BSD.txt`, source URL in `THIRD-PARTY-NOTICES`. | **PASS** for source notice; final app payload notice inclusion is pending. |
| Vendored skills | `.agents/skills/**` and `.agents/LICENSE` are included. Marketing Skills v2.11.1, commit `5b2c0007766c6a1cf1d53fd8fc73e979e0821022`, is MIT; prior provenance evidence reports identity after line-ending normalization. | **PASS** subject to retaining `.agents/LICENSE`. Keep `.agents/product-marketing.md` excluded. |
| Fonts and private creative | Cairo/Zain font binaries and private client imagery are absent from W1 candidate allowlist. | **PASS** as excluded; do not add without documented rights. |
| Inno Setup | DEV-029 compiler output shows “Non-commercial use only.” Installed vendor license text permits commercial applications; the vendor commercial licensing page requests commercial users to purchase a commercial license; its FAQ says purchase is not strictly required and may wait until the installers are production-ready. Sources: `https://jrsoftware.org/isorder.php`, `https://jrsoftware.org/isinfo.php`. | **NEEDS-FOUNDER-CONFIRMATION / PRE-PRODUCTION.** No legal conclusion or production-confirmed claim. |

## Integrated source files

- `LICENSE`: Apache-2.0 project license.
- `NOTICE`: corrected Local/llama.cpp statements, model disclosure, 0BSD htmx reference, and payload limitations.
- `THIRD-PARTY-NOTICES`: source-lock-derived Python/frontend listing and selected-component notices.
- `LICENSE-INVENTORY.json` and `LICENSE-INVENTORY.md`: exact lock versions, declared license metadata and provenance.
- `LICENSES/htmx-0BSD.txt`: exact upstream htmx v2.0.4 0BSD text.
- `scripts/package/notices/llama.cpp-LICENSE`: exact staged llama.cpp MIT notice bytes required by the packaging script.
- `runtime/licenses/llama.cpp/LICENSE` and `LICENSE-LLVM-OpenMP`: staged runtime notice copies for traceability.

## Release disposition

**Source-lock metadata inventory: PASS with one license-variant detail pending (`pyreadline3`). Final payload attribution: BLOCKED.** The final DEV-028 frozen Python set, emitted frontend bundle, retained third-party notice files, and installer notice presence must be checked after those artifacts exist. Do not claim production-ready licensing for Inno Setup until the founder commercial-use/purchase decision is recorded. Current installer state is **PRE-PRODUCTION**.
