"""DEV-039 (W2) — Beta 2 packaging / distribution contract.

The Beta 2 release ships **two** installers, one per edition profile, and they
are named after the canonical version in ``app.paths.APP_VERSION``:

    OMOS-Local-v<version>.exe        required
    OMOS-OpenRouter-v<version>.exe   required
    OpenMarketingOS-Portable.zip     optional

The Beta 1 scripts still described a single ``OpenMarketingOS-Quick-Setup.exe``
and a portable zip that the manifest treated as required. This module is the
falsifiable gate for the repaired contract: the manifest must cover both
editions, Portable must be optional and hashed only when it exists, the release
workflow must build and publish exactly those names, and the user-facing
instructions must describe real filenames and safe SHA-256 verification.

Every filename here is derived from ``app.paths.APP_VERSION`` rather than written
as a literal, so the gate keeps working after the next version bump instead of
failing for a reason that has nothing to do with the contract.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app import paths  # noqa: E402
from scripts.package import make_manifest  # noqa: E402

VERSION = paths.APP_VERSION
LOCAL_INSTALLER = f"OMOS-Local-v{VERSION}.exe"
OPENROUTER_INSTALLER = f"OMOS-OpenRouter-v{VERSION}.exe"
REQUIRED_INSTALLERS = (LOCAL_INSTALLER, OPENROUTER_INSTALLER)
PORTABLE = make_manifest.PORTABLE_FILENAME

WORKFLOW_TEXT = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(
    encoding="utf-8"
)
WORKFLOW = yaml.safe_load(WORKFLOW_TEXT)
TROUBLESHOOTING = (REPO_ROOT / "docs" / "troubleshooting.md").read_text(encoding="utf-8")


def _step(name: str) -> dict:
    """The real parsed workflow step, so assertions cannot drift on whitespace."""
    for step in WORKFLOW["jobs"]["build-release"]["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"release.yml has no step named {name!r}")


VERSION_STEP = _step("Read the canonical release version")["run"]
PAYLOAD_STEP = _step("Build the Windows payload (frozen backend + React)")["run"]
INSTALLER_STEP = _step("Build the Inno Setup installers (one per edition)")["run"]
PORTABLE_STEP = _step("Build the portable zip (optional)")
MANIFEST_STEP = _step("Generate release manifest and checksums")["run"]
NOTES_STEP = _step("Write release notes")["run"]
UPLOAD_STEP = _step("Upload release assets to the GitHub prerelease")

#: Instructions that would weaken the reader's machine. We verify downloads
#: instead; we never tell anyone to switch protection off.
UNSAFE_VERIFICATION_ADVICE = (
    "disable smartscreen",
    "turn off smartscreen",
    "disable windows defender",
    "add an exception",
    "bypass smartscreen",
    "unblock the file",
)


def _stage(tmp_path, *, editions=("Local", "OpenRouter"), portable=False):
    """Create a fake dist/payload pair. No build, no installer, no network."""
    dist = tmp_path / "dist"
    dist.mkdir()
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "OpenMarketingOS.exe").write_bytes(b"synthetic frozen payload")
    for edition in editions:
        (dist / f"OMOS-{edition}-v{VERSION}.exe").write_bytes(
            f"synthetic {edition} installer".encode("utf-8")
        )
    if portable:
        (dist / PORTABLE).write_bytes(b"synthetic portable archive")
    return dist, payload


def _run(dist, payload, *extra) -> int:
    return make_manifest.main(
        ["--out-dir", str(dist), "--payload", str(payload), *extra]
    )


def _manifest(dist) -> dict:
    return json.loads((dist / "release-manifest.json").read_text(encoding="utf-8"))


def _sums(dist) -> list[tuple[str, str]]:
    text = (dist / "SHA256SUMS.txt").read_text(encoding="utf-8")
    return [tuple(line.split("  ", 1)) for line in text.splitlines() if line.strip()]


# --------------------------------------------------------------------- names

@pytest.mark.parametrize(
    ("edition", "expected"),
    [("Local", LOCAL_INSTALLER), ("OpenRouter", OPENROUTER_INSTALLER)],
)
def test_installer_names_are_derived_from_the_canonical_version(edition, expected):
    assert make_manifest.installer_filename(edition, VERSION) == expected


def test_both_editions_are_required_and_portable_is_not():
    assert make_manifest.required_installers(VERSION) == list(REQUIRED_INSTALLERS)
    assert make_manifest.EDITIONS == ("Local", "OpenRouter")
    assert make_manifest.PORTABLE_FILENAME == PORTABLE
    assert PORTABLE not in make_manifest.required_installers(VERSION)


def test_an_unknown_edition_is_refused_rather_than_invented():
    with pytest.raises(ValueError):
        make_manifest.installer_filename("Quick", VERSION)


# ------------------------------------------------------------------- manifest

def test_manifest_covers_both_required_edition_installers(tmp_path):
    dist, payload = _stage(tmp_path)

    assert _run(dist, payload) == 0

    manifest = _manifest(dist)
    assert manifest["version"] == VERSION
    assert manifest["required_artifacts"] == list(REQUIRED_INSTALLERS)
    assert manifest["optional_artifacts"] == [PORTABLE]
    assert manifest["portable_shipped"] is False

    artifacts = manifest["artifacts"]
    assert set(artifacts) == {"installer_local", "installer_openrouter"}
    assert artifacts["installer_local"]["filename"] == LOCAL_INSTALLER
    assert artifacts["installer_openrouter"]["filename"] == OPENROUTER_INSTALLER
    for key, filename in (
        ("installer_local", LOCAL_INSTALLER),
        ("installer_openrouter", OPENROUTER_INSTALLER),
    ):
        path = dist / filename
        assert artifacts[key]["size_bytes"] == path.stat().st_size
        assert artifacts[key]["sha256"] == make_manifest.sha256_of(path)


@pytest.mark.parametrize("missing", ["Local", "OpenRouter"])
def test_manifest_refuses_to_write_when_an_edition_installer_is_missing(
    tmp_path, capsys, missing
):
    """Half a release is not a release. Refusing is the whole point."""
    editions = tuple(e for e in ("Local", "OpenRouter") if e != missing)
    dist, payload = _stage(tmp_path, editions=editions)

    assert _run(dist, payload) == 1

    captured = capsys.readouterr()
    assert f"OMOS-{missing}-v{VERSION}.exe" in captured.err
    assert not (dist / "release-manifest.json").exists()
    assert not (dist / "SHA256SUMS.txt").exists()


def test_dry_run_flag_still_writes_a_manifest_without_every_artifact(tmp_path):
    """`--no-verify-present` is a dry-run escape hatch, and it must say so."""
    dist, payload = _stage(tmp_path, editions=("Local",))

    assert _run(dist, payload, "--no-verify-present") == 0

    manifest = _manifest(dist)
    assert set(manifest["artifacts"]) == {"installer_local"}
    assert manifest["portable_shipped"] is False


# ------------------------------------------------------------------ portable

def test_portable_is_absent_from_the_manifest_and_checksums_when_not_built(tmp_path):
    dist, payload = _stage(tmp_path, portable=False)

    assert _run(dist, payload) == 0

    manifest = _manifest(dist)
    assert "portable" not in manifest["artifacts"]
    assert manifest["portable_shipped"] is False
    # Still declared as optional, so a reader knows it may exist.
    assert manifest["optional_artifacts"] == [PORTABLE]
    assert [name for _, name in _sums(dist)] == list(REQUIRED_INSTALLERS)


def test_portable_is_listed_and_hashed_only_when_it_exists(tmp_path):
    dist, payload = _stage(tmp_path, portable=True)

    assert _run(dist, payload) == 0

    manifest = _manifest(dist)
    assert manifest["portable_shipped"] is True
    portable = manifest["artifacts"]["portable"]
    assert portable["filename"] == PORTABLE
    assert portable["sha256"] == make_manifest.sha256_of(dist / PORTABLE)

    sums = _sums(dist)
    assert sorted(name for _, name in sums) == sorted(
        [*REQUIRED_INSTALLERS, PORTABLE]
    )


def test_checksum_file_lists_exactly_the_shipped_artifacts_with_real_digests(tmp_path):
    dist, payload = _stage(tmp_path, portable=True)

    assert _run(dist, payload) == 0

    manifest = _manifest(dist)
    sums = dict((name, digest) for digest, name in _sums(dist))
    shipped = {info["filename"]: info["sha256"] for info in manifest["artifacts"].values()}
    assert sums == shipped
    for name, digest in sums.items():
        assert digest == make_manifest.sha256_of(dist / name)


# --------------------------------------------------------------- manifest md

def test_manifest_metadata_stays_honest(tmp_path):
    dist, payload = _stage(tmp_path)

    assert _run(dist, payload) == 0

    manifest = _manifest(dist)
    assert manifest["release_channel"] == "beta"
    assert manifest["platform"] == "windows"
    assert manifest["architecture"] == "x64"
    assert manifest["minimum_windows_version"] == "10.0"

    # Signing state is a fact, not a promise: Beta 2 is unsigned.
    assert manifest["code_signed"] is False
    note = manifest["code_signing_note"].lower()
    assert "unknown publisher" in note
    assert "smartscreen" in note
    assert "sha256sums.txt" in note
    assert "do not disable" in note

    assert set(manifest["editions"]) == {"local", "openrouter"}
    assert manifest["editions"]["local"]["installer"] == LOCAL_INSTALLER
    assert manifest["editions"]["local"]["available"] is True
    assert manifest["editions"]["local"]["requires_cloud_ai"] is False
    assert manifest["editions"]["openrouter"]["installer"] == OPENROUTER_INSTALLER
    assert manifest["editions"]["openrouter"]["available"] is True
    assert manifest["editions"]["openrouter"]["requires_cloud_ai"] is True
    assert manifest["editions"]["openrouter"]["providers"] == ["openrouter", "openai"]


def test_local_edition_does_not_claim_bundled_model_weights(tmp_path):
    dist, payload = _stage(tmp_path)

    assert _run(dist, payload) == 0

    notes = _manifest(dist)["editions"]["local"]["notes"].lower()
    assert "not bundled" in notes
    assert "not downloaded silently" in notes


def test_network_behavior_covers_both_editions_of_this_release(tmp_path):
    dist, payload = _stage(tmp_path)

    assert _run(dist, payload) == 0

    behavior = _manifest(dist)["network_behavior"]
    assert behavior["binds"] == "127.0.0.1"
    assert behavior["exposes_to_lan"] is False
    inference = behavior["ai_inference"].lower()
    assert "omos-local" in inference
    assert "omos-openrouter" in inference
    assert "research" in inference, "research still leaves the machine; say so"


def test_verification_instructions_name_the_artifacts_that_shipped(tmp_path):
    dist, payload = _stage(tmp_path, portable=True)

    assert _run(dist, payload) == 0

    verification = _manifest(dist)["verification"]
    assert verification["checksums_file"] == "SHA256SUMS.txt"
    assert verification["checksum_algorithm"] == "sha256"
    assert verification["code_signed"] is False
    assert verification["unknown_publisher_expected"] is True
    assert verification["powershell_example"] == (
        f"Get-FileHash -Algorithm SHA256 -LiteralPath .\\{LOCAL_INSTALLER}"
    )
    instructions = " ".join(verification["instructions"])
    for filename in (*REQUIRED_INSTALLERS, PORTABLE):
        assert filename in instructions, f"verification instructions omit {filename}"
    lowered = instructions.lower()
    assert "get-filehash" in lowered
    for phrase in UNSAFE_VERIFICATION_ADVICE:
        assert phrase not in lowered, f"verification instructions advise {phrase!r}"


# ------------------------------------------------------------------- workflow

def test_release_workflow_builds_both_editions_with_the_published_names():
    # build_installer.ps1 has MANDATORY -EditionProfile and -RunRoot parameters;
    # calling it without them cannot produce an installer at all.
    assert "@('Local', 'OpenRouter')" in INSTALLER_STEP
    assert "$script -EditionProfile $edition -RunRoot $runRoot" in INSTALLER_STEP
    assert "$runRoot = 'build/release'" in INSTALLER_STEP
    assert 'Join-Path $runRoot "output/$name"' in INSTALLER_STEP
    assert '"OMOS-$edition-v$($env:RELEASE_VERSION).exe"' in INSTALLER_STEP
    assert "-RunRoot 'build/release'" in PAYLOAD_STEP, (
        "the payload must be built into the isolated run root the installers read"
    )
    assert "OpenMarketingOS-Quick-Setup" not in WORKFLOW_TEXT, (
        "the Beta 1 installer name must not survive in the release workflow"
    )


def test_release_workflow_stages_both_installers_under_the_published_names():
    assert 'Copy-Item -LiteralPath $built -Destination (Join-Path \'dist\' $name) -Force' \
        in INSTALLER_STEP
    assert "throw \"Expected installer not produced for the $edition edition: $built\"" \
        in INSTALLER_STEP, "a missing edition installer must fail the release"


def test_release_workflow_manifest_step_targets_the_payload_it_built():
    assert "--out-dir dist --payload 'build/release/payload'" in MANIFEST_STEP
    assert "python $script" in MANIFEST_STEP
    assert '"dist/OMOS-Local-v$($env:RELEASE_VERSION).exe"' in MANIFEST_STEP
    assert '"dist/OMOS-OpenRouter-v$($env:RELEASE_VERSION).exe"' in MANIFEST_STEP
    assert "'dist/SHA256SUMS.txt'" in MANIFEST_STEP
    assert "'dist/release-manifest.json'" in MANIFEST_STEP
    # The manifest is only trustworthy if the release checks what it claims.
    assert "installer_local" in MANIFEST_STEP
    assert "installer_openrouter" in MANIFEST_STEP


def test_release_workflow_publishes_both_editions_and_treats_portable_as_optional():
    published = [
        line.strip()
        for line in UPLOAD_STEP["with"]["files"].strip().splitlines()
        if line.strip()
    ]
    assert published == [
        "dist/OMOS-Local-v${{ env.RELEASE_VERSION }}.exe",
        "dist/OMOS-OpenRouter-v${{ env.RELEASE_VERSION }}.exe",
        "dist/OpenMarketingOS-Portable.zip",
        "dist/SHA256SUMS.txt",
        "dist/release-manifest.json",
    ]
    assert UPLOAD_STEP["with"]["fail_on_unmatched_files"] is False
    assert PORTABLE_STEP["continue-on-error"] is True, (
        "the portable build is optional and must not fail the release"
    )
    assert UPLOAD_STEP["with"]["name"] == "Open Marketing OS Beta 2"
    assert UPLOAD_STEP["with"]["prerelease"] is True


def test_portable_step_can_still_reach_the_payload_it_built():
    """`build_portable.ps1` has no run-root parameter and reads `build/payload`.

    The payload is built into the isolated `build/release/payload`, so without
    staging the optional archive would silently never be produced.
    """
    assert "-RunRoot" not in PORTABLE_STEP["run"]
    assert "Copy-Item -LiteralPath 'build/release/payload' -Destination 'build/payload'" \
        in PORTABLE_STEP["run"]
    portable_script = (
        REPO_ROOT / "scripts" / "package" / "build_portable.ps1"
    ).read_text(encoding="utf-8")
    assert "$Payload  = Join-Path $RepoRoot 'build\\payload'" in portable_script, (
        "if build_portable.ps1 ever gains a run root, drop the staging copy above"
    )


def test_release_workflow_requires_tag_to_match_the_canonical_version():
    assert '$expectedTag = "v$version"' in VERSION_STEP
    assert '$env:RELEASE_TAG -ne $expectedTag' in VERSION_STEP
    assert "must match canonical application version" in VERSION_STEP



def test_release_workflow_reads_the_version_instead_of_hardcoding_it():
    assert r'APP_VERSION\s*=\s*"([^"]+)"' in VERSION_STEP
    assert '"RELEASE_VERSION=$version"' in VERSION_STEP
    literals = re.findall(r"\b\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?\b", WORKFLOW_TEXT)
    assert not literals, (
        f"release.yml hard-codes version literals {literals}; the workflow must "
        f"derive every filename from app.paths.APP_VERSION"
    )


def test_release_notes_are_beta_2_and_say_how_to_verify_without_disabling_security():
    assert "# Open Marketing OS Beta 2" in NOTES_STEP
    assert "Beta 1" not in WORKFLOW_TEXT
    # The notes are a literal here-string with placeholders, substituted with the
    # versioned names at write time, so no version literal is ever written here.
    assert "{{LOCAL_INSTALLER}}" in NOTES_STEP
    assert "{{OPENROUTER_INSTALLER}}" in NOTES_STEP
    assert ".Replace('{{LOCAL_INSTALLER}}'" in NOTES_STEP
    assert ".Replace('{{OPENROUTER_INSTALLER}}'" in NOTES_STEP
    assert '"OMOS-Local-v$($env:RELEASE_VERSION).exe"' in NOTES_STEP
    assert '"OMOS-OpenRouter-v$($env:RELEASE_VERSION).exe"' in NOTES_STEP
    lowered = NOTES_STEP.lower()
    assert "get-filehash -algorithm sha256" in lowered
    assert "sha256sums.txt" in lowered
    assert "unknown publisher" in lowered
    assert "smartscreen" in lowered
    assert 'do not download "source code' in lowered
    for phrase in UNSAFE_VERIFICATION_ADVICE:
        assert phrase not in lowered, f"release notes advise {phrase!r}"


def test_release_notes_never_overclaim_local_privacy():
    """Beta 2 has a local edition, but research still leaves the machine."""
    lowered = NOTES_STEP.lower()
    for claim in (
        "runs entirely on your machine",
        "fully private",
        "100% offline",
        "works offline",
        "completely local",
    ):
        assert claim not in lowered
    assert "ai requests are sent" in lowered or "inference is not" in lowered
    assert "no model weights are bundled" in lowered


# ----------------------------------------------------------- docs: users

def test_troubleshooting_documents_the_real_installers_and_verification():
    assert "OMOS-Local-v<version>.exe" in TROUBLESHOOTING
    assert "OMOS-OpenRouter-v<version>.exe" in TROUBLESHOOTING
    assert PORTABLE in TROUBLESHOOTING
    assert "OpenMarketingOS-Quick-Setup" not in TROUBLESHOOTING
    assert "Get-FileHash -Algorithm SHA256" in TROUBLESHOOTING
    assert "SHA256SUMS.txt" in TROUBLESHOOTING
    assert "release-manifest.json" in TROUBLESHOOTING
    assert "0.1.0-beta.1" not in TROUBLESHOOTING


def test_troubleshooting_never_retires_the_local_edition():
    assert "there is no local-model edition in this beta" not in TROUBLESHOOTING
    assert "OMOS Quick needs" not in TROUBLESHOOTING


def test_troubleshooting_does_not_tell_users_to_weaken_their_machine():
    lowered = TROUBLESHOOTING.lower()
    refusal = "do **not** disable smartscreen"
    assert refusal in lowered
    # Allow the phrase only inside the explicit refusal, never as advice.
    remainder = lowered.replace(refusal, "")
    for phrase in UNSAFE_VERIFICATION_ADVICE:
        assert phrase not in remainder, (
            f"troubleshooting.md advises {phrase!r} outside the explicit refusal"
        )


# ------------------------------------------ the producer still matches the name

def test_installer_script_still_produces_the_names_the_manifest_advertises():
    """The manifest derives names; this pins the producer that must match."""
    script = (
        REPO_ROOT / "scripts" / "package" / "build_installer.ps1"
    ).read_text(encoding="utf-8")
    assert 'OMOS-$EditionProfile-v$ReleaseVersion.exe' in script
    assert r'APP_VERSION\s*=\s*"([^"]+)"' in script
    assert "$ReleaseVersion = $VersionMatch.Groups[1].Value" in script
    assert "[ValidateSet('Local', 'OpenRouter')]" in script
    assert make_manifest.installer_filename("Local", VERSION) == (
        "OMOS-Local-v" + VERSION + ".exe"
    )


@pytest.mark.parametrize("suffix", [".gguf", ".db", ".sqlite", ".sqlite3"])
def test_release_manifest_refuses_model_weights_and_sqlite_data_in_payload(
    tmp_path, capsys, suffix
):
    """User data and model weights are never frozen into a release payload."""
    dist, payload = _stage(tmp_path)
    (payload / f"unexpected{suffix}").write_bytes(b"synthetic forbidden file")

    assert _run(dist, payload) == 1

    captured = capsys.readouterr()
    assert "Refusing to write a manifest" in captured.err
    assert not (dist / "release-manifest.json").exists()
    assert not (dist / "SHA256SUMS.txt").exists()


@pytest.mark.parametrize("marker", ["keys", "secrets", "credentials"])
def test_release_manifest_refuses_nested_credential_directories(
    tmp_path, capsys, marker
):
    dist, payload = _stage(tmp_path)
    nested = payload / "_internal" / "runtime" / marker
    nested.mkdir(parents=True)
    (nested / "placeholder.txt").write_text("synthetic", encoding="utf-8")

    assert _run(dist, payload) == 1

    captured = capsys.readouterr()
    assert "Refusing to write a manifest" in captured.err
    assert marker in captured.err
    assert not (dist / "release-manifest.json").exists()
    assert not (dist / "SHA256SUMS.txt").exists()
