"""Single source of truth for project/workspace identity (DEV-008-PUBLISH-GATE).

Why this module exists
----------------------
The default project id used to be the literal ``"njm"`` — the initials of a
real third-party business whose workspace this product was originally built
around. It was not merely a cosmetic label:

- it was the ``DEFAULT`` in ~18 SQLite DDL column definitions, so it was written
  into **every fresh install's** database;
- it was returned by ``GET /api/onboarding/status``;
- it was sent to external tool providers as the ``project_id``;
- it appeared ~500 times across the test suite, so the public repository
  associated this open-source product with a named real company.

DEV-008 neutralised the seeded *business content* (name, website, markets) but
left the *identifier*, and recorded that renaming it was a founder decision.
This module is that decision, made once so it cannot drift.

Design
------
``DEFAULT_PROJECT_ID`` is ``"starter"``: generic, obviously a placeholder, not
anybody's business name, and not presented to the user as their company. The
first-run wizard then writes the user's real business into that project.

Fresh install vs. historical private data are deliberately separate concerns:

- A **fresh** install gets ``starter`` and nothing else.
- An **existing** install keeps whatever id it already has. ``njm`` was never
  deleted from a user's database, so those installs load and migrate exactly as
  before. :func:`resolve_active_project_id` finds the stored
  ``active_project_id`` first and only falls back to ``starter``, which is what
  makes the two cases safe to handle with one code path.

This module deliberately imports nothing from the package, so any module can
depend on it without creating an import cycle.
"""
from __future__ import annotations

#: The internal id of the single default workspace in a **fresh** install.
#:
#: Generic on purpose. It is never shown to the user as a business name; the
#: onboarding wizard replaces the project's display name with the real one.
DEFAULT_PROJECT_ID = "starter"

#: Internal ids that earlier versions wrote into a user's database.
#:
#: These are **read-only history**. Nothing in this codebase creates a project
#: with one of these ids any more, and nothing deletes or rewrites a user's rows
#: because of them. They exist so that:
#:
#:   - migration/diagnostic code can recognise a legacy workspace, and
#:   - the export and identity audits can prove that a *fresh* database contains
#:     none of them.
#:
#: ``njm`` is the initials of a real business. Keeping the string in this list
#: is deliberate and is the only sanctioned place it may appear in shipped
#: product code: removing it entirely would break the very compatibility check
#: that proves old installs still work.
LEGACY_PROJECT_IDS: tuple[str, ...] = ("njm",)


def is_legacy_project_id(project_id: str | None) -> bool:
    """True when ``project_id`` is a pre-DEV-008-PUBLISH-GATE workspace id."""
    return (project_id or "").strip().lower() in LEGACY_PROJECT_IDS


def company_id_for(project_id: str | None) -> str:
    """The company row id that belongs to ``project_id``.

    The schema has always conflated the two (a project's company row shares its
    id), so this is an explicit alias rather than a new concept.
    """
    value = (project_id or "").strip()
    return value or DEFAULT_PROJECT_ID
