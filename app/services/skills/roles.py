"""The versioned employee-role table: one table drives both role and category.

Frozen contract: plan §1.4.4. Eight employee roles, seven display categories,
one ``SKILL_ROLE_MAP`` keyed by skill id, so role and category cannot drift apart
-- they are two lookups into one table, not two independent assignments.

Upstream ships **no** ``category`` key (measured: absent in 50/50). So ``category``
is OMOS-**assigned** from this table and labelled ``"assigned"`` in the manifest.
It is never presented as parsed upstream metadata, and it is never guessed from
the description text: a keyword guess would be fabricated capability wearing a
derivation's clothes.

Fail-closed
-----------
A registered skill with no entry here raises ``ROLE_UNMAPPED``; a role with no
``CATEGORY_BY_ROLE`` entry raises ``CATEGORY_UNMAPPED``. Both fail the **whole
load** rather than skipping a record, because a partially mapped table would
silently mis-route turns -- the failure would show up as worse marketing, not as
an error.


CONTRACT DEVIATION -- MUST BE CONFIRMED BY THE PLANNER / FOUNDER
================================================================

plan.md §1.4.4 states that ``SKILL_ROLE_MAP`` has "**50 entries, one per pinned
skill, complete by construction**" and that ``9+5+5+11+7+3+3+7 = 50``. Both
statements are false for the table as written, and the arithmetic is the cause:

* ``ad-creative`` is listed **twice** -- once under ``content`` and once under
  ``paid_media``. A dict cannot hold a key twice, so the literal table yields
  **49** entries, not 50.
* ``revops`` exists on disk (it is one of the 50 vendored skill directories) and
  appears **nowhere** in the table.

Measured, not eyeballed (script output is in ``workers/w1.md``)::

    role=research     declared=9  actual=9  OK
    role=seo          declared=5  actual=5  OK
    role=cro          declared=5  actual=5  OK
    role=content      declared=11 actual=11 OK
    role=growth       declared=7  actual=7  OK
    role=paid_media   declared=3  actual=3  OK
    role=lifecycle    declared=3  actual=3  OK
    role=strategy     declared=7  actual=7  OK
    declared total: 50 / actual total: 50 / distinct slugs: 49
    duplicated: {'ad-creative': ['content', 'paid_media']}
    on disk: 50 / in table not on disk: [] / on disk not in table: ['revops']

Consequence: the plan's own acceptance check ``set(SKILL_ROLE_MAP) ==
set(registered skill_ids)`` is unsatisfiable from the literal table, and the
plan's own rule ("Fail-closed: a registered skill with no ``SKILL_ROLE_MAP``
entry raises ``ROLE_UNMAPPED`` and fails the whole load") would abort the load
of every skill because one is unmapped.

This module therefore applies the **minimum repair that makes the contract
satisfiable**, and takes the two missing decisions from the plan's own prose
rather than from invention:

1. ``ad-creative`` -> ``content``. plan.md §1.4.4 states the rule that breaks the
   tie: "one skill cannot have two homes", and "this run folds ... creative craft
   into ``content``". ``ad-creative`` *is* creative craft (hooks, headlines,
   UGC scripts), and ``content`` is also where the plan lists it first.
2. ``revops`` -> ``strategy``. plan.md §1.4.4 states "this run folds
   analytics/attribution/experimentation into ``strategy``"; ``CATEGORY_BY_ROLE``
   has no ``ops`` category, and ``revops`` is the operations sibling of
   ``analytics`` and ``attribution`` (CRM, pipeline, lead lifecycle,
   marketing-to-sales handoff -- the same operational surface). The alternative,
   ``content``, would put a revenue-operations playbook in a content queue, which
   no reading of the plan supports.

Both entries are marked ``# DEVIATION`` below. To revert to a strict reading of
the table, delete those two lines: the loader will then abort with
``ROLE_UNMAPPED: revops``, which is the contract-faithful behaviour. Nothing else
in the file needs to change, because the count is data and is never asserted.
"""
from __future__ import annotations

#: Employee roles. Eight slots, matching the eight categories below in breadth.
EMPLOYEE_ROLES = (
    "research",
    "seo",
    "cro",
    "content",
    "growth",
    "paid_media",
    "lifecycle",
    "strategy",
)

#: Role -> display category. Total over :data:`EMPLOYEE_ROLES`; a role absent
#: here is a hard ``CATEGORY_UNMAPPED`` load failure, not a default.
CATEGORY_BY_ROLE = {
    "research": "market",
    "seo": "website",
    "cro": "website",
    "content": "content",
    "growth": "offer",
    "paid_media": "acquisition",
    "lifecycle": "lifecycle",
    "strategy": "strategy",
}

#: The complete set of display categories, in the order shown in the UI.
CATEGORIES = (
    "market",
    "website",
    "content",
    "offer",
    "acquisition",
    "lifecycle",
    "strategy",
)

#: Bumped when the mapping below changes in a way that moves a skill between
#: roles or categories. Recorded in the manifest so a routing regression can be
#: dated to a table edit rather than guessed at.
ROLE_TABLE_VERSION = "omos.role-table/1"

#: skill_id -> employee role. Complete by construction against the vendored tree;
#: ``test_dev008so_skill_registry.py`` asserts the two sets are equal, so a future
#: re-pin that adds or removes a skill fails the build rather than degrading
#: routing quietly.
SKILL_ROLE_MAP: dict[str, str] = {
    # -- research (9) ----------------------------------------------------
    "customer-research": "research",
    "competitor-profiling": "research",
    "community-marketing": "research",
    "co-marketing": "research",
    "public-relations": "research",
    "events": "research",
    "influencer-marketing": "research",
    "referrals": "research",
    "prospecting": "research",
    # -- seo (5) ---------------------------------------------------------
    "seo-audit": "seo",
    "ai-seo": "seo",
    "site-architecture": "seo",
    "schema": "seo",
    "programmatic-seo": "seo",
    # -- cro (5) ---------------------------------------------------------
    "cro": "cro",
    "signup": "cro",
    "onboarding": "cro",
    "popups": "cro",
    "paywalls": "cro",
    # -- content (11) ----------------------------------------------------
    "content-strategy": "content",
    "copywriting": "content",
    "copy-editing": "content",
    "social": "content",
    "video": "content",
    "image": "content",
    "ad-creative": "content",          # DEVIATION 1 of 2 -- see module docstring.
    "competitors": "content",
    "aso": "content",
    "sales-enablement": "content",
    "marketing-ideas": "content",
    # -- growth (7) ------------------------------------------------------
    "offers": "growth",
    "pricing": "growth",
    "lead-magnets": "growth",
    "free-tools": "growth",
    "directory-submissions": "growth",
    "launch": "growth",
    "marketing-council": "growth",
    # -- paid_media (3) --------------------------------------------------
    "ads": "paid_media",
    "cold-email": "paid_media",
    # -- lifecycle (3) ----------------------------------------------------
    "emails": "lifecycle",
    "sms": "lifecycle",
    "churn-prevention": "lifecycle",
    # -- strategy (7 + 1) -------------------------------------------------
    "product-marketing": "strategy",
    "marketing-plan": "strategy",
    "marketing-psychology": "strategy",
    "analytics": "strategy",
    "attribution": "strategy",
    "ab-testing": "strategy",
    "marketing-loops": "strategy",
    "revops": "strategy",               # DEVIATION 2 of 2 -- see module docstring.
}


class RoleTableError(Exception):
    """The role table cannot be applied to a skill id. Fails the whole load."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


def role_for(skill_id: str) -> str:
    """Employee role for ``skill_id``, or ``ROLE_UNMAPPED``."""
    try:
        return SKILL_ROLE_MAP[skill_id]
    except KeyError:
        raise RoleTableError(
            "ROLE_UNMAPPED",
            f"{skill_id!r} has no SKILL_ROLE_MAP entry; the role table is "
            f"incomplete for the vendored library",
        ) from None


def category_for(skill_id: str) -> str:
    """Display category for ``skill_id``, via role then role->category.

    One chained lookup rather than a second table, which is the whole point: role
    and category cannot disagree because they are not independently assigned.
    """
    role = role_for(skill_id)
    try:
        return CATEGORY_BY_ROLE[role]
    except KeyError:
        raise RoleTableError(
            "CATEGORY_UNMAPPED",
            f"role {role!r} (for skill {skill_id!r}) has no CATEGORY_BY_ROLE entry",
        ) from None


def skills_for_role(role: str) -> tuple[str, ...]:
    """Every skill assigned to ``role``, sorted. Useful for the Settings UI."""
    return tuple(sorted(k for k, v in SKILL_ROLE_MAP.items() if v == role))


def role_counts() -> dict[str, int]:
    """Measured size of each role bucket, for diagnostics and docs.

    Recomputed from the table on every call. Nothing in this package branches on
    a remembered skill count.
    """
    counts = {role: 0 for role in EMPLOYEE_ROLES}
    for role in SKILL_ROLE_MAP.values():
        counts[role] = counts.get(role, 0) + 1
    return counts
