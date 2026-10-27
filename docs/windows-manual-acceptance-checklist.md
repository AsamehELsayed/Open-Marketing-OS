# Windows manual acceptance checklist (tests that cannot be automated)

Use a clean Windows 10/11 user account (a fresh VM is best). Installer: `Open Marketing OS Setup 0.1.0-beta.2.exe`
from the CI artifact. Record PASS/FAIL and a screenshot for any FAIL.

1. **Integrity and install (10 min)**: `Get-FileHash -Algorithm SHA256 <exe>` equals the line in `SHA256SUMS`.
   Double-click, accept the SmartScreen "unknown publisher" warning (More info, then Run anyway), install, launch from the Start menu.
   The window opens in under 15 s; Settings opens in under 10 s.
2. **Persistence across reboot (10 min)**: create a business, reboot, relaunch. The business is still there.
3. **Crash recovery by a human (5 min)**: in Task Manager end `omos-sidecar.exe` while the app is open. A dialog offers Retry/Quit.
   Click Retry: the app returns with data intact. Repeat and click Quit: the app closes with no stray processes.
4. **One real campaign run with a capable paid model (20 min; already passed once on a free model, see the Phase 3 report, section 5b; free models take 5-10 minutes per step)**: Settings, AI & Models, connect your own
   OpenRouter key. Chat "Create a 30-day Instagram and email campaign for local families", review the plan, Write this content,
   revise one item, send to review, approve, download the approved .zip. The zip must contain only approved items.
   Create a second project and confirm none of the first project's content appears.
5. **Uninstall (5 min)**: Apps, uninstall. App and shortcuts are removed; `%LOCALAPPDATA%\OpenMarketingOS` remains (by design).
6. **Optional**: Windows 10, 150% DPI or a second monitor, laptop sleep/resume while a generation is running.
