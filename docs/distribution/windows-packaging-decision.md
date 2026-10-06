# Windows packaging decision — Open Marketing OS

DEV-008 · decided 2026-09-26 · status: **implemented and measured**

This records which Windows packaging strategy Open Marketing OS ships with, why,
and what was actually measured on a real build. Numbers below are from a real
run of `scripts/package/build_windows.ps1` on Windows 11 x64, Python 3.12.10,
PyInstaller 6.x, Inno Setup 6.7.3.

---

## 1. The requirement

A non-developer must be able to go from *downloading a file* to *using the
product* with no Python, Node.js, Git, Docker, terminal, `.env`, or knowledge of
FastAPI, LangGraph or llama.cpp. One double-click, no console window.

Concretely the build has to absorb:

| Concern | Why it is hard |
|---|---|
| FastAPI + uvicorn | Dynamic imports for the event loop, HTTP protocol and lifespan implementations — invisible to static analysis |
| LangGraph / LangChain | Deeply dynamic graph/checkpoint module resolution |
| SQLite | Native extension (`pysqlite3`-style) plus a runtime-read `schema.sql` |
| React interface | Must ship **prebuilt**; a user will never run `npm` |
| Chroma / vector deps | Optional, and by far the heaviest thing in the tree |
| Credential Vault | Windows DPAPI via `ctypes` — must keep working from a frozen exe |
| SSE streaming | Long-lived HTTP responses through a frozen ASGI server |
| OpenRouter / OpenAI | `openai` SDK does dynamic client construction |
| MCP | Plugin-style dynamic tool loading |
| Local model integration | Seam must survive freezing even though Local is not in this beta |

## 2. Options considered

### A. PyInstaller, one-folder — **CHOSEN**

Bundles a CPython interpreter plus every dependency into
`OpenMarketingOS/OpenMarketingOS.exe` + `_internal/`.

- **Pros:** mature, enormous ecosystem coverage, `hiddenimports` for the
  dynamic-import cases, `--windowed` gives a genuinely console-free binary,
  one artifact that works from an extracted folder (portable) or under Program
  Files (installed).
- **Cons:** no source-level optimisation, AV false positives on unsigned
  binaries, `_internal` must be shipped alongside the exe.

### B. PyInstaller, one-file

Rejected. Every launch unpacks the whole bundle to a temp directory. That
roughly doubles cold start, and it makes the install layout non-deterministic,
which is a poor trade for a desktop app whose whole pitch is "just works".

### C. Nuitka

Rejected for the first beta. Genuinely faster code and smaller cold start than
PyInstaller, and it would be the natural next step. But it compiles from C and
needs a toolchain per architecture, its packaging of dynamic-import frameworks
is less battle-tested, and every extra moving part is extra release risk for a
first public beta. Revisit once the Quick edition is stable in the field.

### D. Embedded CPython + hand-rolled launcher

Rejected. Lowest tooling risk but highest *maintenance* risk: someone has to own
`pythonXY.zip` layout, `._pth` files, DLL search paths, and the upgrade
procedure by hand, forever.

### E. Docker / self-hosted

Rejected outright. It is the opposite of the product promise and would add a
daemon dependency to a desktop app.

## 3. What was actually built

```
frontend\dist                 24 files, 0.38 MB   (prebuilt React)
frozen payload              144 files, 41.1 MB   (exe + _internal)
OpenMarketingOS-Quick-Setup.exe   22.8 MB         (LZMA2 max)
OpenMarketingOS-Portable.zip      24.2 MB
```

Payload top level is exactly two entries — `OpenMarketingOS.exe` (14.7 MB) and
`_internal/` — which is what a user should see.

### The size story

The 41 MB payload is the *runtime*, not the app. It is dominated by
`langchain-core`, `langsmith`, `openai` and the CPython standard library.

`chromadb` was measured as the single biggest avoidable cost: it drags in
`onnxruntime`, `tokenizers`, `grpcio`, `kubernetes` and `numpy`. It is
therefore **excluded from the shipped payload** (`EXCLUDES` in
`packaging/omos.spec`) and declared in `requirements-optional.txt` instead. The
app does not break: retrieval falls back to SQLite FTS5 keyword search and the
UI badge honestly reports "keyword only" rather than "hybrid". Verified by
`test_reindex_works_on_a_fresh_workspace`, which asserts the mode is one of
`hybrid` / `fts_only` rather than requiring vector search.

## 4. Three real bugs this process caught

Unit tests could not have found any of these. All three would have shipped a
broken installer, and all three were found by the build script's smoke test —
which is why the smoke test is mandatory and non-skippable in a release build.

1. **`app/database/schema.sql` was not bundled.**
   `sqlite.connect()` does `open(SCHEMA_PATH)` and `executescript(...)` on
   every connection. PyInstaller's static analysis does not see an `open()` of a
   sibling `.sql` file. Symptom: `FileNotFoundError` on startup, before a single
   request was served. Fix: an explicit `datas` entry.

2. **The same entry produced a `PermissionError`.**
   PyInstaller treats a `datas` destination as a *directory* to place the source
   inside. Passing `app/database/schema.sql` as the destination silently created a
   **directory** with that name. Symptom: `PermissionError: Access to the path
   ... schema.sql is denied`. Fix: destination is `app/database`.

3. **The smoke test killed the process before its second assertion.**
   `/health` passed, the script then stopped the process, and the onboarding
   check failed with "Unable to connect to the remote server" — a bug in the
   *test*, which is exactly the kind of thing that erodes trust in a release
   gate. Fix: assert everything, tear down in `finally`.

**Lesson recorded deliberately:** a packaging smoke test is not optional
ceremony. It is the only thing standing between a green unit-test run and an
installer that cannot start.

## 5. Application layout (the part that matters most)

The install folder is **read-only**. The app is installed per-machine under
`Program Files` with `PrivilegesRequired=admin`, which guarantees it on every
install rather than merely intending to.

All mutable state lives in the per-user data root instead:

```
%LOCALAPPDATA%\OpenMarketingOS\
  data\marketing.db      SQLite database
  data\chroma\           optional search index
  data\backups\          workspace backups
  credentials\           DPAPI-encrypted secrets
  logs\                  launcher + backend logs
  company\ knowledge\ …  the user's workspace files
```

This is enforced in one place, `app/paths.py`, and covered by
`test_no_mutable_path_falls_back_into_the_bundle` and
`test_frozen_build_redirects_every_mutable_path`. Consequences a user actually
feels:

- Updating never costs them work.
- Uninstalling never costs them work.
- The portable ZIP writes to the *same* place as the installed app, so moving or
  deleting the extracted folder loses nothing.

## 6. Ports and network exposure

The launcher binds port **0** and reads back the kernel's assignment, so it
never assumes 8000 or 8080 are free (`find_free_port`, covered by
`test_free_port_is_actually_bindable`). The backend always binds `127.0.0.1`.

Asserted, not assumed: `test_backend_binds_loopback_only` and
`test_child_entry_never_binds_all_interfaces` fail the build if `0.0.0.0`
appears anywhere in the launcher or serve entry point.

## 7. Code signing — the honest position

**This beta is not code-signed.** There is no certificate, and none is
configured, bundled or implied anywhere in the build.

Windows SmartScreen will therefore show an *"Unknown publisher"* warning on
first run. This is stated plainly in the README, in `SECURITY.md`, in the
release notes, and in `release-manifest.json` (`"code_signed": false` with an
explanatory note). We do not tell users to disable SmartScreen; we tell them how
to verify the download against `SHA256SUMS.txt` instead.

Purchasing a signing certificate is a real cost decision for the project owner
and has deliberately not been made. The path when it is:

1. Buy an OV or EV code-signing certificate (EV avoids SmartScreen reputation
   bootstrap, at higher cost).
2. Add `SignTool` / `signtool.exe` invocation to `scripts/package/build_installer.ps1`,
   reading the certificate from a CI secret — never committed.
3. Add Authenticode timestamping so signatures survive certificate expiry.
4. Sign both the installer and `OpenMarketingOS.exe`.
5. Flip `"code_signed": true` in the manifest and update the README.

Until then the checksum file is the integrity story, and it is a real one.

## 8. Reproducibility

`requirements-lock.txt` is the resolved runtime closure — 52 pinned packages,
generated from a clean virtualenv that installed **only** `requirements.txt`.
That resolution was validated by running the full backend on the lean set:
health, onboarding and settings all answered normally.

Before DEV-008, `requirements.txt` declared 8 packages while the app imported
`langgraph`, `langgraph-checkpoint-sqlite`, `PyYAML` and `python-dotenv`. A
clean install produced an app that could not start, so **no CI run and no
release build was reproducible**. That is now fixed, and the release workflow
prefers the lock file.

`build_windows.ps1` builds in an isolated `build/venv` precisely so a
developer's extra packages cannot leak into a release payload. Passing
`-SkipVenv` reuses the ambient interpreter; it is for iteration and it is
**not** what the release workflow uses. (Measured: `-SkipVenv` inflated the
payload from 41.1 MB / 144 files to 85.1 MB / 225 files, because the dev
virtualenv carries pytest, playwright and chromadb. Useful illustration of why
the flag exists but must not be used for a release.)

## 9. Commands

```powershell
# full release build (isolated venv, PyInstaller, smoke test)
pwsh -File scripts/package/build_windows.ps1

# installer (needs Inno Setup 6)
pwsh -File scripts/package/build_installer.ps1

# portable zip
pwsh -File scripts/package/build_portable.ps1

# manifest + checksums
python scripts/package/make_manifest.py --out-dir dist
```

## 10. Not done, and why

| Item | Status |
|---|---|
| Clean-machine acceptance test | **NOT RUN.** No clean Windows environment is available in the build environment; this machine has Python, Node, Git, an existing checkout, a populated `data/` and a credentials directory. The protocol is in `development/runs/DEV-008/founder-publication-runbook.md` and must be executed by the founder before the release is announced. |
| Upgrade test (A → B, data preserved) | **NOT RUN.** Requires two installed builds on a clean machine. Same blocker. |
| Code signing | Not done. See §7. Deliberate, not an oversight. |
| Local / llama.cpp / model manager packaging | Out of scope for this beta. No local edition is advertised and no model is bundled. |
| Nuitka comparison | Not benchmarked. Deferred; see §2C. |
