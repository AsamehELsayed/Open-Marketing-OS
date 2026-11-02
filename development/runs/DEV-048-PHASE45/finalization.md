# Phase 4.5 security and CI finalization

Primary agent only, following the founder's NO DELEGATION instruction. PR #19 must remain Draft. Independent QA/review is still BLOCKED; direct testing/self-review cannot satisfy it.

## Verified initial findings

GitHub Actions run `41444984138` at original feature commit `6fdfc7b81468a6625efb89434fb0ac1a45065454` reported one PR-range Gitleaks 8.24.3 finding: `generic-api-key`, `development/runs/DEV-048-PHASE45/demo-evidence.json`, line 1, `idempotency_key`.

Verification recomputed SHA-256 over the same six canonical fields used by `server.py` and `host.py`: project/campaign/deliverable identity, approved version, platform and text. It exactly matched the original evidence value. The fixture's identifiers and copy originate in `demo.seed`; no account credential, token, random provider secret, salt or vault value participates. The digest has no authentication role. This specific finding is a verified synthetic-digest false positive. The full value is deliberately omitted from public evidence.

The host also reuses the content digest as an internal Registry approval marker after reading persisted editorial approval. That marker is not a provider authentication secret: a forged marker cannot bypass the registered handler's project, status, exact-version and approved-history checks, as the negative tests demonstrate.

Gitleaks was obtained from its official v8.24.3 release, verified against its published checksum, and run with default rules and redacted reporting. The original PR history reproduces exactly that finding. A follow-up commit alone cannot remove it from the PR's complete commit history.

The full reachable baseline history/current tracked tree additionally match five unchanged test-fixture locations from baseline commit `a08acb0d14c9267290afc797ca41ac7e757d4fd8`: `test_dev007_hardening.py:580`, `test_w4_retrieval.py:21`, `test_dev014_knowledge_core.py:146`, `test_w3_attachments.py:78`, `test_dev008so_event_catalog.py:577` (all under `app/tests/`). Source contexts are fake transport/secret-ref refusal, retrieval secret fixtures, quarantined secret-bearing attachments, indexing cleanup and poisoned-metadata tests. They are not Phase 4.5 additions. They are reported separately, not silently suppressed; no global Gitleaks config, ignore fingerprint, rule or CI exit status is changed. Do not describe a whole-repository history scan as clean when these existing matches remain.

Original CI also found a real portability defect in `test_real_sdk_timeout_cleans_up`: a Linux server can start/list successfully before a half-second deadline, so expecting startup to time out is invalid. The test now stalls an identified initialization operation after real SDK process creation, waits for the actual transport deadline, and verifies process cleanup. No production timeout or security boundary is weakened. The other eight original jobs passed, including backend Python 3.12; backend Python 3.11 failed only this timeout test.

## Evidence changes

The public demo summary verifies the digest against canonical input, then omits its value and summarizes synthetic run IDs. Actual server/host digest generation, exact payload validation, equality assertions and default-deny behavior remain. A regression checks both that the original raw payload is unchanged and that invalid digest evidence is rejected. Unnecessary Windows user/install paths in committed scan reports and logs are replaced with relative paths or `<python-install>`; operational messages/counts remain. Evidence text is normalized to UTF-8/LF. No real credentials or sensitive client/account information were found in Phase 4.5 changes.

## Safest history remediation proposal — authorization required

1. Finish and test the sanitized tree, preserve the current remote branch SHA and review the exact diff.
2. Prepare a replacement commit locally with the identical tested tree and the existing main base as its parent. Scan the complete proposed PR range with unchanged Gitleaks 8.24.3 rules. Do not rewrite main, tags or unrelated branches.
3. Request founder authorization for the single feature-branch history replacement. Only after authorization, push the candidate to the existing PR branch using an explicit `--force-with-lease=<branch>:<observed-remote-sha>`. If the remote changed, stop and reassess; do not override the lease.
4. Verify PR #19 still Draft, confirm the original offending commit is absent from its new ancestry, and run full CI on the exact replacement head. A fresh scan of the PR's entire new commit range must pass. Historical GitHub artifacts/old commit URLs may remain accessible; a branch rewrite is not a claim that GitHub garbage-collected old objects.

Until authorized history remediation and final CI are verified, security remains NO-GO. No force-push has been authorized by this finalization request. A normal follow-up push may demonstrate that the snapshot is sanitized while honestly leaving history-wide security blocked.

## Local verification so far

- Combined prototype/campaign/registry/vault/export regressions: **218 passed, 24 skipped in 233.67s**, exit 0. The extra case covers public evidence sanitization. A subsequent focused sanitizer regression on its final canonical-field selection passed **1 test in 8.11s**, exit 0. Existing pyreadline console teardown emitted an ignored invalid-handle error after that focused result.
- Fresh real SDK demo exited 0; public summary contains no literal SHA-256 values, keeps canonical-match/length/algorithm facts, and retains actual default-deny/determinism/process receipts.
- Unchanged Gitleaks 8.24.3 defaults: sanitized current PR-file snapshot **0 findings**; original complete PR range **1 verified synthetic-digest finding**; whole repository history **6 findings** (that one plus five unchanged baseline fixture matches). Current whole tracked tree has only those five unchanged baseline matches. No unrelated finding is suppressed.
- Repository's own secret scanner on a tracked-tree snapshot: **PASS**, zero real-secret/home-path findings. No unnecessary absolute user/install paths remain in tracked Phase 4.5 evidence. The original paths still exist in the old PR commit until authorized history remediation.
- Fresh complete prototype rerun on final sanitizer/timeout code: **28 passed in 171.26s**, exit 0; none skipped. See `tests-finalization-prototype.txt`. Final-head CI is recorded separately. None of these direct checks satisfy independent QA/review.

## Required independent review process

- Keep Draft and MERGE NO-GO. Do not fabricate `qa`/`reviewer` execution records or relabel `direct_validation`/`self_review` as independent work.
- An independent human must reproduce QA on the exact final PR head with the pinned SDK: approved/stale/pending revision checks, client isolation, default denial, vault-reference refusal, no outbound provider access, audit redaction and subprocess cleanup. Capture commands/results, SHA, dates, reviewer identity and artifact.
- A separate independent review execution must assess the final diff, research/license claims, security evidence, full PR-range secret scan and complete CI results. It must give an explicit verdict and concrete findings. Prefer a different human from implementation and QA; at minimum the QA and review must be separate real executions under the documented policy.
- Enter genuine independent records only after receiving that evidence; require QA PASS and an explicit independent APPROVE covering the exact head. If fixes follow, repeat affected QA and independent re-review. No automated/delegated reviewer may be launched without new founder authorization. No review requests/outreach are sent by this work.
- Only then evaluate the existing `dev_run.py` approval/complete gates and whether Draft may be removed. Nothing in this document authorizes merge, publication, deployment, accounts or provider calls.
