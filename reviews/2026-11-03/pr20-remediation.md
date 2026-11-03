# PR #20 targeted remediation

Original head: `81df05829fb0c8f9d58dc7c8abbc28856d141784`.
Scope: bounded export-completeness bug fix, not a feature phase or DEV-052.
Authority: founder's targeted remediation request; direct implementation,
NO DELEGATION. Independent QA, human review and merge approval are not claimed.

Mini-plan: reproduce clean export failure; narrowly allowlist the read-only
prototype code, static setup configuration, license and requirements; exclude
runtime state and acceptance receipts; run exported tests in an isolated child
process; validate offline controls and secrets; commit/push this existing branch,
then merge its new head into PR #21 without rewriting either branch.

Validation and exact-head handoff will be recorded after execution.

## Reproduction and disposition

The actual exporter produced a clean 870-file tree at the original head. Isolated
pytest collection of its read-only test failed with ModuleNotFoundError for
prototypes.postiz_readonly. The new regression first failed on that missing package.
An earlier invocation failed only because its scratch parent directory was absent;
that setup error was corrected before reproducing the product defect.

Fix: exact-file INCLUDE_FILES entries for read-only code, requirements, policy,
static environment scripts/configuration and the upstream license. No whole
prototype-directory inclusion, evidence receipts, storage inventories, .env,
generated compose.json, databases or machine-specific artifacts. Dev requirements
now include the optional experiment's existing pinned requirements file.

Direct validation (not independent QA): 109 passed, 2 existing internal-fixture
skips, zero final failures in 62.05s. Command: pytest test_postiz_export,
test_postiz_readonly, test_dev008_public_export, test_dev007_tool_registry,
test_dev007_vault and test_dev007_hardening with -q -p no:cacheprovider.
The export regression additionally ran all 37 read-only contracts in a child
with isolated Python, exported cwd and module-path assertions. The existing Python
environment was reused; no environment, Docker service or provider was installed
or contacted. Full backend matrices will be verified through exact-head CI.

Remaining risks: live Postiz/installed-app acceptance remains out of scope;
independent QA/re-review and required human review are outstanding. The review of
81df0582 does not approve this new diff. Keep PR Draft and merge NO-GO.

Re-review: compare original head through the remediation commit; inspect exporter
allowlist, requirements recursion, private-state exclusions and clean-export test.
PR #19 is unchanged. The root-level handoff records final SHA/CI after commit.
