# Phase 4 Windows beta acceptance — in progress

Decision: **NO-GO**. This report records evidence available during Phase 4; it does not assert completion.

- Base main: `cce9d94219408b0088e1565b791ec78a51d97925`.
- Main nonpublishing candidate: [run 40574847380](https://github.com/AsamehELsayed/Open-Marketing-OS/actions/runs/40574847380); dispatched against main. Build, artifact hash and embedded identity are pending verification.
- Local focused campaign tests: 41 passed, including overlap rejection. Desktop unit tests: 9 passed. Full test and CI results are pending.
- `npm audit` against the original locked frontend dependencies reported 7 advisories (1 critical, 2 high, 4 moderate). The critical DOMPurify dependency was updated in the Phase 4 branch; npm subsequently reported 6 (2 high, 4 moderate). Applicability and remediation of remaining advisories are not yet established.
- The final-copy and separate structured-audit contract, editor/export, and regression coverage are not implemented or verified.
- Actual clean-account installation, reboot persistence, mouse-operated recovery, native Save dialog, uninstall, secret storage and project isolation on this candidate remain unexecuted.
- Installed deterministic full-campaign acceptance is unexecuted; no production provider bypass should be introduced. A new real-provider run has not been authorized or executed.
- Installer signing/SmartScreen acceptance remains unresolved. Main candidate and Phase 4 PR build are different artifacts and must be identified separately.

No merge, tag, publish, or deploy has been performed. Update each item only with executed evidence.
