from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGING = ROOT / "packaging"
EXPECTED_FIELDS = {
    "schema_version",
    "profile_id",
    "edition",
    "release_version",
    "initial_settings",
    "local_model_catalog",
    "credential_policy",
    "settings_policy",
}


def _profile(name: str) -> dict:
    return json.loads((PACKAGING / "profiles" / f"{name}.json").read_text(encoding="utf-8"))


def test_profiles_define_exact_first_run_contract_without_secret_material():
    local = _profile("local")
    cloud = _profile("openrouter")

    assert set(local) == EXPECTED_FIELDS
    assert set(cloud) == EXPECTED_FIELDS
    assert local["profile_id"] == "omos-local-v0.1.0-beta.1"
    assert local["edition"] == "Local"
    assert local["release_version"] == "0.1.0-beta.1"
    assert local["initial_settings"] == {
        "ai_mode": "AUTO",
        "manager_provider": "auto",
        "active_model": "Qwen2.5-7B-Instruct",
        "local_model_enabled": True,
        "cloud_escalation": "off",
        "openrouter_default_model": "",
    }
    assert cloud["profile_id"] == "omos-openrouter-v0.1.0-beta.1"
    assert cloud["edition"] == "OpenRouter"
    assert cloud["release_version"] == "0.1.0-beta.1"
    assert cloud["initial_settings"] == {
        "ai_mode": "AUTO",
        "manager_provider": "auto",
        "active_model": "Qwen2.5-7B-Instruct",
        "local_model_enabled": False,
        "cloud_escalation": "on",
        "openrouter_default_model": "openrouter/auto",
    }
    for profile in (local, cloud):
        assert profile["schema_version"] == 1
        assert profile["local_model_catalog"] == {
            "path": "config/local_models.json",
            "default_model_id": "qwen2.5-7b-instruct-q4_k_m",
            "source": "Qwen/Qwen2.5-7B-Instruct-GGUF",
            "revision": "293ca9a10157b0e5fc5cb32af8b636a88bede891",
            "model_bundled": False,
        }
        assert profile["credential_policy"]["credential_bundled"] is False
        assert "only when initializing a pristine settings database" in profile["settings_policy"]
        assert "never overwrite saved settings" in profile["settings_policy"]
        assert "openrouter_key" not in json.dumps(profile).lower()


def test_installer_script_requires_isolated_profile_and_uses_exact_release_names():
    script = (ROOT / "scripts" / "package" / "build_installer.ps1").read_text(encoding="utf-8")
    assert "[ValidateSet('Local', 'OpenRouter')]" in script
    assert "[string]$RunRoot" in script
    assert "OMOS-$EditionProfile-v$ReleaseVersion.exe" in script
    assert "-EditionProfile Local -RunRoot build/local" in script
    assert "-EditionProfile OpenRouter -RunRoot build/openrouter" in script
    assert '"/DEditionProfile=$ProfileSlug"' in script
    assert '"/DProfileId=$ProfileId"' in script
    assert '"/DProfileDescriptor=$issProfile"' in script
    assert '"/DOutputBaseFilename=$([IO.Path]::GetFileNameWithoutExtension($InstallerName))"' in script
    assert r'APP_VERSION\s*=\s*"([^"]+)"' in script
    assert "$ReleaseVersion = $VersionMatch.Groups[1].Value" in script
    assert "'/DReleaseVersion=\"' + $ReleaseVersion + '\"'" in script
    assert "0.1.0-beta.1" not in script
    assert "credential_bundled -ne $false" in script
    assert "model_bundled -ne $false" in script
    assert "-Filter '*.gguf'" in script


def test_inno_installs_profile_descriptor_and_local_runtime_only_for_local():
    iss = (PACKAGING / "omos.iss").read_text(encoding="utf-8")
    assert "AppId={{7C4E1B2A-9F3D-4A6E-8B21-0D5E7C9A4F13}" in iss
    assert 'DestName: "edition-profile.json"' in iss
    assert "#define AppVersion     ReleaseVersion" in iss
    assert "AppVersion={#AppVersion}" in iss
    assert "AppVerName={#AppName} {#AppVersion} ({#EditionDisplayName})" in iss
    assert "AppComments=OMOS edition profile: {#ProfileId}" in iss
    assert "OutputBaseFilename={#OutputBaseFilename}" in iss
    runtime_line = 'Source: "{#PayloadDir}\\_internal\\runtime\\*"; DestDir: "{localappdata}\\OpenMarketingOS\\runtime"'
    assert runtime_line in iss
    runtime_index = iss.index(runtime_line)
    local_guard = iss.rindex('#if EditionProfile == "local"', 0, runtime_index)
    assert local_guard < runtime_index < iss.index("#endif", runtime_index)
    assert "DefaultDirName={autopf}\\{#AppName}" in iss
    assert "UserDataDir = 'OpenMarketingOS'" in iss
    assert "DestDir={localappdata}\\OpenMarketingOS\\runtime" not in iss

