"""Skip markers for tests that depend on non-public repository content (DEV-008).

The **public** repository produced by `scripts/package/export_public_repo.py`
is a subset of this development repository. Several areas are deliberately
withheld because they are internal engineering history or the founder's private
business workspace rather than product:

- ``development/runs/**``  — internal development-run artifacts (gate packets,
  migration fragments, parity fixtures)
- ``company/``, ``production/``, ``strategy/``, ``state/`` — the founder's
  private marketing workspace for a real third-party business

Tests that read those paths pass here and **fail on arrival in a clean export**.
Without handling, a fresh clone of the public repository has a red test suite,
and CI — which runs the full ``python -m pytest -q`` — is red before it has
evaluated a single real change. The first thing a technical evaluator does with
a public repository is clone it and run its tests, so that matters.

These are not product tests: they are regression tests for internal artifacts
and for a private workspace that no public user will ever have. Skipping them
with an explicit reason is the honest behaviour. Publishing internal run history
to keep them green would be strictly worse.

Every skip is conditional on the content being absent, so **nothing is skipped
in the development repository** and the full suite still runs locally.

Mechanism
---------
``PRIVATE_WORKSPACE_MODULES`` lists whole test modules that read withheld
paths. They are skipped at module level from ``conftest.py``, which also covers
reads that happen in fixtures or at import time — a function-level ``skipif``
cannot, because the read has already happened by then.
"""
from __future__ import annotations

import functools
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Directories withheld from the public repository.
INTERNAL_RUNS = REPO_ROOT / "development" / "runs"
PRIVATE_WORKSPACE_DIRS = (
    REPO_ROOT / "company",
    REPO_ROOT / "production",
    REPO_ROOT / "strategy",
    REPO_ROOT / "state",
)

#: Test modules (relative to ``app/tests``) that read withheld paths.
#: Keep this list explicit and short: a module belongs here only if it reads
#: internal run artifacts or the private workspace, not merely because it is old.
PRIVATE_WORKSPACE_MODULES = (
    "test_w3_adapters.py",        # imports the founder's company/production/strategy/state files
    "test_w5_pages.py",           # drives reimport/reindex over the private workspace
    "test_ux.py",                 # same, via the legacy page surface
    "test_spa_api.py",            # same, via the React API surface
    "test_foundation.py",         # reads the private workspace seed
    "test_dev007_mcp.py",         # module-level migration-fragment path
    "test_dev_parallel.py",       # needs the dev scheduler + worktree layout
)

#: DEV-008-PUBLISH-GATE: ``test_remediation.py`` used to be listed here. Its two
#: offending tests read the founder's real ``company/company.yaml`` and
#: ``production/approval-queue.md`` and asserted that real business data back at
#: us. Both now build their own obviously-synthetic fixtures in ``tmp_path``, so
#: the module runs — and is actually verified — in the public export. Dropping a
#: module from this list makes its tests *stricter* in CI, never weaker.

_SKIP_REASON_INTERNAL_RUNS = (
    "depends on internal development-run artifacts under development/runs/, "
    "which are excluded from the public repository by design "
    "(scripts/package/export_public_repo.py)"
)
_SKIP_REASON_PRIVATE_WORKSPACE = (
    "depends on the founder's private business workspace "
    "(company/, production/, strategy/, state/), which is deliberately not "
    "published (scripts/package/export_public_repo.py)"
)

internal_runs_available = INTERNAL_RUNS.is_dir()
private_workspace_available = all(d.is_dir() for d in PRIVATE_WORKSPACE_DIRS)


def requires_internal_runs(func):
    """Skip a test unless `development/runs/` is present."""
    return pytest.mark.skipif(
        not internal_runs_available, reason=_SKIP_REASON_INTERNAL_RUNS
    )(func)


def requires_private_workspace(func):
    """Skip a test unless the founder's private workspace is present."""
    return pytest.mark.skipif(
        not private_workspace_available, reason=_SKIP_REASON_PRIVATE_WORKSPACE
    )(func)


def module_needs_withheld_content(module_name: str) -> str | None:
    """Return a skip reason for `module_name`, or None if it can run anywhere."""
    if module_name in PRIVATE_WORKSPACE_MODULES and not private_workspace_available:
        return _SKIP_REASON_PRIVATE_WORKSPACE
    return None

