# DEV-028 License and Attribution Audit — Candidate Snapshot

**Scope:** DEV-028 source and packaging candidate. Evidence snapshot: 2026-10-06. This is an attribution audit, not legal advice or complete release clearance. W6 found missing recipient notices in the frozen installer; this candidate adds deterministic payload staging, and the remediated installers still require build and independent payload verification.

## Findings

| Area | Evidence | Status |
|---|---|---|
| Project license | Candidate `LICENSE` is Apache License 2.0; `NOTICE` identifies Open Marketing OS contributors and the project license. | **PASS** for source project license identity. |
| Local / llama.cpp | `config/local_models.json` pins llama.cpp b11429, commit `d81235049384534c167caea52b85a694f6103d14`, archive SHA-256 `1283323272b04cd07905816a597a0da810918102de958f4ff6f7bbaa70ed2efe`, MIT. DEV-029 staged runtime has MIT `LICENSE` and `LICENSE-LLVM-OpenMP`; exact MIT text is supplied at `scripts/package/notices/llama.cpp-LICENSE`. | **PASS** for identity and source notices. Inno copies both runtime notices only under the Local profile's runtime destination; OpenRouter excludes the runtime directory. Verify remediated installer contents after build. |
| Local model | Catalog pins Qwen2.5-7B-Instruct Q4_K_M, `Qwen/Qwen2.5-7B-Instruct-GGUF`, revision `293ca9a10157b0e5fc5cb32af8b636a88bede891`, and declares Apache-2.0. Model weights are downloaded only after user initiation and are absent from source candidate/installer. | **PASS** for source catalog disclosure and exclusion scope; no weight redistribution is authorized by this audit. |
| Python lock | `requirements-lock.txt` pins 114 packages. `LICENSE-INVENTORY.json/.md` and `THIRD-PARTY-NOTICES` record each exact lock version and declared license. The new packaging stage copies each locked distribution's exact license files (four source-distribution gaps have version-pinned notice/license fallbacks) and records the copied paths in `THIRD-PARTY-LICENSES/manifest.json`. | **SOURCE REMEDIATION IMPLEMENTED; installer verification pending.** `pyreadline3==3.5.6` exact wheel notice is retained; the vendor text calls it BSD-type without naming a narrower SPDX variant. |
| Frontend | `frontend/package-lock.json` contains 11 production entries, including React ecosystem, Marked and DOMPurify 3.4.15 `(MPL-2.0 OR Apache-2.0)`. The packaging stage copies exact package license files for the 11 production lock entries and records them in the manifest. | **SOURCE REMEDIATION IMPLEMENTED; installer verification pending.** |
| htmx | Candidate includes `app/static/htmx.min.js` version 2.0.4. Upstream v2.0.4 license is 0BSD; exact text is in `LICENSES/htmx-0BSD.txt`, source URL in `THIRD-PARTY-NOTICES`. The staging step copies it into the install payload. | **SOURCE REMEDIATION IMPLEMENTED; installer verification pending.** |
| Vendored skills | `.agents/skills/**` and `.agents/LICENSE` are included. Marketing Skills v2.11.1, commit `5b2c0007766c6a1cf1d53fd8fc73e979e0821022`, is MIT; prior provenance evidence reports identity after line-ending normalization. | **PASS** subject to retaining `.agents/LICENSE`. Keep `.agents/product-marketing.md` excluded. |
| Marketing Skills provenance | The library is `coreyhaines31/marketingskills` v2.11.1 at commit `5b2c0007766c6a1cf1d53fd8fc73e979e0821022`, with `Copyright (c) 2025 Corey Haines`. Its verbatim MIT grant is `.agents/LICENSE`; the generated manifest and lock record file checksums. `.agents/product-marketing.md` is OMOS-authored and not upstream. | **PASS** while the notice, manifest, lock and narrow export rule remain present. |
| Historical marketing adapter claim | `OMOS-Qwen2.5-7B-Marketing-v1` does not exist and no trained adapter weights are included. The current Local edition uses the separately disclosed Qwen2.5-7B-Instruct base model, which the user downloads explicitly; no model weights are bundled. | **PASS** for the source candidate disclosure; no adapter or base-model weight redistribution is implied. |
| Fonts and private creative | Cairo/Zain font binaries and private client imagery are absent from W1 candidate allowlist. | **PASS** as excluded; do not add without documented rights. |
| Inno Setup | DEV-029 compiler output shows “Non-commercial use only.” Installed vendor license text permits commercial applications; the vendor commercial licensing page requests commercial users to purchase a commercial license; its FAQ says purchase is not strictly required and may wait until the installers are production-ready. Sources: `https://jrsoftware.org/isorder.php`, `https://jrsoftware.org/isinfo.php`. | **NEEDS-FOUNDER-CONFIRMATION / PRE-PRODUCTION.** No legal conclusion or production-confirmed claim. |

## Integrated source files

- `LICENSE`: Apache-2.0 project license.
- `NOTICE`: corrected Local/llama.cpp statements, model disclosure, 0BSD htmx reference, and payload limitations.
- `THIRD-PARTY-NOTICES`: source-lock-derived Python/frontend listing and selected-component notices.
- `LICENSE-INVENTORY.json` and `LICENSE-INVENTORY.md`: exact lock versions, declared license metadata and provenance.
- `LICENSES/htmx-0BSD.txt`: exact upstream htmx v2.0.4 0BSD text.
- `scripts/package/notices/llama.cpp-LICENSE`: exact staged llama.cpp MIT notice bytes required by the packaging script.
- `scripts/package/stage_license_payload.py` and `scripts/package/notices/python/`: stage all exact locked Python and production frontend license files, required project notices, and a path manifest in the frozen payload.
- `runtime/licenses/llama.cpp/LICENSE` and `LICENSE-LLVM-OpenMP`: staged runtime notice copies for traceability.
- `LICENSE:189` retains the unfilled Apache-2.0 copyright-owner template; project attribution is supplied in `NOTICE`.

## Release disposition

**W6 finding:** the previous frozen installer omitted the project notice/inventory, htmx 0BSD text, and package attribution/license documents. **Candidate remediation:** build-time staging now adds those source materials, per-package Python/frontend license files, and a payload manifest. Focused fixture and installer-contract tests pass; actual remediated installer membership remains unverified until W5 rebuilds and W6 re-audits both installers. The exact `pyreadline3` vendor notice is preserved but describes only a BSD-type license. Do not claim production-ready licensing for Inno Setup until the founder commercial-use/purchase decision is recorded. Current installer state is **PRE-PRODUCTION**.
