# DEV-038 source provenance approval

The founder approved a strictly verified logical-equivalence guard for the offline synthetic acceptance run on 2026-10-15 (Africa/Cairo), after QA and review blocked on the original Northstar DB's changed byte hash.

Read-only evidence before approval:

- Previously pinned original DB SHA-256: `f54a384efbbe789e0c7f2ca2eafa9563a4796e08fbd952cc7fe7f790f9eac507`; current SHA-256: `9c1aa99a905408c2a4efee5aea585bc365996560a2832a6165eb563ea997f99d`.
- Previously approved-derived isolated DB `synthetic-output/remediation-03/marketing.db` SHA-256: `7ad2a03f8e0b71a5c23ba6171e58fd2bbffd99bc416f782012e7d92f35fa0420`.
- Both databases passed `PRAGMA quick_check`, had user version 14, schema version 68, and the same 62 schema objects with the same SQL definitions.
- Every row in the current source was present in the approved-derived copy. That copy had exactly four extra rows: one synthetic campaign batch, deliverable, revision, and model call from the prior DEV-038 run. No other table differed.

Approval scope: retain the exact-hash path and add a fail-closed second path that verifies the derived copy's pinned bytes, schema and full row multiset equivalence after excluding only those four identified synthetic rows. Preserve read-only input inspection and unchanged source bytes during each run. Do not approve arbitrary new data, drop source isolation, alter factual validation, or make provider requests. A fresh complete synthetic run, independent QA, and independent re-review remain required.

## Clean-main source equivalence evidence (2026-10-16)

The replacement branch was created from fetched `origin/main` at `3b6dff1a9fd589105c944ff52e918dfcdc6c0e50`.

- Approved DEV-037 base: `7136389aed293c152fb387de2a2f97bc1cd2dd47`.
- The approved base tree and clean-main tree both equal `88881ace7c42781e7db3d538d3bc84263d5c62b0`.
- The `app/` subtree for both commits equals `c0ed202e1f6dcf5fb00d1b054516720ed6d1b350`.
- `git diff --quiet 7136389... 3b6dff1... -- app` exited 0.

The complete source tree is identical, so clean `main` preserves every approved DEV-037 file and byte. The synthetic runner now pins both commits and requires exact full-tree equivalence, clean-base ancestry, a clean checkout, and source module origins within the checkout.
