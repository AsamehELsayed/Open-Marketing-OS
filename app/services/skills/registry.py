"""The skill library loader (DEV-008-SKILLS-OPS).

Reads the vendored library off disk and produces a
:class:`~app.services.skills.records.MarketingSkillRecord` per skill directory.
No content is authored here: every field is parsed, derived or assigned, and the
manifest labels which.

Where the library lives
-----------------------
Resolution order, first existing wins (plan §1.5.2)::

    1. $OMOS_SKILLS_DIR                 -- test / CI seam
    2. app.paths.bundle_root()/.agents/skills   -- the frozen, read-only install
    3. app.paths.user_data_root()/.agents/skills -- source checkout
    4. <repo>/.agents/skills             -- last resort

The frozen install location comes **before** the user-writable one on purpose:
the library is read-only product data, so a user-writable copy must never be able
to shadow the shipped playbooks.

If no root resolves the registry is **unavailable, not empty** -- an absent
playbook library must not take chat down. The build gate
(``python -m app.services.skills.manifest --verify``) still fails closed, so this
path cannot reach a shipped artifact.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .parser import (
    SkillParseError,
    is_valid_skill_id,
    parse_description,
    parse_frontmatter,
    parse_name,
    parse_version,
    partition_related,
    read_evals,
    related_skills_from_description,
    related_skills_from_section,
)
from .records import (
    HASH_SCHEME,
    SKILL_SOURCE,
    MarketingSkillRecord,
    SkillValidationReport,
    library_checksum,
    normalize_bytes,
    sha256_hex,
)
from .roles import RoleTableError, category_for

#: Environment override for the library location (test/CI seam).
ENV_SKILLS_DIR = "OMOS_SKILLS_DIR"

#: ConfigService setting holding the comma-separated list of disabled skill ids.
SETTINGS_DISABLED_KEY = "skills_disabled"

#: Library location as reported to callers. Relative on purpose -- see
#: :meth:`SkillRegistry.to_dict`.
LIBRARY_ROOT_LABEL = ".agents/skills"

#: Reason code for a directory whose id is already claimed.
_DUPLICATE = "DUPLICATE_SKILL_ID"


class SkillRegistryUnavailable(RuntimeError):
    """No skill library root could be resolved. Never raised for a bad skill."""


def _repo_root() -> Path:
    """The repository checkout, i.e. the last-resort library location.

    Named rather than inlined at the call site so a test can point it away and
    exercise the genuinely-unavailable path without patching ``pathlib``.
    """
    return Path(__file__).resolve().parents[3]


#: Default location of the generated manifest, relative to the repo root.
MANIFEST_RELPATH = Path("docs") / "marketing-skills-manifest.json"


def resolve_library_root(explicit: str | Path | None = None) -> Path:
    """Return the first existing library root, or raise :class:`SkillRegistryUnavailable`.

    An explicit argument (including ``OMOS_SKILLS_DIR``) wins so a test can point
    the loader at a temp copy and prove the drift gate bites.
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = (os.environ.get(ENV_SKILLS_DIR, "") or "").strip()
    if env:
        candidates.append(Path(env))

    from app import paths as app_paths

    candidates.append(app_paths.bundle_root() / ".agents" / "skills")
    candidates.append(app_paths.user_data_root() / ".agents" / "skills")
    candidates.append(_repo_root() / ".agents" / "skills")

    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise SkillRegistryUnavailable(
        "no marketing-skills library found; tried: "
        + ", ".join(str(c) for c in candidates)
    )



def _manifest_gaps(
    root: Path, manifest_path: str | Path | None | bool, warnings: list[str]
) -> tuple[str, ...]:
    """Skill ids the manifest promises that are absent from ``root``.

    This is what makes ``MISSING`` measured rather than a constant. It reads the
    manifest JSON directly (no import of :mod:`manifest`) to keep the dependency
    one-way: the manifest is generated *from* the registry, never the reverse.

    ``manifest_path=False`` skips the comparison entirely -- used when a test
    points the loader at a temp tree, where the repo manifest says nothing useful.
    """
    if manifest_path is False:
        return ()
    if manifest_path is None:
        candidate = _repo_root() / MANIFEST_RELPATH
    else:
        candidate = Path(manifest_path)
    if not candidate.is_file():
        warnings.append(f"manifest absent at {candidate.name}: MISSING not measured")
        return ()

    try:
        import json

        document = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        warnings.append(f"manifest unreadable at {candidate.name}: {type(exc).__name__}")
        return ()

    promised = {
        str(entry.get("skill_id"))
        for entry in document.get("skills", [])
        if isinstance(entry, dict) and entry.get("skill_id")
    }
    on_disk = {p.name for p in _skill_dirs(root)}
    return tuple(sorted(promised - on_disk))


def read_manifest(path: str | Path | None = None) -> dict[str, Any] | None:
    """Read a generated manifest file, or ``None`` when it is absent/unreadable."""
    import json

    candidate = Path(path) if path else _repo_root() / MANIFEST_RELPATH
    if not candidate.is_file():
        return None
    try:
        document = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def _read_skill_md(directory: Path) -> tuple[str, bytes]:
    """Return ``(normalised_text, normalised_bytes)`` for a skill's SKILL.md."""
    try:
        raw = (directory / "SKILL.md").read_bytes()
    except OSError as exc:
        raise SkillParseError("SKILL_DIR_UNREADABLE", str(exc)) from exc
    normalised = normalize_bytes(raw)
    return normalised.decode("utf-8"), normalised


def _skill_dirs(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def count_library_files(root: Path) -> int:
    """Regular files under ``root``, recursive. The manifest's ``file_count``."""
    return sum(1 for p in root.rglob("*") if p.is_file())


def _build_record(
    directory: Path, *, registered: set[str]
) -> tuple[MarketingSkillRecord | None, str | None, str | None]:
    """Build one record. Returns ``(record, reason_code, warning)``.

    ``record`` is ``None`` when the skill is refused; ``reason_code`` is then one
    of :data:`~app.services.skills.records.REASON_CODES`. Never raises for a bad
    skill: a defect is collected and reported, not thrown at the caller.
    """
    skill_id = directory.name
    warning: str | None = None

    if not is_valid_skill_id(skill_id):
        return None, "SKILL_DIR_UNREADABLE", f"{skill_id}: unusable directory name"

    if not (directory / "SKILL.md").is_file():
        return None, "NO_SKILL_MD", None

    try:
        text, normalised = _read_skill_md(directory)
    except SkillParseError as exc:
        return None, exc.reason_code, None
    except UnicodeDecodeError:
        return None, "FRONTMATTER_UNPARSEABLE", None

    try:
        frontmatter, _parser_name = parse_frontmatter(text)
    except SkillParseError as exc:
        return None, exc.reason_code, None

    try:
        name = parse_name(frontmatter)
    except SkillParseError as exc:
        return None, exc.reason_code, None
    if name != skill_id:
        return None, "NAME_DIR_MISMATCH", None

    try:
        description, truncated = parse_description(frontmatter)
    except SkillParseError as exc:
        return None, exc.reason_code, None

    try:
        version = parse_version(frontmatter)
    except SkillParseError as exc:
        return None, exc.reason_code, None

    try:
        category = category_for(skill_id)
    except RoleTableError as exc:
        return None, exc.reason_code, None

    triggers, eval_count, evals_warning = read_evals(directory / "evals" / "evals.json")
    if evals_warning:
        warning = f"{skill_id}: evals/evals.json {evals_warning}"

    candidates = related_skills_from_section(text) + related_skills_from_description(
        description
    )
    related, unresolved = partition_related(
        candidates, self_id=skill_id, registered=registered
    )

    record = MarketingSkillRecord(
        skill_id=skill_id,
        name=name,
        description=description,
        version=version,
        source=SKILL_SOURCE,
        path=f".agents/skills/{skill_id}",
        triggers=triggers,
        related_skills=related,
        unresolved_related=unresolved,
        category=category,
        checksum=sha256_hex(normalised),
        eval_count=eval_count,
        enabled=True,
        validation_status="valid",
        description_truncated=truncated,
    )
    return record, None, warning


@dataclass(frozen=True)
class SkillRegistry:
    """An immutable, in-memory view of the vendored library.

    Built once per process by :func:`load_registry` and then handed around by
    reference. ``enabled`` is **not** baked into the records: it is a per-request
    answer, so a Settings toggle takes effect on the next turn without a reload.
    """

    root: Path
    report: SkillValidationReport
    #: skill ids that the manifest records but that are absent on disk.
    manifest_missing: tuple[str, ...] = ()

    # -- construction ----------------------------------------------------

    @classmethod
    def load(
        cls,
        *,
        root: str | Path | None = None,
        disabled: str | Iterable[str] | None = None,
        manifest_path: str | Path | None | bool = None,
    ) -> "SkillRegistry":
        root_path = resolve_library_root(root)
        directories = _skill_dirs(root_path)

        # Pass 1: identity only. A cross-reference can only be resolved against
        # a set of ids that is already known, so ids are collected before any
        # record is built. This is not two loads -- it is one walk and one map.
        registered = {d.name for d in directories if is_valid_skill_id(d.name)}

        records: list[MarketingSkillRecord] = []
        invalid: list[tuple[str, str]] = []
        warnings: list[str] = []
        checksums: dict[str, str] = {}
        seen: set[str] = set()

        for directory in directories:
            if directory.name in seen:
                invalid.append((directory.name, _DUPLICATE))
                continue
            seen.add(directory.name)
            record, reason, warning = _build_record(directory, registered=registered)
            if warning:
                warnings.append(warning)
            if record is None:
                invalid.append((directory.name, reason or "SKILL_DIR_UNREADABLE"))
                continue
            records.append(record)
            checksums[record.skill_id] = record.checksum

        missing = _manifest_gaps(root_path, manifest_path, warnings)

        report = SkillValidationReport(
            root=str(root_path),
            total=len(directories),
            valid=tuple(sorted(records, key=lambda r: r.skill_id)),
            missing=missing,
            invalid=tuple(sorted(invalid)),
            library_checksum=library_checksum(checksums),
            file_count=count_library_files(root_path),
            warnings=tuple(warnings),
        )
        return cls(root=root_path, report=report)

    def with_disabled(self, disabled: str | Iterable[str]) -> "SkillRegistry":
        """Return a copy whose records carry ``enabled`` from ``disabled``.

        Accepts the raw comma-separated setting value or any iterable of ids.
        """
        if isinstance(disabled, str):
            wanted = {part.strip() for part in disabled.split(",") if part.strip()}
        else:
            wanted = {str(part).strip() for part in disabled if str(part).strip()}

        updated = tuple(
            _enabled(record, False) if record.skill_id in wanted else record
            for record in self.report.valid
        )
        return SkillRegistry(
            root=self.root,
            report=SkillValidationReport(
                root=self.report.root,
                total=self.report.total,
                valid=updated,
                missing=self.report.missing,
                invalid=self.report.invalid,
                library_checksum=self.report.library_checksum,
                file_count=self.report.file_count,
                warnings=self.report.warnings,
            ),
            manifest_missing=self.manifest_missing,
        )

    # -- queries ---------------------------------------------------------

    @property
    def records(self) -> tuple[MarketingSkillRecord, ...]:
        return self.report.valid

    @property
    def skill_ids(self) -> tuple[str, ...]:
        return self.report.skill_ids

    def __len__(self) -> int:
        return len(self.report.valid)

    def __contains__(self, skill_id: object) -> bool:
        return skill_id in self.report.skill_ids

    def get(self, skill_id: str) -> MarketingSkillRecord | None:
        for record in self.report.valid:
            if record.skill_id == skill_id:
                return record
        return None

    def enabled_records(self) -> tuple[MarketingSkillRecord, ...]:
        """Valid **and** enabled records -- the only set the router may inject."""
        return tuple(r for r in self.report.valid if r.enabled and r.is_valid)

    def category_counts(self) -> dict[str, int]:
        """Measured records per category. Recomputed, never remembered."""
        counts: dict[str, int] = {}
        for record in self.report.valid:
            counts[record.category] = counts.get(record.category, 0) + 1
        return counts

    def summary(self) -> str:
        return self.report.summary()

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready view used by ``/api/skills``.

        Deliberately carries no absolute filesystem path. ``report.root`` is
        absolute because the loader needs it, but
        ``app.contracts.events.sanitize_user_text`` redacts absolute Windows and
        Unix paths, so shipping one would render as ``[REDACTED]`` on the wire --
        a field that looks populated and is useless. The library location is
        reported as the relative label instead.
        """
        return {
            "library_root": LIBRARY_ROOT_LABEL,
            "hash_scheme": HASH_SCHEME,
            "summary": self.report.summary(),
            "total": self.report.total,
            "valid": self.report.valid_count,
            "missing": self.report.missing_count,
            "invalid": [
                {"skill_id": skill_id, "reason_code": reason}
                for skill_id, reason in self.report.invalid
            ],
            "warnings": list(self.report.warnings),
            "library_checksum": self.report.library_checksum,
            "file_count": self.report.file_count,
            "skills": [record.to_dict() for record in self.report.valid],
        }


def _enabled(record: MarketingSkillRecord, value: bool) -> MarketingSkillRecord:
    from dataclasses import replace

    return replace(record, enabled=value)


def load_registry(
    *,
    root: str | Path | None = None,
    disabled: str | Iterable[str] | None = None,
    manifest_path: str | Path | None = None,
) -> SkillRegistry:
    """Load the library. Raises :class:`SkillRegistryUnavailable` if no root.

    ``disabled=None`` means "read the setting now" -- it is resolved per call,
    never cached at import, so a Settings toggle is visible on the next turn.
    Pass an explicit value (including ``""``) to stay off the database.

    ``manifest_path=None`` means "use the repo manifest if it is there", which is
    what makes ``MISSING`` a measured number rather than a constant: it is the
    set of skills the manifest promises minus the ones actually on disk. Pass a
    path to compare against something else, or ``False`` to skip the comparison.
    """
    registry = SkillRegistry.load(root=root, manifest_path=manifest_path)
    if disabled is None:
        return registry.with_disabled(read_disabled_setting())
    return registry.with_disabled(disabled)


def read_disabled_setting() -> set[str]:
    """Read ``ConfigService.get_setting("skills_disabled", "")`` as a set of ids.

    Imported lazily and defensively: the registry is usable in a test process
    with no database, and a missing database must not look like "everything
    disabled" or, worse, "everything enabled by accident".
    """
    try:
        from app.services.config_service import ConfigService

        raw = ConfigService.get_setting(SETTINGS_DISABLED_KEY, "")
    except Exception:
        return set()
    return {part.strip() for part in str(raw or "").split(",") if part.strip()}


def verify_checksums(registry: SkillRegistry) -> list[str]:
    """Recompute every checksum from disk; return the ids that disagree.

    This is a *self-consistency* check: it re-hashes each ``SKILL.md`` and compares
    against the checksum the loader already recorded, so it reports a skill whose
    file changed underneath a live registry. It needs no manifest and works on a
    cold clone.

    It deliberately does **not** compare against the pin. Detecting drift versus
    the recorded commit is :func:`app.services.skills.manifest._verify_document`'s
    job, which needs ``manifest=``/``lock=``; that is why the ``--verify`` CLI
    passes both. Calling this alone can never fail on a freshly loaded tree,
    because records and files agree by construction.
    """
    drifted: list[str] = []
    for record in registry.report.valid:
        try:
            _, normalised = _read_skill_md(registry.root / record.skill_id)
        except (SkillParseError, UnicodeDecodeError):
            drifted.append(record.skill_id)
            continue
        if sha256_hex(normalised) != record.checksum:
            drifted.append(record.skill_id)
    return drifted
