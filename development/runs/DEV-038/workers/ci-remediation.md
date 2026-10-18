# DEV-038 CI remediation implementer handoff

Agent: general (primary execution)
Run: DEV-038
Branch: `codex/dev038-ci-repair`
Base: `3b6dff1a9fd589105c944ff52e918dfcdc6c0e50` (`origin/main`)

## Root causes and changes

- The GitHub Secret Hygiene log identifies Gitleaks 8.24.3 `generic-api-key` at `run_synthetic.py:34`, caused by the `_KEY` identifier suffix. The value is the synthetic batch row's existing 32-character database identity. Renamed only the Python symbol to `SYNTHETIC_BATCH_ID`; the literal and exact row verification remain unchanged. No scanner rule, allowlist, or exclusion changed.
- The GitHub Python 3.12 log shows the job row already `completed`/`failed` while its event list held only start/running events. `BackgroundJobs.upsert` committed the terminal row before two terminal events were written on a second connection. Added optional deferred commits for the job and execution-event repository writes, then writes the job row and ordered terminal events in one `BEGIN IMMEDIATE` transaction on the worker connection. Failure rolls the transaction back.
- The regression polls for the terminal job state with a bounded 15-second monotonic deadline, then checks the matching terminal event, terminal status event ordering, and project isolation.
- The historical DEV-037 approved commit and clean `origin/main` commit have the same full tree hash `88881ace7c42781e7db3d538d3bc84263d5c62b0`; their `app/` subtree hashes are also identical. The synthetic source guard pins both SHAs and tree identity, requires clean-base ancestry, clean checkout state, expected repair-branch name, and imported production module origins inside the checkout.

## Verification performed

- `python -m pytest --basetemp=development/runs/DEV-038/synthetic-output/pytest-lifecycle-repeat -q app/tests/test_v021_streaming.py::test_job_lifecycle_events_persist_and_isolate`: **20 consecutive passes** on Python 3.12.10.
- `python -m pytest --basetemp=development/runs/DEV-038/synthetic-output/pytest-focused -q development/runs/DEV-038/test_synthetic.py app/tests/test_v021_streaming.py`: **19 passed** on Python 3.12.10.
- Initial pytest attempt using the system temp path could not create pytest temp directories due sandbox permissions. Reran successfully with basetemp inside the ignored DEV-038 synthetic-output directory.
- Full app backend run collected **2,879 items / 3 skipped** under Python 3.12.10. The quiet run stopped producing output after about 50 tests. A verbose rerun confirmed early tests passed but stopped producing output in the local Windows sandbox during a slow test; it was interrupted without a reported test failure. The full suite remains to be verified by CI.
- Local Gitleaks is not installed. The captured PR #12 scanner finding is preserved in the task evidence; the clean-history Gitleaks result must be confirmed by replacement PR CI.
- The prior PR #12 Windows, frontend, packaging, and provenance checks passed; replacement-branch CI must rerun them.

No real provider request, AI inference, network operation, or real-client database write was made. The only database copied for the local synthetic tests is the pinned, ignored DEV-038 approved-derived fixture; it is outside Git.
