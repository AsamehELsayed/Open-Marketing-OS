"""Generate and verify the skills manifest and lockfile (DEV-008-SKILLS-OPS).

Both files are **generated from the real tree**. Nothing here is hand-typed, and
the generator is the only supported way to change either file -- which is what
makes them trustworthy rather than decorative.

The old ``skills-lock.json`` was the opposite: its ``computedHash`` values
verified **0 of 50** against the vendored files under both candidate hash
schemes, it recorded no upstream tag, no commit, no licence and no library
checksum, and its ``skillPath`` values were upstream-relative and therefore
unresolvable. A lockfile that verifies nothing is worse than no lockfile, because
it looks like a pin. Schema ``omos.skills-lock/2`` fixes all of that.

Hash scheme (``sha256-utf8-lf``, plan §1.5.2)
---------------------------------------------
::

    normalize(b) := b.decode("utf-8")
                        .replace("\\r\\n", "\\n").replace("\\r", "\\n")
                        .encode("utf-8")

    skill.checksum   := sha256_hex( normalize(read(<root>/<id>/SKILL.md)) )
    library_checksum := sha256_hex( "".join(f"{id}:{checksum}\\n"
                                             for id in sorted(ids)) )
    file_count       := regular files under <root>, recursive

Three properties matter and are all intentional:

* ``sorted(...)`` makes the library checksum order-independent, so reordering the
  manifest is not reported as drift. A gate that fires on a reorder teaches
  people to ignore it.
* It depends on nothing but the file tree, so a cold clone with no manifest can
  recompute it and confirm the pin.
* LF normalisation is what makes the CRLF working tree hash equal to the LF git
  blob hash, so a recorded digest is comparable to ``git hash-object``.

CLI::

    python -m app.services.skills.manifest --write       # regenerate both files
    python -m app.services.skills.manifest --verify      # recompute and compare
    python -m app.services.skills.manifest --summary     # print the run summary
    python -m app.services.skills.manifest --check-license

``--verify`` exits non-zero on drift, on a missing/invalid skill, or on an
incomplete role table. It never repairs anything silently.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from .records import (
    HASH_SCHEME,
    SKILL_SOURCE,
    UPSTREAM_REPOSITORY,
    MarketingSkillRecord,
    library_checksum,
)
from .registry import (
    MANIFEST_RELPATH,
    SkillRegistry,
    SkillRegistryUnavailable,
    load_registry,
    read_manifest,
    verify_checksums,
)
from .roles import ROLE_TABLE_VERSION, SKILL_ROLE_MAP

MANIFEST_SCHEMA = "omos.marketing-skills-manifest/1"
LOCK_SCHEMA = "omos.skills-lock/2"

#: Frozen pin metadata. W0 §1 verified each of these against the upstream
#: repository at the pinned SHA (tag object, recursive git tree, raw LICENSE).
UPSTREAM: dict[str, Any] = {
    "repository": UPSTREAM_REPOSITORY,
    "version": "v2.11.1",
    "commit": "5b2c0007766c6a1cf1d53fd8fc73e979e0821022",
    "license": "MIT",
    "license_file": ".agents/LICENSE",
}

#: How each record field got its value. Repeated in the manifest so a reader
#: never has to guess whether ``category`` came from upstream.
#: W0 §3.1 measured ``triggers``/``category``/``related_skills``/``license`` as
#: absent from all 50 frontmatters, so none of these is presented as parsed
#: upstream metadata.
FIELD_PROVENANCE: dict[str, str] = {
    "skill_id": "derived",
    "name": "parsed",
    "description": "parsed",
    "version": "parsed",
    "source": "assigned",
    "path": "derived",
    "triggers": "derived",
    "related_skills": "derived",
    "unresolved_related": "derived",
    "category": "assigned",
    "checksum": "derived",
    "eval_count": "derived",
    "enabled": "assigned",
    "validation_status": "derived",
    "validation_reason": "derived",
    "description_truncated": "derived",
}

GENERATED_BY = "python -m app.services.skills.manifest --write"

LOCK_RELPATH = Path("skills-lock.json")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def build_manifest(registry: SkillRegistry) -> dict[str, Any]:
    """Build the manifest document from a loaded registry.

    Pure function of the file tree plus the frozen pin: the same tree always
    produces the same bytes, so a regenerate that changes nothing is a no-op.
    """
    records = [record.to_dict() for record in registry.report.valid]
    for record in records:
        record["provenance"] = dict(FIELD_PROVENANCE)

    return {
        "schema": MANIFEST_SCHEMA,
        "upstream": dict(UPSTREAM),
        "hash_scheme": HASH_SCHEME,
        "role_table_version": ROLE_TABLE_VERSION,
        "generated_by": GENERATED_BY,
        "skill_count": len(records),
        "file_count": registry.report.file_count,
        "library_checksum": registry.report.library_checksum,
        "library_root": f".agents/skills",
        "missing_count": registry.report.missing_count,
        "invalid_count": registry.report.invalid_count,
        "warnings": list(registry.report.warnings),
        "field_provenance": dict(FIELD_PROVENANCE),
        "skills": records,
    }


def build_lock(registry: SkillRegistry) -> dict[str, Any]:
    """Build the ``omos.skills-lock/2`` document.

    Keeps the upstream-relative path as ``upstreamPath`` so the pin can still be
    diffed against the release tree, and adds a ``localPath`` that actually
    resolves on disk. A lock entry that cannot be resolved cannot verify anything.
    """
    skills: dict[str, Any] = {}
    for record in sorted(registry.report.valid, key=lambda r: r.skill_id):
        skill_id = record.skill_id
        skills[skill_id] = {
            "source": SKILL_SOURCE,
            "sourceType": "github",
            "upstreamPath": f"skills/{skill_id}/SKILL.md",
            "localPath": f".agents/skills/{skill_id}/SKILL.md",
            "version": record.version,
            "computedHash": record.checksum,
            "hashScheme": HASH_SCHEME,
        }

    return {
        "schema": LOCK_SCHEMA,
        "upstream": dict(UPSTREAM),
        "hash_scheme": HASH_SCHEME,
        "library_checksum": registry.report.library_checksum,
        "skill_count": len(skills),
        "skills": skills,
    }


def write_json(path: Path, document: dict[str, Any]) -> bool:
    """Write ``document`` as pretty JSON with a trailing newline.

    Returns ``True`` when the file's bytes actually changed, so ``--write`` can
    say whether a regenerate was a no-op instead of always claiming success.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    encoded = payload.encode("utf-8")
    if path.is_file() and path.read_bytes() == encoded:
        return False
    path.write_bytes(encoded)
    return True


# --------------------------------------------------------------------- #
# verification                                                            #
# --------------------------------------------------------------------- #


class DriftError(Exception):
    """A recorded value disagrees with the file tree. Always named, never vague."""


def verify_registry(
    registry: SkillRegistry,
    *,
    manifest: dict[str, Any] | None = None,
    lock: dict[str, Any] | None = None,
) -> list[str]:
    """Recompute everything from disk and return the list of problems.

    An empty list means the pin verifies. Each entry is a human-readable defect
    so a CI log says what is wrong, not just that something is.
    """
    problems: list[str] = []
    report = registry.report

    if report.missing_count:
        problems.append(
            f"MANIFEST_DRIFT: {report.missing_count} manifest skill(s) absent on "
            f"disk: {', '.join(report.missing)}"
        )
    for skill_id, reason in report.invalid:
        problems.append(f"INVALID: {skill_id} -> {reason}")
    if report.invalid_count:
        problems.append(f"MANIFEST_DRIFT: {report.invalid_count} invalid skill(s)")

    drifted = verify_checksums(registry)
    if drifted:
        problems.append(
            f"MANIFEST_DRIFT: checksum mismatch for {len(drifted)} skill(s): "
            f"{', '.join(drifted)}"
        )

    # Role table completeness: the assertion that stops the table falling behind
    # a re-pin. Compared as sets, so no skill count is ever written down.
    unmapped = sorted(set(report.skill_ids) - set(SKILL_ROLE_MAP))
    if unmapped:
        problems.append(
            f"ROLE_UNMAPPED: {len(unmapped)} registered skill(s) have no role: "
            f"{', '.join(unmapped)}"
        )
    stale = sorted(set(SKILL_ROLE_MAP) - set(report.skill_ids))
    if stale:
        problems.append(
            f"ROLE_UNMAPPED: {len(stale)} role-table entr(ies) match no skill: "
            f"{', '.join(stale)}"
        )

    if manifest is None:
        problems.append("MANIFEST_DRIFT: marketing-skills manifest is missing or unreadable")
    else:
        problems.extend(_verify_document("manifest", manifest, registry))
    if lock is None:
        problems.append("MANIFEST_DRIFT: skills lock is missing or unreadable")
    else:
        problems.extend(_verify_document("lock", lock, registry))
    return problems


def _verify_document(
    label: str, document: dict[str, Any], registry: SkillRegistry
) -> list[str]:
    problems: list[str] = []
    by_id = {r.skill_id: r for r in registry.report.valid}

    if document.get("library_checksum") != registry.report.library_checksum:
        problems.append(
            f"MANIFEST_DRIFT: {label} library_checksum "
            f"{document.get('library_checksum')!r} != recomputed "
            f"{registry.report.library_checksum!r}"
        )

    if label == "manifest":
        entries = document.get("skills") or []
        recorded = {
            str(e.get("skill_id")): e for e in entries if isinstance(e, dict)
        }
        if document.get("skill_count") != len(recorded):
            problems.append(
                f"MANIFEST_DRIFT: manifest skill_count {document.get('skill_count')!r} "
                f"!= {len(recorded)} entries"
            )
        if document.get("file_count") != registry.report.file_count:
            problems.append(
                f"MANIFEST_DRIFT: manifest file_count {document.get('file_count')!r} "
                f"!= recomputed {registry.report.file_count}"
            )
    else:
        recorded = document.get("skills") or {}
        recorded = {k: v for k, v in recorded.items() if isinstance(v, dict)}
        if document.get("skill_count") != len(recorded):
            problems.append(
                f"MANIFEST_DRIFT: lock skill_count {document.get('skill_count')!r} "
                f"!= {len(recorded)} entries"
            )

    for skill_id in sorted(set(by_id) - set(recorded)):
        problems.append(f"MANIFEST_DRIFT: {label} has no entry for {skill_id}")
    for skill_id in sorted(set(recorded) - set(by_id)):
        problems.append(f"MANIFEST_DRIFT: {label} entry {skill_id} is not on disk")

    for skill_id in sorted(set(by_id) & set(recorded)):
        entry = recorded[skill_id]
        expected = by_id[skill_id]
        if entry.get("checksum") != expected.checksum and entry.get(
            "computedHash"
        ) != expected.checksum:
            problems.append(
                f"MANIFEST_DRIFT: {label} {skill_id} checksum "
                f"{entry.get('checksum') or entry.get('computedHash')!r} != "
                f"recomputed {expected.checksum!r}"
            )
        if label == "manifest" and entry.get("version") != expected.version:
            problems.append(
                f"MANIFEST_DRIFT: manifest {skill_id} version "
                f"{entry.get('version')!r} != parsed {expected.version!r}"
            )
        if label == "lock":
            local = entry.get("localPath")
            if not local or not (_repo_root() / str(local)).is_file():
                problems.append(
                    f"MANIFEST_DRIFT: lock {skill_id} localPath {local!r} does not resolve"
                )
    return problems


def read_lock(path: str | Path | None = None) -> dict[str, Any] | None:
    """Read the lockfile, or ``None`` when absent/unreadable."""
    candidate = Path(path) if path else _repo_root() / LOCK_RELPATH
    if not candidate.is_file():
        return None
    try:
        document = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def check_license(repo_root: Path | None = None) -> list[str]:
    """Verify the upstream grant is recorded in-repo. Returns problems.

    A vendored MIT library is only lawfully redistributable if the copyright and
    permission notice travel with the copy. ``NOTICE`` is owned by another packet
    in this run, so this function reports rather than edits.
    """
    root = repo_root or _repo_root()
    problems: list[str] = []

    license_file = root / str(UPSTREAM["license_file"])
    if not license_file.is_file():
        problems.append(f"LICENSE: {UPSTREAM['license_file']} is absent")
    else:
        text = license_file.read_text(encoding="utf-8", errors="replace")
        if "MIT" not in text or "Permission is hereby granted" not in text:
            problems.append(
                f"LICENSE: {UPSTREAM['license_file']} does not read as the MIT text"
            )

    notice = root / "NOTICE"
    if not notice.is_file():
        problems.append("NOTICE: file is absent")
    else:
        text = notice.read_text(encoding="utf-8", errors="replace")
        for needle, label in (
            (SKILL_SOURCE, "upstream repository"),
            (str(UPSTREAM["version"]), "upstream tag"),
            (str(UPSTREAM["commit"]), "upstream commit"),
            (UPSTREAM["license"], "upstream licence"),
        ):
            if needle not in text:
                problems.append(f"NOTICE: does not name the {label} ({needle})")
    return problems


# --------------------------------------------------------------------- #
# CLI                                                                     #
# --------------------------------------------------------------------- #


def _with_reason(problem: str) -> str:
    """Prefix a problem with its reason code, without doubling it.

    The gate must never emit a bare "something is wrong": every line names the
    defect so a CI log is actionable. Double-prefixing would read as two
    separate problems and train readers to skim past the real one.
    """
    for prefix in ("MANIFEST_DRIFT", "INVALID", "MISSING", "ROLE_UNMAPPED",
                   "CATEGORY_UNMAPPED", "SKILL_LIBRARY_UNAVAILABLE", "LICENSE"):
        if problem.startswith(prefix):
            return problem
    return f"MANIFEST_DRIFT: {problem}"


def _summarise(registry: SkillRegistry) -> None:
    report = registry.report
    print(f"root:            {report.root}")
    print(f"library_checksum: {report.library_checksum}")
    print(report.summary())
    for skill_id, reason in report.invalid:
        print(f"  INVALID {skill_id} -> {reason}")
    for skill_id in report.missing:
        print(f"  MISSING {skill_id}")
    for warning in report.warnings:
        print(f"  WARNING {warning}")
    counts = registry.category_counts()
    print("categories:      " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"eval_prompts:    {sum(r.eval_count for r in registry.report.valid)}")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.services.skills.manifest",
        description="Generate and verify the marketing-skills pin.",
    )
    parser.add_argument("--write", action="store_true", help="regenerate both files")
    parser.add_argument(
        "--verify", action="store_true", help="recompute and compare against disk"
    )
    parser.add_argument(
        "--summary", action="store_true", help="print the measured run summary"
    )
    parser.add_argument(
        "--check-license", action="store_true", help="verify the MIT grant is recorded"
    )
    parser.add_argument("--root", default=None, help="override the library root")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not any((args.write, args.verify, args.summary, args.check_license)):
        args.summary = True

    try:
        registry = load_registry(root=args.root, disabled="")
    except SkillRegistryUnavailable as exc:
        print(f"SKILL_LIBRARY_UNAVAILABLE: {exc}", file=sys.stderr)
        return 2

    _summarise(registry)
    exit_code = 0

    if args.write:
        manifest = build_manifest(registry)
        lock = build_lock(registry)
        manifest_path = _repo_root() / MANIFEST_RELPATH
        lock_path = _repo_root() / LOCK_RELPATH
        changed_manifest = write_json(manifest_path, manifest)
        changed_lock = write_json(lock_path, lock)
        print(
            f"wrote {manifest_path.name}: "
            + ("changed" if changed_manifest else "unchanged")
        )
        print(f"wrote {lock_path.name}: " + ("changed" if changed_lock else "unchanged"))

    if args.verify:
        problems = verify_registry(
            registry,
            manifest=read_manifest(),
            lock=read_lock(),
        )
        if problems:
            exit_code = 1
            for problem in problems:
                print(_with_reason(problem), file=sys.stderr)
        else:
            print("verify: OK (checksums, counts, role table, manifest, lock)")

    if args.check_license:
        problems = check_license()
        if problems:
            exit_code = 1
            for problem in problems:
                print(f"LICENSE: {problem}", file=sys.stderr)
        else:
            print("check-license: OK")

    return exit_code


if __name__ == "__main__":  # pragma: no cover - exercised via the CLI
    raise SystemExit(main())
