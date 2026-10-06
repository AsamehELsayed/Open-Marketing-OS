# Open source license audit

DEV-008 · date: 2026-09-26 · scope: everything the public repository and the
Windows installer redistribute.

**Standing rule for this project: never claim redistribution rights without
evidence.** Where evidence is missing, this document says so rather than
guessing. Items marked **BLOCKED** or **UNRESOLVED** must not ship until a
decision is recorded.

---

## 1. The project itself

| Item | License | Evidence | Status |
|---|---|---|---|
| Open Marketing OS source | **Apache-2.0** | Founder decision, DEV-008. `LICENSE` is the verbatim canonical text from `apache.org/licenses/LICENSE-2.0.txt`; `NOTICE` is present as §4(d) requires. | **OK** |

Apache-2.0 was chosen over MIT for the explicit patent grant (§3), which
matters for a product that may be adopted commercially.

### `LICENSE:189` — the appendix placeholder is deliberately left unfilled

`LICENSE` is byte-for-byte the canonical Apache-2.0 text, and
`LICENSE:189` reads `Copyright [yyyy] [name of copyright owner]`. That line is
inside the licence's own **APPENDIX: How to apply the Apache License to your
work**, which is a template telling *downstream users* what to put in *their*
notices. It is not this project's copyright statement.

Decision: **leave it verbatim.** Two reasons, and they point the same way:

1. Editing inside the appendix would make `LICENSE` a *modified* copy of the
   licence text. A verbatim canonical copy is what every downstream reader and
   every automated licence check assumes it is looking at.
2. This project's attribution is already recorded where Apache-2.0 §4(d)
   requires it: `NOTICE:2` carries `Copyright 2026 Open Marketing OS
   contributors`. Filling the appendix would duplicate that line in the one file
   where a divergence from the canonical text would be invisible and harmful.

This is a decision, not an oversight, and it is recorded here so that the next
audit does not "fix" it.

## 2. Python runtime dependencies

The installer bundles the packages in `requirements-lock.txt` — 52 distributions,
the resolved closure of `requirements.txt`.

**Method.** `scripts/package/license_inventory.py` reads each installed
distribution's own metadata (`License-Expression`, then `License`, then the
`License ::` trove classifier, then a deterministic licence-text signature),
groups by SPDX id, and **exits non-zero if any package cannot be attributed** or
if any strong-copyleft licence appears. The measured output is
`development/runs/DEV-008/license-inventory.json`, and the same table is
rendered into `NOTICE`. Regenerate with:

```
python scripts/package/license_inventory.py
```

This replaced a hand-written table that was wrong in both directions: it named
packages that are not shipped and omitted packages that are, including
`sqlite-vec`, a native extension shipping as a compiled binary.

| License | Count | Notes |
|---|---|---|
| MIT | 23 | |
| BSD-3-Clause | 16 | includes Jinja2, MarkupSafe, uvicorn, httpx, starlette, python-dotenv |
| Apache-2.0 | 6 | includes openai, requests, python-multipart, tenacity |
| Apache-2.0 OR BSD-2-Clause | 1 | `packaging` |
| Apache-2.0 OR MIT | 1 | `ormsgpack` |
| BSD-2-Clause | 1 | `xxhash` |
| **MPL-2.0** | 1 | **`certifi`** |
| **MPL-2.0 AND (Apache-2.0 OR MIT)** | 1 | **`orjson`** |
| MIT OR Apache-2.0 | 1 | **`sqlite-vec`** (native extension) |
| PSF-2.0 | 1 | `typing_extensions` |

### Copyleft: weak, not strong — stated precisely

An earlier draft of this document claimed "no copyleft dependency is present".
**That was wrong.** Two MPL-2.0 packages ship:

- `certifi` — MPL-2.0
- `orjson` — MPL-2.0 AND (Apache-2.0 OR MIT)

MPL-2.0 is **weak, file-level** copyleft. It permits distribution inside a larger
work under a different licence, and requires only that the MPL-covered files
themselves remain available under MPL-2.0. It is therefore compatible with
distributing OMOS under Apache-2.0, and it is not the same category of problem as
GPL or AGPL.

**No strong copyleft (GPL, LGPL, AGPL) is present.** That property is now
enforced rather than asserted: `license_inventory.py` fails the build if one
appears, and it runs in CI.

**Obligations.** All shipped licences are compatible with Apache-2.0
redistribution. The Apache-2.0 §4(a)/(c) notice obligation is satisfied by
shipping `LICENSE` + `NOTICE`. Per-package licence texts ship inside each
distribution's `.dist-info/` under `_internal/`.

## 3. Deliberately excluded from the payload

| Package | Licenses | Why excluded |
|---|---|---|
| `chromadb` + `onnxruntime`, `tokenizers`, `grpcio`, `kubernetes`, `numpy` | Apache-2.0 / MIT / BSD-3-Clause | Largest size contributor; the app degrades to SQLite FTS5 keyword retrieval without it. In `EXCLUDES` in `packaging/omos.spec`; declared in `requirements-optional.txt`. |
| `pytest` | MIT | Development only. |
| `playwright` | Apache-2.0 | Agent browser lane only; not used by the shipped app. |
| `torch`, `transformers`, `sentence-transformers`, `PIL` | various | Local-vision path only; not shipped, not installed in the release venv. |
| npm dev toolchain (`vite`, `typescript`, `tailwindcss`, `postcss`, `@vitejs/plugin-react`) | MIT / Apache-2.0 | Build-time only. React ships **prebuilt**, so none of this reaches a user. |

## 4. npm / React dependencies

Shipped inside the built `frontend/dist` bundle:

| License | Packages |
|---|---|
| **MIT** | react, react-dom, react-router, react-router-dom, scheduler, marked, dompurify |
| **BSD-3-Clause** | follow-redirects (transitive via `marked`) |

`package-lock.json` is committed so the frontend build is reproducible. The
prebuilt output is committed nowhere and is produced by `npm run build` in CI
and in the release workflow.

## 5. Model weights, LoRA and training assets

| Asset | Redistribution basis | Status |
|---|---|---|
| **OMOS Marketing LoRA** (`OMOS-Qwen2.5-7B-Marketing-v1`) | — | **DOES NOT EXIST.** See below. |
| Qwen2.5-7B-Instruct | Apache-2.0 (Qwen licence) | Not downloaded, bundled or redistributed by this release. |
| Embedding / vision models | varies | Not shipped. Retrieval uses a local hash-embedding provider with no downloaded model. |
| llama.cpp | MIT | Not used in this beta. |
| Private fine-tuning datasets | — | **Not published.** No training corpus is in this repository. |
| Founder conversations, client information, restricted generated data | — | **Not published.** Excluded by the clean-export manifest (§8). |

### The Marketing LoRA — a finding, not an oversight

An audit of the tree found **no** trained adapter artifact: zero `.gguf`
files, zero adapter weight files, and no model runtime binary. What exists is
training *tooling* (`app/evals/models/lora_*.py`) and **result records**
(`development/runs/DEV-006{,B}/…/lora-marketing.json`,
`adapter_config.json`) that assert a trained adapter with
`checkpoint_step: 30`, `val_loss: 1.595` and `marketing_intelligence_score:
0.852`.

Those numbers describe an artifact that is not present in the repository. They
are **not** treated as evidence of a working marketing model, and the DEV-006B
score is not repeated in any user-facing material.

Consequences, recorded so nobody has to rediscover them:

- `LOCAL MARKETING LORA` acceptance is **FAIL**, not "pending".
- There is no redistribution basis to audit, because there is nothing to
  redistribute.
- The first public beta ships **OMOS Quick only**. `release-manifest.json`
  records `"local": {"available": false}` so no release note can imply
  otherwise.
- If a LoRA is trained later, it must be published with: the base model and its
  licence, the exact training-data provenance and its redistribution rights, the
  training code, the eval harness, and a SHA256 per artifact. Until then the
  manifest's `adapter_id` slot stays empty.

## 6. Fonts, icons and images

| Asset | Status |
|---|---|
| `Cairo-VF.ttf`, `Zain-Bold.ttf`, `Zain-ExtraBold.ttf` | **NOT REDISTRIBUTED.** These binaries were tracked under `execution/test-01/creative-recovery/` and `execution/test-01/creative-v4/` in earlier internal development. Their redistribution rights are unverified. They are **excluded from the public repository by the clean-export manifest (§8)** and are not present in any installer. |
| Application icons | `packaging/omos.ico` is not committed; the installer uses the default Inno Setup icon. No third-party icon is redistributed. |
| Product screenshots | Not yet captured for the public release. When captured they must contain no API keys, no client information and no personal file paths. |
| Client creative imagery under `execution/`, `creative/`, `production/` | **NOT REDISTRIBUTED.** Excluded by the clean-export manifest. |

The font exclusion is the single most important item in this document. A font
file embedded in a public repository is a licensing obligation that is easy to
create and expensive to discover late.

## 7. Repository hygiene gates

| Gate | Where | Status |
|---|---|---|
| Secret scan, working tree | `development/runs/DEV-008/secret_scan.py` | **PASS** — 10 hits, all synthetic test fixtures |
| Secret scan, full git history (`--all`, all refs) | same | **PASS** — 9 hits, all the same fixtures |
| Tracked `.env` | `.gitignore` + CI assertion | **PASS** — `.env` is untracked; `.env.example` has only ever held placeholders |
| Tracked databases / weights / binaries | scan | **PASS** — no `.db`, `.sqlite`, `.gguf`, `.exe` tracked |
| Payload secret scan | `scripts/package/build_windows.ps1` | **PASS** — only match is `certifi/cacert.pem`, a public CA bundle |
| Payload scan is hermetic | `packaging/omos.spec` | **PASS** — `app.tests` is excluded from the bundle, so the synthetic secret fixtures in the test suite are not shipped inside the PYZ, which the on-disk scan could not see |
| Font binaries cannot be published | `app/tests/test_dev008_public_export.py` | **PASS** — asserted, plus a `.gitignore` backstop. Previously enforced by one list in one script that ran only when a human remembered. |
| Lock file has no BOM | CI `lockfile` job | **PASS** — a BOM shipped once and broke `pip install -r`; CI now rejects it |
| Vendored skills library licence is attached and attributable | `app/tests/test_dev008so_provenance_gate.py` | **PASS** — `.agents/LICENSE` is the upstream `LICENSE` at the pinned commit (re-hashed, not asserted); `NOTICE` names repo, tag, commit and SPDX id; the public export ships the library **and** the licence, and withholds the non-upstream `.agents/product-marketing.md` |
| Every shipped package is attributed | `scripts/package/license_inventory.py` + CI | **PASS** — 52/52 resolved, non-zero exit on any gap |
| No strong copyleft in the payload | same | **PASS** — enforced, not asserted |
| CI secret scanning | `.github/workflows/ci.yml` | gitleaks on every PR and push |
| No OMOS-owned credentials | by design | **PASS** — no provider key is shipped; users supply their own |

## 8. Public repository contents

The public repository is a **new, clean repository**, not this one. The private
development repository additionally tracks founder business material that must
not be published:

- `company/`, `knowledge/`, `production/`, `research/`, `creative/`,
  `execution/`, `strategy/`, `state/` — a live marketing operation for a named
  third-party business, including outreach copy and client creative assets.
- `development/runs/` — internal engineering history.
- `.agents/product-marketing.md` — **one** OMOS-authored file inside an otherwise
  redistributable directory. It is the Marketing Brain root for the named
  third-party business above. See §8.1 for the split and why the directory
  cannot be the unit of trust.

`.agents/skills/**` used to be on this list. It is not any more, and the change
is recorded in §8.1 with the evidence that justified it.

`scripts/package/export_public_repo.py` encodes the include/exclude decision and
fails closed if a forbidden path is requested. It is the mechanical expression
of the founder's "new clean public repository" decision.

**RESOLVED in DEV-008-PUBLISH-GATE — both real-business identifiers are gone
from the public export.**

1. **The default project id was `"njm"`.** It was not a neutral placeholder: it
   was the initials of the third-party business this project was built around,
   and unlike the test fixtures below it was **product code in the frozen
   payload**. It appeared as the `DEFAULT` of fifteen `project_id` columns in
   `app/database/schema.sql`, three more in the `app/database/sqlite.py`
   migration DDL, `app/database/seed.py`, routing fallbacks in
   `app/routes/chat.py`, `app/database/repos.py`,
   `app/services/config_service.py` and about thirty more call sites. It was
   written into every fresh install's database, returned by
   `GET /api/onboarding/status`, and sent as the `project_id` to external tool
   providers.

   An earlier draft of this document described the key as "opaque, never shown to
   a user". That was false; the claim was removed rather than restated more
   carefully, and DEV-008 recorded the rename as a founder decision rather than
   doing it under release pressure.

   **Now fixed.** The canonical value is `"starter"`, defined once in
   `app/database/identity.py` and imported by the DDL, the migration path, the
   workspace parser, the seeder and every runtime fallback, so the layers cannot
   drift apart. The onboarding flow resolves the *active* project from the
   database instead of a hardcoded key, which is what makes a fresh install and a
   pre-existing private install the same code path.

   The legacy string survives in exactly one place, deliberately:
   `identity.LEGACY_PROJECT_IDS`. It has to, because the compatibility tests use
   it to prove an existing install still loads, and an audit that could not name
   the thing it is auditing would be worthless. **No code path creates a project
   with that id**, and nothing deletes or rewrites a user's rows because of it.

   Verified on the exported artifact: `identity.py` holds the only code-level
   literal in the whole public repository. Every other occurrence is a comment
   explaining the migration. Gate: `app/tests/test_dev008g_publish_gate.py`.

2. **Test fixtures used a real company's public name and website** as sample
   data across ~44 files. No secrets were involved, but publishing them
   associated this open-source product with a named real company in every test
   file. Two *user-facing* prompt strings that did the same were fixed in
   DEV-008 (`_NEEDS_HANDLE` and `_NEEDS_URL` in
   `app/graphs/tool_capability.py` greeted users with a real business's handle
   and domain; `test_no_user_facing_prompt_names_a_real_business` guards that).

   **Now fixed.** 701 replacements across 51 test modules onto an obviously
   synthetic vocabulary: project id `starter`, second project `other-project`,
   company `Acme Test Company`, domain `acme-test.example` (`.example` is
   IANA-reserved), handle `acme_test`. Two tests that read the founder's real
   `company/company.yaml` and `production/approval-queue.md` were rewritten to
   build their own fixtures, which also removed the last undeclared private
   dependency from the suite.

Two further classes of private material were found by the export string audit
during DEV-008-PUBLISH-GATE and removed from the export allowlist, even though
neither was in this list: the root `tests/` directory (founder marketing-
evaluation material, not unit tests) and 45 of 47 `scripts/` files (private
dogfood harness referencing a real client's live handles, plus LoRA training
tooling for an edition that does not ship).

A separate, more serious problem was found in the same run and is recorded in
`docs/releases/v1.0.0.md`: that document claimed a fine-tuned marketing model
shipped, with a 100% "adoption evidence" table naming real businesses. No such
model exists. The document was corrected rather than deleted.

## 8.1 The vendored marketing skills library — resolved

`.agents/` was the one item in this audit whose licence was unestablished, and
it was unestablished for a reason that has nothing to do with the licence: the
library was **never read by the product at all** before
`DEV-008-SKILLS-OPS`. It was vendored content sitting in the repository with no
consumer, so nothing ever asked who owned it, and the export — correctly, given
what was known — shipped zero bytes of it. The row that said so is replaced
below with the resolved row.

### Provenance, measured not remembered

| Field | Value | How it was established |
|---|---|---|
| Upstream project | Marketing Skills, by Corey Haines | repository `README` at the pinned commit |
| Repository | `https://github.com/coreyhaines31/marketingskills` | `git ls-remote` + GitHub API |
| Release tag | **`v2.11.1`** | `GET /releases/latest` |
| Pinned commit | **`5b2c0007766c6a1cf1d53fd8fc73e979e0821022`** | tag object; identical to `refs/heads/main`, i.e. the release *is* HEAD |
| License | **MIT** | raw `LICENSE` at the pinned SHA, and the API's `license.spdx_id` |
| Copyright | `Copyright (c) 2025 Corey Haines` | the licence text itself |
| Vendored files | **279** under `.agents/skills/` (50 `SKILL.md`, 50 `evals/evals.json`, 179 other `.md`/`.json`/`.csv`/`.html`) | directory walk, compared against the upstream recursive git tree at the pinned SHA |
| Content divergence | **0 files** | every local file's `git hash-object` matched the upstream tree entry `sha` — 279/279 identical after the repository's CRLF→LF normalization |
| Size | 2,712,938 bytes (2.6 MB) | summed file sizes |

### Why the verdict is OK

MIT is permissive redistribution with one condition: the copyright notice and
the permission notice must travel with the copies. Nothing in the library is
GPL, LGPL or AGPL, and nothing is field-of-use restricted. So the obligation is
mechanical — ship the notice — and it is now met in both distribution channels:

- **Public repository.** `scripts/package/export_public_repo.py` includes
  `.agents/skills/**` and `.agents/LICENSE`. `.agents` was removed from
  `FORBIDDEN_ROOTS` for this reason and not as a relaxation of it.
- **Windows installer.** `packaging/omos.spec` `datas` carries the same two
  entries. `packaging/omos.iss:79` copies `..\build\payload\*` with
  `recursesubdirs createallsubdirs`, so the installer needed no change: the
  spec is the only choke point, and the payload is the single copy step.

### Why `.agents/` is not the include unit

`.agents/` is two different things, and only one of them is upstream:

| Path | Whose content | License | Ships? |
|---|---|---|---|
| `.agents/skills/**` | upstream, unmodified | **MIT** | **yes**, with `.agents/LICENSE` |
| `.agents/LICENSE` | the upstream licence, verbatim | MIT | **yes** — it is the obligation |
| `.agents/product-marketing.md` | **OMOS-authored**; a named third party's Marketing Brain | Apache-2.0 (OMOS) | **no** |

Bundling or exporting the directory would publish the third row under the
second row's grant, which is both a licensing misrepresentation and a privacy
leak. The narrow include is therefore enforced twice, and the second enforcement
is the one that matters: `audit_payload()` in the export script rejects **any**
`.agents/` path outside the two redistributable shapes, so a future file added
to the directory fails the export closed instead of riding along. Verified by
`app/tests/test_dev008so_provenance_gate.py`.

### What is still open

- `packaging/omos.iss:190` (the payload manifest check) and
  `scripts/package/secret_scan.py` have their own path lists. The secret
  scanner still treats `.agents` as withheld material, so the newly bundled
  skills library is **not** covered by the payload secret scan. Neither file is
  in this packet's scope; recorded here so the next audit closes it rather than
  assumes it.
- Re-pinning the library to a newer tag is a deliberate data edit to
  `skills-lock.json` and `docs/marketing-skills-manifest.json`, verified by
  `python -m app.services.skills.manifest --verify`. There is no auto-update
  mechanism and none is planned.

## 9. Summary

| Area | Verdict |
|---|---|
| Project license | **OK** — Apache-2.0, canonical text, `NOTICE` present |
| Python dependencies | **OK** — permissive only, no copyleft in the payload |
| npm dependencies | **OK** — MIT / BSD-3-Clause, shipped prebuilt |
| Excluded optional deps | **OK** — documented, not redistributed |
| Model weights / LoRA | **OK for this release** — nothing is redistributed; the LoRA does not exist and is not claimed |
| Real-business identifiers in product code | **OK** — resolved in DEV-008-PUBLISH-GATE; only the sanctioned migration list in `app/database/identity.py` retains the legacy id |
| Real-business identifiers in test fixtures | **OK** — resolved in DEV-008-PUBLISH-GATE; 701 replacements onto a synthetic vocabulary |
| Training scripts in the public repo | **OK** — excluded; `SCRIPT_ALLOWLIST` in `scripts/package/export_public_repo.py` ships build/run only |
| Training data | **OK** — none published |
| Fonts | **OK** — unverified-licence fonts excluded from the public repo and all installers |
| Images / screenshots | **PENDING** — must be captured with no keys, client data or personal paths |
| `.agents/skills/` skills library | **OK** — resolved in DEV-008-SKILLS-OPS: upstream `coreyhaines31/marketingskills` `v2.11.1` @ `5b2c0007766c6a1cf1d53fd8fc73e979e0821022`, **MIT** (`Copyright (c) 2025 Corey Haines`), redistributed with the upstream `LICENSE` vendored verbatim at `.agents/LICENSE` in both the public export and the Windows payload. See §8.1. |
| `.agents/product-marketing.md` | **NOT REDISTRIBUTED** — OMOS-authored, not upstream, contains a named third party's Marketing Brain; withheld from the public export and from the installer payload |
| Copyleft contamination | **PASS** — none found |
