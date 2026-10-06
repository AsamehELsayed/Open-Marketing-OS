"""Shared pytest configuration for the Open Marketing OS test suite (DEV-008).

The one job here is to keep the **public** repository's suite green without
weakening the development repository's.

`scripts/package/export_public_repo.py` publishes a subset of this tree. Several
test modules read content that is deliberately withheld — internal development-run
artifacts, and the founder's private business workspace. Those tests pass here and
fail on arrival in a clean export, which would make CI red for every contributor
before a single real change is evaluated.

`internal_fixtures.PRIVATE_WORKSPACE_MODULES` names those modules. Skipping them
at module level, from here, is what makes it work: the reads happen in fixtures
and at import time, so a function-level `skipif` decorator arrives too late.

Every skip is conditional. In the development repository, where the content
exists, nothing is skipped and the whole suite runs.
"""
from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config, items):
    from app.tests.internal_fixtures import module_needs_withheld_content

    for item in items:
        module_name = item.module.__name__.rsplit(".", 1)[-1] + ".py"
        reason = module_needs_withheld_content(module_name)
        if reason:
            item.add_marker(pytest.mark.skip(reason=reason))
