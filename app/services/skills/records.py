"""The skill registry record and its validation report (DEV-008-SKILLS-OPS).

`MarketingSkillRecord` is the only vocabulary this package uses to describe a
playbook. It deliberately has **no** ``parameters``, ``handler``,
``permission_level`` and **no** ``side_effect``: skills are knowledge, tools are
action, and the two types share no field (plan §1.4.6). Nothing here imports
``app.services.tools``.

Provenance honesty (plan §1.1.1)
--------------------------------
Every field on the record declares where its value came from, and the manifest
repeats that label per field:

===============  ============  ==================================================
Field            Class         Rule
===============  ============  ==================================================
``skill_id``     DERIVED       ``path.name`` of the skill directory.
``name``         PARSED        frontmatter ``name``. Must equal ``skill_id``.
``description``  PARSED        frontmatter ``description``, unquoted, collapsed.
``version``      PARSED        ``frontmatter.metadata.version``, must be semver.
``source``       ASSIGNED      :data:`SKILL_SOURCE`.
``path``         DERIVED       ``.agents/skills/<skill_id>`` -- POSIX, RELATIVE.
``triggers``     DERIVED       mined from ``<dir>/evals/evals.json``. Never typed.
``related_*``    DERIVED       ``## Related Skills`` + description-tail ``see``.
``category``     ASSIGNED      ``CATEGORY_BY_ROLE[SKILL_ROLE_MAP[skill_id]]``.
``checksum``     DERIVED       sha256 of the LF-normalised ``SKILL.md``.
``eval_count``   DERIVED       ``len(evals)`` in ``evals.json``.
``enabled``      ASSIGNED      per-request; never computed at import time.
``validation_*`` DERIVED       one of :data:`VALIDATION_STATUSES`.
===============  ============  ==================================================

``path`` is relative **on purpose**. ``app.contracts.events.sanitize_user_text``
redacts absolute Windows and Unix paths, so an absolute path in any metadata
that reaches the wire renders as ``[REDACTED]`` -- which would make the field
useless for debugging while looking like a working value.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

#: Upstream pin this vendored copy came from. The tag/commit/license live in
#: the manifest and the lockfile, not here, so a re-pin edits one file.
SKILL_SOURCE = "coreyhaines31/marketingskills"

#: Upstream repository URL as recorded in the manifest.
UPSTREAM_REPOSITORY = "https://github.com/coreyhaines31/marketingskills"

#: The complete set of validation outcomes. A record outside this set is a bug.
VALIDATION_STATUSES = ("valid", "invalid", "missing")

#: Frozen reason codes. Every gate that refuses a record names one of these, so
#: a failure is always attributable and never an anonymous skip.
REASON_CODES = (
    "NAME_DIR_MISMATCH",
    "VERSION_MISSING",
    "VERSION_MALFORMED",
    "DESCRIPTION_MISSING",
    "FRONTMATTER_UNDELIMITED",
    "FRONTMATTER_UNPARSEABLE",
    "NO_SKILL_MD",
    "SKILL_DIR_UNREADABLE",
    "CATEGORY_UNMAPPED",
    "ROLE_UNMAPPED",
    "DUPLICATE_SKILL_ID",
)

#: Descriptions are clamped to this many characters. Truncation is recorded in
#: the manifest as ``description_truncated`` rather than hidden.
DESCRIPTION_MAX_CHARS = 1024

#: A single eval prompt is clamped to this many characters before de-duplication.
TRIGGER_MAX_CHARS = 512

#: Hash scheme identifier. Recorded in both generated files so a future verifier
#: never has to guess which normalisation produced a digest.
HASH_SCHEME = "sha256-utf8-lf"


def normalize_bytes(raw: bytes) -> bytes:
    """Decode to UTF-8 and normalise line endings to LF, then re-encode.

    This is the single normalisation used by every checksum in the manifest and
    the lockfile. It is what makes the CRLF working tree hash equal to the LF git
    blob hash, so a recorded digest is directly comparable to ``git hash-object``
    and a re-pin can be verified with no new tooling.
    """
    return raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def normalize_text(raw: bytes) -> str:
    """Same normalisation as :func:`normalize_bytes`, returning ``str``."""
    return normalize_bytes(raw).decode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Lowercase hex sha256 of already-normalised bytes."""
    return hashlib.sha256(data).hexdigest()


def library_checksum(checksums: dict[str, str]) -> str:
    """Order-independent checksum over ``{skill_id: checksum}``.

    Built from ``sorted(...)`` so that reordering a manifest cannot change the
    result -- a reorder is not drift, and a drift gate that fires on a reorder
    trains people to ignore it. Depends on nothing but the file tree, so it can
    be recomputed from a cold clone with no access to the manifest.
    """
    payload = "".join(f"{skill_id}:{checksums[skill_id]}\n" for skill_id in sorted(checksums))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MarketingSkillRecord:
    """One vendored playbook, as the product understands it.

    Frozen: records are shared across threads (the router hands the same tuple to
    every employee), so nothing here may be mutated after construction.
    """

    skill_id: str
    name: str
    description: str
    version: str
    source: str
    path: str
    triggers: tuple[str, ...] = ()
    related_skills: tuple[str, ...] = ()
    unresolved_related: tuple[str, ...] = ()
    category: str = ""
    checksum: str = ""
    eval_count: int = 0
    enabled: bool = True
    validation_status: str = "valid"
    #: Reason code when ``validation_status != "valid"``; empty when valid.
    validation_reason: str = ""
    #: True when the description was clamped to ``DESCRIPTION_MAX_CHARS``. The
    #: clamp exists so a pathological upstream description cannot blow up an
    #: event row; hiding that it happened would be a lie about the data.
    description_truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict; tuples become lists, matching the manifest file."""
        data = asdict(self)
        for key, value in data.items():
            if isinstance(value, tuple):
                data[key] = list(value)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MarketingSkillRecord":
        """Inverse of :meth:`to_dict`. Unknown keys are ignored, not fatal."""
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in data.items() if k in known}
        for key in ("triggers", "related_skills", "unresolved_related"):
            if key in kwargs and kwargs[key] is not None:
                kwargs[key] = tuple(kwargs[key])
        return cls(**kwargs)

    @property
    def is_valid(self) -> bool:
        """True only for a record the router is allowed to inject."""
        return self.validation_status == "valid"


@dataclass(frozen=True)
class SkillValidationReport:
    """What one loader run measured.

    This is the object the ``MISSING``/``INVALID`` numbers are read from. The
    counts are properties of a run over a file tree, so they are recomputed every
    time and can never be a hardcoded constant.
    """

    root: str
    total: int
    valid: tuple[MarketingSkillRecord, ...] = ()
    missing: tuple[str, ...] = ()
    invalid: tuple[tuple[str, str], ...] = ()
    library_checksum: str = ""
    file_count: int = 0
    #: Non-fatal observations, e.g. a skill with no ``evals/evals.json``.
    warnings: tuple[str, ...] = field(default=())

    @property
    def valid_count(self) -> int:
        return len(self.valid)

    @property
    def missing_count(self) -> int:
        return len(self.missing)

    @property
    def invalid_count(self) -> int:
        return len(self.invalid)

    @property
    def skill_ids(self) -> tuple[str, ...]:
        return tuple(record.skill_id for record in self.valid)

    def summary(self) -> str:
        """The one-line form the loader prints and the tests assert against."""
        return (
            f"TOTAL: {self.total}  VALID: {self.valid_count}  "
            f"MISSING: {self.missing_count}  INVALID: {self.invalid_count}  "
            f"FILES: {self.file_count}"
        )

    def reason_for(self, skill_id: str) -> str | None:
        """Reason code recorded for ``skill_id``, or ``None`` if it is valid."""
        for candidate, reason in self.invalid:
            if candidate == skill_id:
                return reason
        return None
