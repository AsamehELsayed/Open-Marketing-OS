# Installed-app acceptance harness (Windows)

Drives the real NSIS-installed app (Electron + frozen sidecar) over CDP with `playwright-core`.
Not part of CI: it needs an installed build. Paths are hard-coded to the layout used in Phase 3:
installer silently installed to `C:\omos-p3\install` (`/S /D=...`, run from PowerShell, not Git Bash,
which mangles `/S`), isolated profile in `C:\omos-p3\profile`, `npm i playwright-core` next to the scripts.

    node suite.cjs      # about 2 minutes, writes C:\omos-p3\suite-results.json

No real AI provider is used. The final cleanup `taskkill` may print an error when no sidecar is left;
that is the expected state, not a failure.
