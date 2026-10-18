"""DEV-039 regression tests for Beta 2 application identity consistency.

`app.paths.APP_VERSION` is the single canonical version. Every other
user-visible or artifact-visible copy of it - the health route, the installer
profile descriptors, the frontend edition-profile matcher and the private
frontend package metadata - must agree with it.

The canonical string is pinned here on purpose. Everything else derives from it
at runtime, so this module is the one place a future release bump has to touch
together with `app/paths.py`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app import paths
from app.paths import APP_VERSION
from app.services import edition_profile

ROOT = Path(__file__).resolve().parents[2]
CANONICAL_VERSION = "0.1.0-beta.2"
CANONICAL_PROFILE_IDS = {
    "local": f"omos-local-v{CANONICAL_VERSION}",
    "openrouter": f"omos-openrouter-v{CANONICAL_VERSION}",
}
EDITION_PROFILE_MODULE = ROOT / "app" / "services" / "edition_profile.py"
EDITION_PROFILE_COPY = ROOT / "frontend" / "src" / "editionProfileCopy.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _descriptor(slug: str) -> dict:
    return json.loads(_read(ROOT / "packaging" / "profiles" / f"{slug}.json"))


def test_canonical_backend_version_is_beta_two():
    assert APP_VERSION == CANONICAL_VERSION
    assert paths.APP_VERSION == CANONICAL_VERSION
    assert re.fullmatch(r"\d+\.\d+\.\d+-[0-9A-Za-z.]+", APP_VERSION)


def test_health_route_reports_the_canonical_version():
    from app.routes.health import health

    assert health() == {"status": "ok", "version": CANONICAL_VERSION}


def test_edition_profile_defaults_are_stamped_from_the_canonical_version():
    assert edition_profile._RELEASE_VERSION == APP_VERSION
    assert set(edition_profile._PROFILE_SETTINGS) == set(CANONICAL_PROFILE_IDS.values())
    assert set(edition_profile._PROFILE_CREDENTIALS) == set(CANONICAL_PROFILE_IDS.values())

    # The release version must never be written out again in the module that
    # consumes it, otherwise the next bump would have to edit two files.
    module_source = _read(EDITION_PROFILE_MODULE)
    assert "0.1.0-beta" not in module_source
    assert "from app.paths import APP_VERSION" in module_source


def test_packaging_descriptors_match_the_canonical_version():
    for slug, profile_id in CANONICAL_PROFILE_IDS.items():
        descriptor = _descriptor(slug)

        assert descriptor["profile_id"] == profile_id
        assert descriptor["release_version"] == APP_VERSION
        assert descriptor["initial_settings"] == edition_profile._PROFILE_SETTINGS[profile_id]
        assert descriptor["credential_policy"] == edition_profile._PROFILE_CREDENTIALS[profile_id]
        assert descriptor["local_model_catalog"] == edition_profile._MODEL_CATALOG


def test_shipped_descriptors_are_accepted_by_the_first_run_applier(monkeypatch):
    from app.services.config_service import ConfigService

    writes: dict[str, str] = {}
    monkeypatch.setattr(
        ConfigService,
        "set_setting",
        lambda key, value, conn=None: writes.__setitem__(key, value),
    )

    for slug, profile_id in CANONICAL_PROFILE_IDS.items():
        writes.clear()
        applied = edition_profile.apply_first_run_profile(
            ROOT / "packaging" / "profiles" / f"{slug}.json",
            database_preexisted=False,
        )
        assert applied == profile_id
        assert writes["edition_profile"] == profile_id


def test_frontend_edition_matcher_pins_no_release():
    source = _read(EDITION_PROFILE_COPY)

    # The retired hardcoded Beta 1 profile IDs must not come back, and no other
    # release literal may be pinned either.
    assert "omos-local-v0.1.0-beta.1" not in source
    assert "omos-openrouter-v0.1.0-beta.1" not in source
    assert re.search(r"omos-(local|openrouter)-v\d", source) is None
    assert "0.1.0-beta" not in source

    # Matching stays version-tolerant: the edition slug is exact and the release
    # segment is structural, so a stored profile from any release resolves.
    assert r"\d+\.\d+\.\d+" in source
    assert "local" in source and "openrouter" in source
    assert "generic" in source


def test_frontend_package_metadata_matches_the_canonical_version():
    package_text = _read(ROOT / "frontend" / "package.json")
    lock_text = _read(ROOT / "frontend" / "package-lock.json")
    package = json.loads(package_text)
    lock = json.loads(lock_text)
    root_entry = lock["packages"][""]

    assert package["private"] is True
    assert package["version"] == APP_VERSION
    assert lock["name"] == package["name"] == root_entry["name"]
    assert lock["version"] == APP_VERSION
    assert root_entry["version"] == APP_VERSION

    # The canonical application version appears only in the lockfile root metadata;
    # the lock may also advance independently for compatible security patches.
    assert lock["lockfileVersion"] == 3
    assert root_entry["dependencies"] == package["dependencies"]
    assert root_entry["devDependencies"] == package["devDependencies"]
    assert lock_text.count(CANONICAL_VERSION) == 2


def test_beta_two_notes_document_the_unsigned_installer_safely():
    notes_path = ROOT / "docs" / "release-notes-v0.1.0-beta.2.md"
    notes = _read(notes_path)

    assert CANONICAL_VERSION in notes
    assert "PRE-RELEASE" in notes
    assert "Unknown Publisher" in notes
    assert "SmartScreen" in notes
    assert "SHA-256" in notes
    # PowerShell verification guidance, not a vague "check the hash" claim.
    assert "Get-FileHash" in notes
    assert "-Algorithm SHA256" in notes
    assert "SHA256SUMS.txt" in notes
    assert "checksums-sha256.txt" not in notes.lower()
    assert "Get-FileHash -Algorithm SHA256 -LiteralPath" in notes
    assert "Get-FileHash -Algorithm SHA256 -LiteralPath .\\SHA256SUMS.txt" not in notes
    assert "Known limitations" in notes

    # The notes must never tell a reader to weaken a protection.
    assert "Do not disable Windows security protections" in notes
    lowered = notes.lower()
    for forbidden in (
        "disable windows defender",
        "turn off microsoft defender",
        "disable antivirus",
        "disable smartscreen",
        "bypass smartscreen",
        "bypass windows security",
    ):
        assert forbidden not in lowered


def test_historical_beta_one_notes_are_preserved():
    changelog = _read(ROOT / "CHANGELOG.md")

    assert "## [0.1.0-beta.2]" in changelog
    assert "## [0.1.0-beta.1]" in changelog
    assert changelog.index("## [0.1.0-beta.2]") < changelog.index("## [0.1.0-beta.1]")
    assert (ROOT / "docs" / "release-notes-v0.1.0-beta.1.md").is_file()
