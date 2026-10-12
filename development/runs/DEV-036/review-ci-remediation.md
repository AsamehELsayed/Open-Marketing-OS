VERDICT: APPROVE

# DEV-036 Independent Re-review — CI Sentinel Allowlist Remediation

**Role:** Independent reviewer (general lane)
**Checkout:** `development/worktrees/DEV-036-citation-diagnostics`
**Branch / PR candidate:** `codex/dev-036-citation-diagnostics` / commit `e4097af1d383aa390b49438b80ad2739f93586d9`
**Baseline source commit:** `b7284427acf947c16bf4c46591edcf1e1ae555a4`
**Started:** `2026-10-12 19:06:40 UTC`
**Completed:** `2026-10-12 19:07:47 UTC`

## Scope and decision

I reviewed the approved DEV-036 plan, the full PR source/test delta, the prior final review `review-remediation2.md`, and the CI remediation and QA records. I also inspected the scanner's allowlist selection and match-suppression code. The current uncommitted CI-only code delta is two entries in `scripts/package/secret_scan.py`; the other current changes are DEV-036 run evidence and pytest scratch directories.

The two entries are appropriately narrow and justified by deterministic privacy regressions. They allow exactly the same synthetic GitHub-shaped sentinel in exactly `app/tests/test_dev031_business_workspace.py` and `app/tests/test_dev032_campaign_ai.py`. The literal contains `FAKE` and is used to exercise redaction of a credential-shaped token; the tests assert the raw value is absent from diagnostics and saved/API data. The sentinel exception preserves that test purpose while allowing the public repository scanner to run cleanly.

The scanner normalizes a repository-relative path, selects sentinel literals only for that exact path, and suppresses a finding only when the matched candidate equals one of those exact literals. The scanner patterns and matching logic are unchanged. There is no pattern, prefix, glob, directory, or test-file-wide exemption. A different token, including a real credential, remains a finding. The two entries therefore do not mask real secrets beyond the deliberately fake, exact test sentinel.

## Five-axis review

- **Correctness:** The exception aligns with the scanner's existing `(path, literal, reason)` policy and matches only the candidate the GitHub-token pattern detects. The current remediation does not alter citation decisions or diagnostics.
- **Readability:** Both entries use a clear, consistent reason describing the citation diagnostic redaction regression sentinel.
- **Architecture:** The change uses the existing allowlist mechanism; it adds no scanner mode or new abstraction. The broader PR's shared citation detector and boolean wrappers remain as previously approved.
- **Security:** Static inspection confirms exact-path and exact-literal matching. QA reports the secret scan passed over 549 text files with 14 allowlisted sentinels, zero real-secret hits, and zero home-path hits. The synthetic value remains confined to deterministic privacy tests and its exact allowlist entries.
- **Performance:** Two additional tuple entries have negligible cost in the existing scanner iteration and do not change its scanning work or matching algorithm.

## Scope and regression evidence

- Compared with the approved plan and prior final review, this remediation changes no application code, citation validator, tests, project scoping, persistence/atomic-write path, provider safeguard, or Northstar data. The feature source and test delta remains the previously reviewed DEV-036 change.
- QA reports the 17 focused deterministic citation/privacy remediation tests passed (114 deselected), and the public-repository secret scan passed.
- QA's current full local three-file suite encountered Windows sandbox `tmp_path` setup errors: **85 passed, 46 setup errors, 1 warning**. Its worktree-basetemp retry was stopped after 102 seconds without a result. This is an environment-limited local run, not a full passing result.
- The prior independent QA/review record documents the approved full targeted suite at **131 passed, 1 existing deprecation warning**, before this sentinel-only adjustment. That prior result is not represented as a post-adjustment full-suite run.
- No real model/provider calls were made. No Northstar data was read or changed.

## Decision boundary

No blocking finding remains in the allowlist remediation or the reviewed DEV-036 delta, so this independent code review is approved. This review approval does **not** satisfy or waive the required GitHub CI gate. Required CI must still pass on the PR candidate before merge; the local QA setup errors and interrupted retry do not substitute for that gate.
