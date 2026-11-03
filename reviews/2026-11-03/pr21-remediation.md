# PR #21 targeted remediation

Original head: `a4ce24d6af4db18b356a26da3ad9c4008d088dac`.
Updated PR #20 was incorporated by a normal merge, preserving existing history.
Scope: export completeness and operator CLI failure exit codes; no new feature
phase/DEV-052. Founder authorized direct remediation; NO DELEGATION remains.

Mini-plan: reproduce the draft package omission in a clean export and the pending/
ambiguous exit-code failures in real subprocesses; extend the existing exact-file
export allowlist; give refusal and uncertainty distinct nonzero CLI codes without
changing the ledger or retry controls; run synthetic process and control tests,
secret hygiene and exact-head CI; retain Draft and request fresh human re-review.

## Confirmed reproductions

After the PR #20 merge, a real clean export still omitted postiz_drafts;
isolated collection failed at test_postiz_drafts.py:19 with ModuleNotFoundError.
The new subprocess/export red selection returned 5 failed, 3 passed in 132.64s:
pending/rejected approval, stale revision and simulated commit-then-timeout all
incorrectly returned exit 0, plus the missing exported package assertion.

## Fix

The existing exact-file exporter allowlist now includes only the six draft
source/documentation files, using the read-only dependency/config/license entries
from PR #20. It excludes acceptance receipts, storage inventories, credentials,
operator configuration and databases. No broad prototype directory is included.

The CLI returns 0 only for successful commands; execute requires confirmed
succeeded (including its cached duplicate receipt). Refusal/failure is 1; persisted
in_flight, ambiguous, succeeded_stale and manual reconciliation require 2. A
successful status inspection returns 0 independently of the reported write state.
Standard argparse usage errors retain 2. JSON/redaction are preserved. The host,
approval validators, gateway, transport, durable ledger and retry rules are unchanged.

## Direct validation and limits

The focused initial green selection passed 62 tests in 277.08s, including all
54 existing draft tests, the then-seven process tests and clean-export execution.
The process tests run real child interpreters against real SQLite state; only the
vault backend and HTTP outcomes are synthetic, with real HTTP transport forbidden.
They inspect process return code, JSON stdout, empty stderr, durable ledger state
and a persisted synthetic remote count across separate invocations.

The broader MCP/Registry/Vault/editorial/export selection initially returned
138 passed, 24 existing internal-fixture skips and 2 failures in 197.26s. Both were
unchanged PR #19 SDK initialization timeouts while another process-heavy suite was
running. The two exact tests passed alone in 21.40s with no code, timeout or gate
changes. This observation is recorded rather than hidden or counted as a wholly
passing original run. PR #19 remains unchanged.

An additional late-edit subprocess test verifies exit 2, retention of the remote
ID and no duplicate dispatch. Final exported tests keep generated fixture state
outside the exported code tree. The root-level exact-head handoff records the
final selection results, source line numbers, commit SHAs, secret checks and CI.

All checks are direct implementer validation, not independent QA/human review.
No actual Postiz write, provider request, Docker operation, new environment,
production wiring, cleanup, deployment or merge to main was performed. No new
feature PR, DEV-052 or fabricated role execution was created.

## Fresh re-review required

Inspect the PR #20 merge separately from this remediation delta, then examine
export allowlists, local.py's outcome-to-exit mapping, subprocess fixture isolation,
all requested CLI scenarios and secret/clean-export evidence at the new exact head.
The original a4ce24d6 review does not approve modified code. Independent project
gates and required human review remain outstanding; retain Draft / MERGE NO-GO.

Final targeted selection: `python -m pytest app/tests/test_postiz_cli.py
app/tests/test_postiz_export.py -q -p no:cacheprovider` — 9 passed, 0 failed,
0 skipped in 196.25s. It includes all eight subprocess scenarios/tests and actual
exported-tree execution of read-only, draft and CLI suites, with fixture state
outside the export. No Python environment was created or installed.
