"""DEV-008-SKILLS-OPS W9 (integrator) — manifest / provenance drift gate.

Recomputes ``library_checksum`` and every per-skill checksum from the file
tree alone and compares them to the recorded manifest + lock (plan §6.6,
§7 A1/A17). Then corrupts exactly one byte in a TEMP COPY of one SKILL.md
(the real tree is never mutated) and asserts the verifier exits non-zero
with a named ``MANIFEST_DRIFT`` reason code — the proof the gate bites.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / ".agents" / "skills"
MANIFEST_PATH = REPO_ROOT / "docs" / "marketing-skills-manifest.json"


def test_library_and_skill_checksums_recompute_from_the_tree():
    """library_checksum + every per-skill checksum match the manifest."""
    import json

    from app.services.skills import load_registry
    from app.services.skills.records import library_checksum

    registry = load_registry(disabled="")
    assert registry.report.invalid_count == 0, registry.report.invalid
    assert registry.report.missing == (), registry.report.missing

    recomputed_per_skill = {
        record.skill_id: record.checksum for record in registry.records
    }
    recomputed_library = library_checksum(recomputed_per_skill)
    print(f"\nmeasured: skills={len(recomputed_per_skill)} "
          f"library={recomputed_library[:12]}...")

    document = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert document["library_checksum"] == recomputed_library, (
        "manifest library_checksum does not match the file tree"
    )
    recorded = {entry["skill_id"]: entry["checksum"] for entry in document["skills"]}
    assert set(recorded) == set(recomputed_per_skill), (
        f"manifest skill set drifted: "
        f"missing={sorted(set(recomputed_per_skill) - set(recorded))} "
        f"stale={sorted(set(recorded) - set(recomputed_per_skill))}"
    )
    mismatched = sorted(
        skill_id for skill_id, digest in recomputed_per_skill.items()
        if recorded[skill_id] != digest
    )
    assert not mismatched, f"per-skill checksum drift: {mismatched}"


def test_lock_checksums_and_paths_resolve():
    """skills-lock.json schema 2: every computedHash recomputes, every
    localPath resolves, library_checksum matches the manifest."""
    import hashlib
    import json

    from app.services.skills.records import normalize_bytes

    lock = json.loads((REPO_ROOT / "skills-lock.json").read_text(encoding="utf-8"))
    assert lock["schema"] == "omos.skills-lock/2", lock.get("schema")
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert lock["library_checksum"] == manifest["library_checksum"]

    checked = 0
    for skill_id, entry in lock["skills"].items():
        local = REPO_ROOT / entry["localPath"]
        assert local.is_file(), f"{skill_id}: localPath missing: {entry['localPath']}"
        digest = hashlib.sha256(normalize_bytes(local.read_bytes())).hexdigest()
        assert digest == entry["computedHash"], (
            f"{skill_id}: computedHash does not recompute"
        )
        checked += 1
    print(f"\nmeasured: lock_entries_verified={checked}")
    assert checked >= 1


def test_single_byte_corruption_in_temp_copy_fails_with_named_reason(tmp_path):
    """One corrupted byte in a TEMP COPY → non-zero exit + MANIFEST_DRIFT."""
    skill_dirs = sorted(p for p in SKILLS_ROOT.iterdir() if p.is_dir())
    assert skill_dirs, "no vendored skills on disk"
    target = skill_dirs[0]

    copy_root = tmp_path / "skills"
    shutil.copytree(SKILLS_ROOT, copy_root)
    victim = copy_root / target.name / "SKILL.md"
    raw = victim.read_bytes()
    assert len(raw) > 1024, "SKILL.md unexpectedly tiny; refusing a vacuous test"
    body_start = raw.find(b"\n---", 3)
    assert body_start != -1
    pos = body_start + 16
    victim.write_bytes(raw[:pos] + bytes([raw[pos] ^ 0x01]) + raw[pos + 1:])

    assert (SKILLS_ROOT / target.name / "SKILL.md").read_bytes() == raw, (
        "the real vendored library was mutated by this test"
    )

    proc = subprocess.run(
        [sys.executable, "-m", "app.services.skills.manifest",
         "--verify", "--root", str(copy_root)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    combined = proc.stdout + proc.stderr
    print(f"\nmeasured: exit={proc.returncode} "
          f"names_drift={'MANIFEST_DRIFT' in combined}")
    assert proc.returncode != 0, (
        f"--verify exited 0 on a corrupted temp copy:\n{combined[:2000]}"
    )
    assert "MANIFEST_DRIFT" in combined, (
        f"--verify failed without a named MANIFEST_DRIFT reason:\n{combined[:2000]}"
    )
