"""The marketing-skills runtime: parser, records, role table, loader, manifest.

A **skill** is a playbook: a vendored ``SKILL.md`` from
``coreyhaines31/marketingskills``. It is knowledge, not capability. Nothing in
this package has ``parameters``, a ``handler``, a ``permission_level`` or a
``side_effect``, and nothing here imports ``app.services.tools``. The two
vocabularies stay separate on purpose, because collapsing them is how a marketing
playbook silently acquires the power to spend money.

Import surface (kept small so the router and the API can depend on it without
dragging in the generator)::

    from app.services.skills import (
        load_registry,          # the loader
        SkillRegistry,          # the immutable in-memory view
        MarketingSkillRecord,   # one playbook
        SkillValidationReport,  # what one run measured
        resolve_library_root,   # where the library lives
        SkillParseError,        # a named, attributable parse failure
    )

``app.services.skills.manifest`` is imported explicitly when the manifest or the
lockfile is needed; ``router`` and ``tree`` are owned by other packets in this
run and are not imported here, so a half-written module in a parallel worker can
never break the registry.
"""
from __future__ import annotations

from .parser import SkillParseError
from .records import (
    DESCRIPTION_MAX_CHARS,
    HASH_SCHEME,
    REASON_CODES,
    SKILL_SOURCE,
    TRIGGER_MAX_CHARS,
    VALIDATION_STATUSES,
    MarketingSkillRecord,
    SkillValidationReport,
    library_checksum,
    normalize_bytes,
    sha256_hex,
)
from .registry import (
    ENV_SKILLS_DIR,
    SETTINGS_DISABLED_KEY,
    SkillRegistry,
    SkillRegistryUnavailable,
    load_registry,
    read_disabled_setting,
    resolve_library_root,
    verify_checksums,
)
from .roles import (
    CATEGORIES,
    CATEGORY_BY_ROLE,
    EMPLOYEE_ROLES,
    ROLE_TABLE_VERSION,
    SKILL_ROLE_MAP,
    RoleTableError,
    category_for,
    role_for,
)

__all__ = [
    # records
    "MarketingSkillRecord",
    "SkillValidationReport",
    "SKILL_SOURCE",
    "VALIDATION_STATUSES",
    "REASON_CODES",
    "HASH_SCHEME",
    "DESCRIPTION_MAX_CHARS",
    "TRIGGER_MAX_CHARS",
    "normalize_bytes",
    "sha256_hex",
    "library_checksum",
    # parser
    "SkillParseError",
    # roles
    "EMPLOYEE_ROLES",
    "CATEGORY_BY_ROLE",
    "CATEGORIES",
    "SKILL_ROLE_MAP",
    "ROLE_TABLE_VERSION",
    "RoleTableError",
    "role_for",
    "category_for",
    # registry
    "SkillRegistry",
    "SkillRegistryUnavailable",
    "load_registry",
    "resolve_library_root",
    "read_disabled_setting",
    "verify_checksums",
    "ENV_SKILLS_DIR",
    "SETTINGS_DISABLED_KEY",
]
