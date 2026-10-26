# Phase 2 — Desktop Runtime Acceptance (Open Marketing OS 0.1.0-beta.2)

> **Status update:** the Phase 2 fixes were built by CI run 40273844902 (commit 88f24e1d, Windows and Linux green).
> That build's installer was downloaded and its checksum verified in Phase 3. See
> `docs/acceptance-phase3-windows-beta.md` for the current Windows status. This page is the Phase 2 record and is otherwise unchanged.

Verdict: **NO-GO for a limited beta on the CI artifacts of run 40246611654. GO-candidate once the fixes on
`claude/desktop-runtime-acceptance-p2-e84f05` are rebuilt by CI and the manual checks below pass.**

## Provenance and what was NOT tested
- CI run 40246611654 built commit `e6eedb29` (branch `claude/omos-desktop-rc-integration-91f555`).
- **The CI artifacts were NOT downloaded or checksum-verified.** Artifact download needs a GitHub token; `gh` is not
  installed and I was denied access to the stored git credential. Their SHA-256 digests as reported by the GitHub API:
  - `desktop-win-x64-openrouter` sha256:ef44ee2a10861a69e8630bdf1eee8ab18518a07905705cd3b05050a4a3c20afa
  - `desktop-linux-x64-openrouter` sha256:edfe7bb1660242fc4f844d47808343fdc2a82148238fa312f53becf67a401a07
- Instead I built both candidates locally from the same commit (+ the fixes) and tested those. They are equivalent
  source, not the same bytes. Local Windows installer SHA-256 matched its own SHA256SUMS (Get-FileHash). Not code-signed.
- No real OpenRouter call was made; only a fake `sk-or-v1-ACCEPTANCE...TESTONLYNOTREAL` key, never sent anywhere.
  Real-model chat quality and the real-provider path are untested.
- "Linux desktop" = Ubuntu 26.04 under WSL2/WSLg with a real dbus session and gnome-keyring. It is a real Linux
  Electron GUI and a real Secret Service, but not a physical GNOME/KDE desktop.

## Defects found and fixed (all reproduced on the frozen binaries, regression-tested)
| # | Severity | Defect | Fix |
|---|---|---|---|
| 1 | Blocker (Linux) | gnome-keyring adds `xdg:schema` to items; backend compared attributes by equality, so every stored key read back as "unreadable" and OpenRouter could never be used | ignore provider-added `xdg:*` attributes (foreign attributes still fail closed) |
| 2 | Blocker (Windows) | Sidecar control monitor did a blocking CRT read of stdin, holding the fd lock; lazy numpy/OpenBLAS DLL load blocked forever, so `/api/settings/config` (Settings page) never returned (>3 min, 0 CPU) | poll with `PeekNamedPipe` on Windows |
| 3 | High (flaky start) | `platform.machine()` is empty under Electron's cleaned env (no `PROCESSOR_ARCHITECTURE`), so the sidecar refused to start ("installed build manifest mismatch"); intermittent depending on WMI | pointer-width fallback; env allowlist forwards the variable |
| 4 | Medium | Missing/locked keyring produced a bare HTTP 500 | 503 with the sanitized, actionable message |

## Evidence — REAL desktop tests (executed)
Windows, installed from the NSIS installer (silent, `/D=` normal path), isolated LOCALAPPDATA/APPDATA, driven through
the real Electron window with Playwright `_electron`:
- Install, launch (window ready 4.8 s), preload bridge `omosDesktop.status()` OK, no `require`/`process` in the page,
  clean shutdown leaves no `Open Marketing OS.exe`/`omos-sidecar.exe`; uninstall removes app, registry and shortcuts and keeps user data.
- GUI: Create business -> Settings -> AI & Models -> Connect OpenRouter (password field) -> "Configured", key absent
  from the DOM; after app restart business and credential persisted.
Windows frozen sidecar black-box harness (real protocol, real binary): 28/30; the two FAILs are the harness expecting a
non-zero exit code on startup failure, which is 0 (minor, Electron keys off the error frame/exit event). Covered:
token/Host/Origin enforcement (401/403), query-string token rejected, CSP headers, DPAPI file store, plaintext scan of
the data root, shutdown, restart persistence, hard-kill recovery, parent-death (stdin EOF) exit, no orphans.
Linux (frozen sidecar + Electron linux-unpacked under WSLg + gnome-keyring over D-Bus): sidecar harness 24/25 and 18/19
(the one FAIL in each is a harness false positive: `pgrep -f` matched its own shell; `pgrep -x` shows none). Covered:
Secret Service item in the default "Login" collection, attributes hold no key material, removed on disconnect,
persists across restart, no D-Bus session -> fails closed with 503 and nothing written in plaintext. GUI: same
create-business / save-key / restart-persistence journey passed with the default Chromium sandbox.

## Evidence — AUTOMATED tests (not desktop)
- `desktop` unit tests 7/7; backend 3048 passed / 1 failed (fixed, see commit 2) / 85 skipped (excluding
  `test_w2_langchain_first.py`, which cannot import in my local env: chromadb/langchain_chroma mismatch).
- Chat -> plan -> generate -> revise -> approve -> export: `test_dev044_ui_journey.py` 2/2 in real Chromium against the
  real app with the deterministic provider, **from source, not the packaged app**.

## NOT executed (no PASS claimed)
Chat-to-campaign inside the installed app (needs a real OpenRouter key; the provider URL is hard-coded, so no
deterministic provider can be injected into a frozen build); project isolation through the GUI (API-level project
creation tested only); Linux AppImage/.deb install paths; Ubuntu 24.04+ AppArmor userns restriction on the sandbox;
Wayland/X11 native desktop session; locked-keyring prompt UX; CI rerun on the fixed branch.

## Other findings (non-blocking)
- Sidecar startup failure exits with code 0.
- Settings copy mixes "Local inference disabled" with a "Cloud escalation... Off keeps AI strictly local" label.
- Linux AppImage needs `libasound2t64` (+ libsecret etc.) present; the deb must declare them.
- `/docs` is token-gated (200 only with the token).

## Manual checks still required (real desktop, ~1 hour)
1. Download both CI artifacts of the rebuilt fixed commit; verify SHA-256 against `SHA256SUMS`.
2. Windows 10/11 clean user: install, SmartScreen (unsigned) flow, first run, Settings opens <10 s, key persists after reboot.
3. Ubuntu 24.04/26.04 GNOME (and KDE): AppImage and .deb launch with sandbox, first login keyring unlocked and locked
   states, key persists after logout/login.
4. One real OpenRouter key on a free/low-cost model (needs your authorization): request -> plan -> generate -> revise ->
   approve -> export zip in the installed app; create two projects and confirm no cross-project content.
5. Kill the app while generating; relaunch; confirm recovery dialog and no data loss.

## Next step needing approval
CI rerun: `desktop-candidates.yml` only triggers on pushes to the integration branch or manual dispatch. I pushed my
branch only; I did not touch the integration branch or merge/tag/publish.
