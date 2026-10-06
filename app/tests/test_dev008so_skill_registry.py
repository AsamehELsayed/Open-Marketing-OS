"""DEV-008-SKILLS-OPS / W1 -- the registry, proven against the real library.

This is the acceptance matrix for plan §7 A1/A2/A4/A5/A17. Four properties, each
of which can be faked by a careless test and so is checked against the file tree
rather than against a remembered number:

1. **One generated test per registered skill, parametrised over the real
   registry.** Collection reads the library, so the suite tracks a re-pin: a new
   skill directory produces new tests automatically, and no literal skill count
   appears anywhere. A fixed count of 50 would silently stop covering the 51st.

2. **``MISSING: 0`` and ``INVALID: 0`` are measured, not constants.**
   ``report.missing`` is the set of ids the manifest promises minus the ids on
   disk; ``report.invalid`` is collected during the load. Both are printed by the
   loader and asserted against that run's own output.

3. **Every recorded checksum is recomputed from disk.** ``library_checksum`` and
   each per-skill digest are rebuilt here from the file tree, so the manifest
   cannot rot unnoticed and the CI gate is proven to bite rather than merely
   existing.

4. **Fail-closed behaviour is exercised, not described.** A temp copy with one
   corrupted byte, one renamed skill, one stripped version and one removed
   ``SKILL.md`` each produce a named reason code -- the tests that would catch a
   silent skip are the ones that run against a deliberately broken tree.

Everything here is derived from the real tree. Nothing asserts a skill count, a
trigger count or a category histogram as a constant; those are printed and
checked for self-consistency instead.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
from pathlib import Path

import pytest

from app.services.skills import (
    CATEGORIES,
    CATEGORY_BY_ROLE,
    EMPLOYEE_ROLES,
    REASON_CODES,
    SKILL_ROLE_MAP,
    SKILL_SOURCE,
    VALIDATION_STATUSES,
    MarketingSkillRecord,
    SkillRegistry,
    SkillRegistryUnavailable,
    SkillValidationReport,
    library_checksum,
    load_registry,
    normalize_bytes,
    resolve_library_root,
    role_for,
    sha256_hex,
    verify_checksums,
)
from app.services.skills import manifest as skills_manifest
from app.services.skills import registry as skills_registry
from app.services.skills import roles as roles_module
from app.services.skills.roles import RoleTableError, category_for

REPO_ROOT = Path(__file__).resolve().parents[2]
LIBRARY_ROOT = REPO_ROOT / ".agents" / "skills"
MANIFEST_PATH = REPO_ROOT / "docs" / "marketing-skills-manifest.json"
LOCK_PATH = REPO_ROOT / "skills-lock.json"

pytestmark = pytest.mark.skipif(
    not LIBRARY_ROOT.is_dir(),
    reason="the vendored marketing-skills library is not present at .agents/skills",
)

#: One registry, loaded once. ``disabled=""`` keeps this off the database so the
#: suite has no fixture and no ordering dependency; the enabled/disabled logic is
#: tested directly further down.
REGISTRY = load_registry(disabled="")

SKILL_IDS: list[str] = list(REGISTRY.skill_ids)
RECORDS: dict[str, MarketingSkillRecord] = {r.skill_id: r for r in REGISTRY.records}


def _ids_in(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.is_dir())


# --------------------------------------------------------------------- #
# the run summary, printed so it can be quoted in a handoff                #
# --------------------------------------------------------------------- #


def test_run_summary_is_printed():
    summary = REGISTRY.summary()
    print(f"\nloader run: {summary}")
    assert REGISTRY.report.total == len(_ids_in(LIBRARY_ROOT)), (
        "total must equal the number of directories actually on disk"
    )


def test_measured_counts_are_zero_and_zero():
    """MISSING/INVALID as run-derived numbers, printed and asserted.

    Not ``assert 0 == 0`` dressed up: both are recomputed by this load, and the
    print statement makes the measured value visible in CI output.
    """
    report = REGISTRY.report
    print(
        f"\nmeasured: TOTAL={report.total} VALID={report.valid_count} "
        f"MISSING={report.missing_count} INVALID={report.invalid_count}"
    )
    assert report.missing_count == 0, f"MISSING: {report.missing}"
    assert report.invalid_count == 0, f"INVALID: {report.invalid}"
    assert report.valid_count == report.total
    assert report.library_checksum, "no library checksum was computed"


def test_no_warnings_on_a_healthy_load():
    assert REGISTRY.report.warnings == (), list(REGISTRY.report.warnings)


def test_eval_prompts_are_mined_from_upstream_and_sum_is_measured():
    """Triggers are upstream's, and the total is printed rather than asserted."""
    total = sum(r.eval_count for r in REGISTRY.records)
    trigger_total = sum(len(r.triggers) for r in REGISTRY.records)
    print(f"\neval entries: {total}  distinct triggers kept: {trigger_total}")
    assert total > 0
    for record in REGISTRY.records:
        assert record.eval_count > 0, f"{record.skill_id}: no eval entries"
        assert record.triggers, f"{record.skill_id}: no triggers"
        document = json.loads(
            (LIBRARY_ROOT / record.skill_id / "evals" / "evals.json").read_text(
                encoding="utf-8"
            )
        )
        assert document["skill_name"] == record.skill_id, record.skill_id
        for trigger in record.triggers:
            assert any(
                entry["prompt"].strip().startswith(trigger)
                for entry in document["evals"]
                if isinstance(entry.get("prompt"), str)
            ), f"{record.skill_id}: trigger is not upstream text"


# --------------------------------------------------------------------- #
# 1. generated per-skill coverage (plan §6.1)                             #
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("skill_id", SKILL_IDS)
def test_register_record_is_complete(skill_id):
    """A2: one generated record per skill, every §1.1 field present and sane."""
    record = RECORDS[skill_id]
    assert record.skill_id == skill_id
    assert record.name == skill_id == (LIBRARY_ROOT / skill_id).name
    assert record.description and record.description == record.description.strip()
    assert re.match(r"^\d+\.\d+\.\d+$", record.version), record.version
    assert record.source == SKILL_SOURCE
    assert record.path == f".agents/skills/{skill_id}"
    assert record.category in CATEGORIES
    assert record.checksum and re.match(r"^[0-9a-f]{64}$", record.checksum)
    assert record.validation_status == "valid"
    assert record.validation_reason == ""
    assert record.enabled is True
    assert record.eval_count > 0
    assert record.triggers


@pytest.mark.parametrize("skill_id", SKILL_IDS)
def test_register_record_checksum_is_recomputed_from_disk(skill_id):
    record = RECORDS[skill_id]
    raw = (LIBRARY_ROOT / skill_id / "SKILL.md").read_bytes()
    assert record.checksum == sha256_hex(normalize_bytes(raw)), skill_id


@pytest.mark.parametrize("skill_id", SKILL_IDS)
def test_register_record_path_is_relative_and_posix(skill_id):
    """An absolute path would be redacted to ``[REDACTED]`` on the wire."""
    record = RECORDS[skill_id]
    assert not re.match(r"^[A-Za-z]:[\\/]", record.path)
    assert not record.path.startswith("/")
    assert "\\" not in record.path
    assert record.path == f".agents/skills/{skill_id}"


@pytest.mark.parametrize("skill_id", SKILL_IDS)
def test_role_assignment_is_total_and_derives_the_category(skill_id):
    """A5: role -> category is one table, so the two cannot drift."""
    role = role_for(skill_id)
    assert role in EMPLOYEE_ROLES, f"{skill_id}: role {role!r}"
    assert role == SKILL_ROLE_MAP[skill_id]
    assert CATEGORY_BY_ROLE[role] == category_for(skill_id)
    assert RECORDS[skill_id].category == CATEGORY_BY_ROLE[role], skill_id


@pytest.mark.parametrize("skill_id", SKILL_IDS)
def test_related_skills_resolve_and_exclude_self(skill_id):
    record = RECORDS[skill_id]
    assert skill_id not in record.related_skills
    assert skill_id not in record.unresolved_related
    assert list(record.related_skills) == sorted(record.related_skills)
    assert list(record.unresolved_related) == sorted(record.unresolved_related)
    for related in record.related_skills:
        assert related in RECORDS, f"{skill_id} -> {related} does not resolve"
        assert related != skill_id
    for dangling in record.unresolved_related:
        assert dangling not in RECORDS, f"{skill_id} -> {dangling} unexpectedly resolves"


def test_cross_references_exist_so_the_field_is_not_vacuous():
    with_related = [r for r in REGISTRY.records if r.related_skills]
    assert with_related, "no skill has a resolved cross-reference"
    with_dangling = [r for r in REGISTRY.records if r.unresolved_related]
    print(
        f"\nresolved cross-refs on {len(with_related)} skills; "
        f"dangling refs on {len(with_dangling)}: "
        f"{ {r.skill_id: r.unresolved_related for r in with_dangling} }"
    )


# --------------------------------------------------------------------- #
# 2. role table completeness and the frozen contract                       #
# --------------------------------------------------------------------- #


def test_role_map_covers_exactly_the_registered_skills():
    """The check that stops the table falling behind a re-pin.

    Compared as sets, so no count is ever written down. W0 §1 warns that nothing
    in code may branch on a skill count; a set comparison satisfies that.
    """
    assert set(SKILL_ROLE_MAP) == set(SKILL_IDS), {
        "unmapped": sorted(set(SKILL_IDS) - set(SKILL_ROLE_MAP)),
        "stale": sorted(set(SKILL_ROLE_MAP) - set(SKILL_IDS)),
    }


def test_every_employee_role_maps_to_a_category():
    assert set(CATEGORY_BY_ROLE) == set(EMPLOYEE_ROLES)
    assert set(CATEGORY_BY_ROLE.values()) <= set(CATEGORIES)
    for role in EMPLOYEE_ROLES:
        assert CATEGORY_BY_ROLE[role] in CATEGORIES, role


def test_role_counts_are_recomputed_not_stored():
    counts = roles_module.role_counts()
    assert set(counts) == set(EMPLOYEE_ROLES)
    assert sum(counts.values()) == len(SKILL_ROLE_MAP)
    assert counts == {
        role: sum(1 for r in SKILL_ROLE_MAP.values() if r == role)
        for role in EMPLOYEE_ROLES
    }


def test_category_counts_cover_every_skill_exactly_once():
    counts = REGISTRY.category_counts()
    assert set(counts) <= set(CATEGORIES)
    assert sum(counts.values()) == len(SKILL_IDS)
    print(f"\ncategory histogram: {dict(sorted(counts.items()))}")


def test_unmapped_skill_is_a_named_failure_not_a_default():
    with pytest.raises(RoleTableError) as excinfo:
        role_for("no-such-skill")
    assert excinfo.value.reason_code == "ROLE_UNMAPPED"
    assert excinfo.value.reason_code in REASON_CODES


def test_unmapped_role_category_is_a_named_failure(monkeypatch):
    monkeypatch.setitem(roles_module.CATEGORY_BY_ROLE, "seo", CATEGORY_BY_ROLE["seo"])
    monkeypatch.delitem(roles_module.CATEGORY_BY_ROLE, "seo")
    with pytest.raises(RoleTableError) as excinfo:
        category_for("seo-audit")
    assert excinfo.value.reason_code == "CATEGORY_UNMAPPED"


def test_role_table_deviation_is_documented():
    """The two contract deviations must be recorded where a reviewer will see them.

    plan.md §1.4.4 claims a 50-entry role table that is arithmetically 49, and
    omits ``revops``. This asserts the deviation is *declared* -- in the module
    docstring, in the doc, and in the run handoff -- rather than discovered later.
    """
    source = (REPO_ROOT / "app" / "services" / "skills" / "roles.py").read_text(
        encoding="utf-8"
    )
    assert "CONTRACT DEVIATION" in source
    assert "revops" in source
    doc = (REPO_ROOT / "docs" / "marketing-skills.md").read_text(encoding="utf-8")
    assert "CONTRACT DEVIATION" in doc
    handoff = (
        REPO_ROOT
        / "development"
        / "runs"
        / "DEV-008-SKILLS-OPS"
        / "workers"
        / "w1.md"
    )
    if handoff.is_file():
        assert "CONTRACT DEVIATION" in handoff.read_text(encoding="utf-8")
    # The repair is exactly two entries beyond the plan's literal table.
    assert SKILL_ROLE_MAP["revops"] == "strategy"
    assert SKILL_ROLE_MAP["ad-creative"] == "content"


# --------------------------------------------------------------------- #
# 3. the hash scheme, recomputed blind                                     #
# --------------------------------------------------------------------- #


def test_library_checksum_is_recomputable_from_the_file_tree_alone():
    """No manifest access: recomputed from the tree, as a cold clone would."""
    checksums = {
        path.name: sha256_hex(normalize_bytes((path / "SKILL.md").read_bytes()))
        for path in sorted(LIBRARY_ROOT.iterdir())
        if path.is_dir()
    }
    recomputed = library_checksum(checksums)
    assert recomputed == REGISTRY.report.library_checksum
    assert re.match(r"^[0-9a-f]{64}$", recomputed)


def test_library_checksum_is_order_independent():
    checksums = {r.skill_id: r.checksum for r in REGISTRY.records}
    shuffled = dict(reversed(list(checksums.items())))
    assert library_checksum(shuffled) == library_checksum(checksums), (
        "reordering must not read as drift"
    )


def test_library_checksum_detects_a_single_byte_edit(tmp_path):
    copy = _copy_library(tmp_path)
    before = SkillRegistry.load(root=copy, manifest_path=False).report
    target = copy / SKILL_IDS[0] / "SKILL.md"
    target.write_bytes(target.read_bytes() + b"\n")
    after = SkillRegistry.load(root=copy, manifest_path=False).report
    assert after.library_checksum != before.library_checksum


def test_verify_checksums_reports_drift(tmp_path):
    copy = _copy_library(tmp_path)
    registry = SkillRegistry.load(root=copy, manifest_path=False)
    assert verify_checksums(registry) == []
    target = copy / SKILL_IDS[0] / "SKILL.md"
    target.write_bytes(target.read_bytes() + b"\n")
    assert verify_checksums(registry) == [SKILL_IDS[0]]


def test_crlf_working_tree_matches_the_lf_hash():
    """Why the scheme normalises: a CRLF tree and an LF clone agree."""
    skill_id = SKILL_IDS[0]
    raw = (LIBRARY_ROOT / skill_id / "SKILL.md").read_bytes()
    lf = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    crlf = lf.replace(b"\n", b"\r\n")
    assert sha256_hex(normalize_bytes(crlf)) == sha256_hex(normalize_bytes(lf))
    assert RECORDS[skill_id].checksum == sha256_hex(normalize_bytes(crlf))


def test_file_count_is_measured():
    actual = sum(1 for p in LIBRARY_ROOT.rglob("*") if p.is_file())
    assert REGISTRY.report.file_count == actual
    print(f"\nfile_count measured: {actual}")


# --------------------------------------------------------------------- #
# 4. the generated files                                                   #
# --------------------------------------------------------------------- #


@pytest.mark.skipif(
    not MANIFEST_PATH.is_file(),
    reason="docs/marketing-skills-manifest.json has not been generated",
)
def test_manifest_round_trips_every_checksum_from_disk():
    document = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert document["schema"] == "omos.marketing-skills-manifest/1"
    assert document["hash_scheme"] == "sha256-utf8-lf"
    assert document["library_checksum"] == REGISTRY.report.library_checksum
    assert document["file_count"] == REGISTRY.report.file_count
    assert document["upstream"]["version"] == skills_manifest.UPSTREAM["version"]
    assert document["upstream"]["license"] == "MIT"

    recorded = {e["skill_id"]: e for e in document["skills"]}
    assert set(recorded) == set(SKILL_IDS)
    assert document["skill_count"] == len(recorded)
    for skill_id, record in RECORDS.items():
        entry = recorded[skill_id]
        raw = (LIBRARY_ROOT / skill_id / "SKILL.md").read_bytes()
        assert entry["checksum"] == sha256_hex(normalize_bytes(raw)), skill_id
        assert entry["version"] == record.version, skill_id
        assert entry["category"] == record.category, skill_id
        assert entry["name"] == record.name, skill_id
        assert entry["path"] == record.path, skill_id


@pytest.mark.skipif(
    not MANIFEST_PATH.is_file(), reason="the manifest has not been generated"
)
def test_manifest_labels_provenance_honestly():
    """``category`` and ``triggers`` must be labelled assigned/derived, not parsed."""
    document = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    provenance = document["field_provenance"]
    assert provenance["category"] == "assigned", (
        "upstream ships no category key; presenting it as parsed would be a lie"
    )
    assert provenance["triggers"] == "derived"
    assert provenance["name"] == "parsed"
    assert provenance["version"] == "parsed"
    assert provenance["checksum"] == "derived"
    for entry in document["skills"]:
        assert entry["provenance"]["category"] == "assigned"
        assert entry["provenance"]["triggers"] == "derived"


@pytest.mark.skipif(not LOCK_PATH.is_file(), reason="skills-lock.json is absent")
def test_lockfile_is_schema_2_and_every_hash_verifies():
    document = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    assert document["schema"] == "omos.skills-lock/2", "the 1 -> 2 bump is load-bearing"
    assert document["hash_scheme"] == "sha256-utf8-lf"
    assert document["library_checksum"] == REGISTRY.report.library_checksum
    assert document["skill_count"] == len(document["skills"])
    upstream = document["upstream"]
    assert upstream["version"] == skills_manifest.UPSTREAM["version"]
    assert upstream["commit"] == skills_manifest.UPSTREAM["commit"]
    assert upstream["license"] == "MIT"

    assert set(document["skills"]) == set(SKILL_IDS)
    for skill_id, entry in document["skills"].items():
        raw = (LIBRARY_ROOT / skill_id / "SKILL.md").read_bytes()
        assert entry["computedHash"] == sha256_hex(normalize_bytes(raw)), skill_id
        assert entry["hashScheme"] == "sha256-utf8-lf"
        assert entry["version"] == RECORDS[skill_id].version, skill_id
        assert entry["source"] == SKILL_SOURCE
        # The old lockfile's defect: an unresolvable path that verifies nothing.
        local = REPO_ROOT / entry["localPath"]
        assert local.is_file(), f"{skill_id}: localPath {entry['localPath']} unresolved"
        assert entry["upstreamPath"] == f"skills/{skill_id}/SKILL.md"


@pytest.mark.skipif(
    not MANIFEST_PATH.is_file(), reason="the manifest has not been generated"
)
def test_verify_cli_reports_no_problems():
    registry = load_registry(disabled="")
    problems = skills_manifest.verify_registry(
        registry,
        manifest=skills_manifest.read_manifest(),
        lock=skills_manifest.read_lock(),
    )
    assert problems == [], problems


@pytest.mark.skipif(
    not MANIFEST_PATH.is_file(), reason="the manifest has not been generated"
)
def test_verify_cli_bites_on_a_corrupted_copy(tmp_path):
    """A corrupted byte in a temp copy must be reported, not absorbed."""
    copy = _copy_library(tmp_path)
    target = copy / SKILL_IDS[0] / "SKILL.md"
    target.write_bytes(target.read_bytes().replace(b"\n", b"\n ", 1))
    registry = SkillRegistry.load(root=copy, manifest_path=False)
    problems = skills_manifest.verify_registry(registry)
    assert any("MANIFEST_DRIFT" in p for p in problems), problems


def test_generate_is_idempotent(tmp_path):
    """Regenerating from an unchanged tree must produce identical bytes."""
    first = skills_manifest.build_manifest(REGISTRY)
    second = skills_manifest.build_manifest(load_registry(disabled=""))
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    manifest_path = tmp_path / "m.json"
    assert skills_manifest.write_json(manifest_path, first) is True
    assert skills_manifest.write_json(manifest_path, first) is False, (
        "a no-op rewrite must be reported as unchanged, not as a fresh write"
    )


# --------------------------------------------------------------------- #
# 5. fail-closed behaviour on a deliberately broken tree                    #
# --------------------------------------------------------------------- #


def test_a_renamed_skill_directory_is_reported_not_skipped(tmp_path):
    """Directory name and frontmatter name must agree."""
    copy = _copy_library(tmp_path)
    victim = copy / SKILL_IDS[0]
    shutil.move(str(victim), str(copy / "renamed-skill"))
    report = SkillRegistry.load(root=copy, manifest_path=False).report
    assert ("renamed-skill", "NAME_DIR_MISMATCH") in report.invalid
    assert report.invalid_count == 1
    assert report.valid_count == report.total - 1


def test_a_missing_version_is_reported_not_defaulted(tmp_path):
    copy = _copy_library(tmp_path)
    target = copy / SKILL_IDS[0] / "SKILL.md"
    text = target.read_bytes().decode("utf-8").replace("\r\n", "\n")
    target.write_bytes(
        re.sub(
            r"^  version: .*$", "  other: 1.0.0", text, count=1, flags=re.M
        ).encode("utf-8")
    )
    report = SkillRegistry.load(root=copy, manifest_path=False).report
    assert report.reason_for(SKILL_IDS[0]) == "VERSION_MISSING"


def test_a_malformed_version_is_reported(tmp_path):
    copy = _copy_library(tmp_path)
    target = copy / SKILL_IDS[0] / "SKILL.md"
    text = target.read_bytes().decode("utf-8").replace("\r\n", "\n")
    text = re.sub(r"^  version: .*$", "  version: not-a-version", text, count=1, flags=re.M)
    target.write_bytes(text.encode("utf-8"))
    report = SkillRegistry.load(root=copy, manifest_path=False).report
    assert report.reason_for(SKILL_IDS[0]) == "VERSION_MALFORMED"


def test_a_missing_skill_md_is_reported(tmp_path):
    copy = _copy_library(tmp_path)
    (copy / SKILL_IDS[0] / "SKILL.md").unlink()
    report = SkillRegistry.load(root=copy, manifest_path=False).report
    assert report.reason_for(SKILL_IDS[0]) == "NO_SKILL_MD"


def test_an_undelimited_frontmatter_is_reported(tmp_path):
    copy = _copy_library(tmp_path)
    target = copy / SKILL_IDS[0] / "SKILL.md"
    text = target.read_bytes().decode("utf-8").replace("\r\n", "\n")
    target.write_bytes(("\n" + text).encode("utf-8"))
    report = SkillRegistry.load(root=copy, manifest_path=False).report
    assert report.reason_for(SKILL_IDS[0]) == "FRONTMATTER_UNDELIMITED"


def test_a_missing_description_is_reported(tmp_path):
    copy = _copy_library(tmp_path)
    target = copy / SKILL_IDS[0] / "SKILL.md"
    text = target.read_bytes().decode("utf-8").replace("\r\n", "\n")
    text = re.sub(r"^description:.*$", "description:", text, count=1, flags=re.M)
    target.write_bytes(text.encode("utf-8"))
    report = SkillRegistry.load(root=copy, manifest_path=False).report
    assert report.reason_for(SKILL_IDS[0]) in {
        "DESCRIPTION_MISSING",
        "FRONTMATTER_UNPARSEABLE",
    }


def test_an_unreadable_skill_directory_is_reported_not_skipped(tmp_path):
    copy = _copy_library(tmp_path)
    target = copy / SKILL_IDS[0] / "SKILL.md"
    target.write_bytes(b"\xff\xfe\x00\x01 not utf-8 at all")
    report = SkillRegistry.load(root=copy, manifest_path=False).report
    assert report.reason_for(SKILL_IDS[0]) in {
        "FRONTMATTER_UNPARSEABLE",
        "SKILL_DIR_UNREADABLE",
    }


def test_an_unmapped_role_fails_the_load_and_names_the_skill(tmp_path, monkeypatch):
    """``ROLE_UNMAPPED`` must be a whole-load failure, not a per-record skip.

    A partially mapped table would silently mis-route turns, and the failure would
    surface as worse marketing rather than as an error -- which is the exact
    failure this gate exists to prevent.
    """
    copy = _copy_library(tmp_path)
    monkeypatch.delitem(roles_module.SKILL_ROLE_MAP, SKILL_IDS[0])
    report = SkillRegistry.load(root=copy, manifest_path=False).report
    assert report.reason_for(SKILL_IDS[0]) == "ROLE_UNMAPPED"
    assert report.valid_count == report.total - 1

    problems = skills_manifest.verify_registry(
        SkillRegistry.load(root=copy, manifest_path=False)
    )
    assert any("ROLE_UNMAPPED" in p for p in problems), problems


def test_missing_manifest_entries_are_reported(tmp_path, monkeypatch):
    """``MISSING`` is measured against a manifest, not assumed empty."""
    copy = _copy_library(tmp_path)
    shutil.rmtree(copy / SKILL_IDS[0])
    manifest_file = tmp_path / "m.json"
    manifest_file.write_text(
        json.dumps(
            {"skills": [{"skill_id": s} for s in SKILL_IDS]}, indent=2
        ),
        encoding="utf-8",
    )
    report = SkillRegistry.load(
        root=copy, manifest_path=manifest_file
    ).report
    assert report.missing == (SKILL_IDS[0],)
    assert report.missing_count == 1
    assert report.valid_count == report.total


def test_absent_library_is_unavailable_not_empty(monkeypatch, tmp_path):
    """A missing library must not look like a library with no skills.

    An empty registry would read as "this product has no playbooks", which is a
    different and much more misleading statement than "the library is missing".
    """
    from app import paths as app_paths

    empty = tmp_path / "empty-workspace"
    empty.mkdir()
    monkeypatch.setenv("OMOS_SKILLS_DIR", str(tmp_path / "does-not-exist"))
    monkeypatch.setattr(app_paths, "bundle_root", lambda: empty)
    monkeypatch.setattr(app_paths, "user_data_root", lambda: empty)
    monkeypatch.setattr(skills_registry, "_repo_root", lambda: empty)

    with pytest.raises(SkillRegistryUnavailable):
        resolve_library_root()
    with pytest.raises(SkillRegistryUnavailable):
        load_registry(disabled="")


def test_env_override_selects_the_library(tmp_path):
    """``OMOS_SKILLS_DIR`` is the test/CI seam and wins over the checkout."""
    copy = _copy_library(tmp_path)
    registry = SkillRegistry.load(root=copy, manifest_path=False)
    assert registry.root == copy
    assert set(registry.skill_ids) == set(SKILL_IDS)


# --------------------------------------------------------------------- #
# 6. record shape, enable/disable, and skills-are-knowledge                 #
# --------------------------------------------------------------------- #


def test_record_round_trips_through_json():
    for record in REGISTRY.records:
        again = MarketingSkillRecord.from_dict(record.to_dict())
        assert again == record, record.skill_id
        assert isinstance(again.triggers, tuple)
        assert json.dumps(record.to_dict())  # tuples serialise as lists


def test_validation_statuses_and_reason_codes_are_closed_sets():
    assert VALIDATION_STATUSES == ("valid", "invalid", "missing")
    assert set(REASON_CODES) == {
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
    }
    for record in REGISTRY.records:
        assert record.validation_status in VALIDATION_STATUSES
        assert not record.validation_reason


def test_disabled_skills_are_removed_from_the_candidate_set_only():
    victim = SKILL_IDS[0]
    disabled = REGISTRY.with_disabled(victim)
    assert disabled.get(victim).enabled is False
    assert disabled.get(victim) not in disabled.enabled_records()
    assert victim not in {r.skill_id for r in disabled.enabled_records()}
    # Disabling is not a parse failure: the record stays in the registry.
    assert victim in disabled
    assert disabled.report.invalid_count == 0
    assert len(disabled.records) == len(REGISTRY.records)
    # The library checksum is a property of the files, not of who is enabled.
    assert disabled.report.library_checksum == REGISTRY.report.library_checksum
    assert disabled.enabled_records() == tuple(
        r for r in REGISTRY.records if r.skill_id != victim
    )


def test_disabled_accepts_a_comma_separated_setting_value():
    a, b = SKILL_IDS[0], SKILL_IDS[1]
    registry = REGISTRY.with_disabled(f" {a} , {b} ,")
    off = {r.skill_id for r in registry.records if not r.enabled}
    assert off == {a, b}


def test_records_carry_no_action_fields():
    """Skills are knowledge, tools are action: the vocabularies do not merge."""
    forbidden = {
        "parameters",
        "handler",
        "permission_level",
        "side_effect",
        "tool_id",
        "tool_run_id",
        "cost",
    }
    for record in REGISTRY.records:
        assert not (forbidden & set(record.to_dict())), record.skill_id


def test_skill_module_never_imports_the_tool_registry():
    """A structural guarantee, checked on the AST rather than on prose.

    Scanning the raw text would trip over the docstrings that explain *why* the
    two vocabularies are separate, so the import graph itself is walked.
    """
    package = REPO_ROOT / "app" / "services" / "skills"
    checked = 0
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "services.tools" not in alias.name, (path.name, alias.name)
                    assert "services import tools" not in alias.name, path.name
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "services.tools" not in module, (path.name, module)
                for alias in node.names:
                    assert module != "app.services.tools" or alias.name, path.name
        checked += 1
    assert checked >= 6, f"expected the whole package, walked {checked} files"


def test_wire_dict_carries_no_absolute_path():
    """`sanitize_user_text` redacts absolute paths, so they must not be shipped."""
    payload = json.dumps(REGISTRY.to_dict())
    assert "C:\\" not in payload
    assert not re.search(r'"[a-z_]*root[a-z_]*":\s*"/', payload)
    assert '"library_root": ".agents/skills"' in payload
    for record in REGISTRY.records:
        assert not re.match(r"^[A-Za-z]:[\\/]", record.path)


def test_registry_is_queryable_without_a_database():
    """No fixture, no ordering dependency: construction is enough."""
    assert len(REGISTRY) == REGISTRY.report.valid_count
    assert SKILL_IDS[0] in REGISTRY
    assert REGISTRY.get("no-such-skill") is None
    assert REGISTRY.get(SKILL_IDS[0]) == RECORDS[SKILL_IDS[0]]
    assert list(REGISTRY.skill_ids) == sorted(SKILL_IDS)


def test_validation_report_is_usable_standalone():
    report = SkillValidationReport(root=".", total=0)
    assert report.valid_count == 0
    assert report.missing_count == 0
    assert report.invalid_count == 0
    assert report.skill_ids == ()
    assert report.reason_for("x") is None
    assert "MISSING: 0" in report.summary()
    assert "INVALID: 0" in report.summary()


# --------------------------------------------------------------------- #
# helpers                                                                  #
# --------------------------------------------------------------------- #


def _copy_library(tmp_path: Path) -> Path:
    destination = tmp_path / "skills"
    shutil.copytree(LIBRARY_ROOT, destination)
    return destination
