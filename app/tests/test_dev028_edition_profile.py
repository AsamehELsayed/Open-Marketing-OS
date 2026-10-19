from pathlib import Path

import pytest

from app.paths import APP_VERSION
from app.services.edition_profile import apply_first_run_profile

# Profile IDs are stamped from the canonical release version, so this module
# asserts the first-run *behaviour* for whichever release is current. The
# canonical version itself is pinned by
# `app/tests/test_dev039_version_consistency.py`.
LOCAL_PROFILE_ID = f"omos-local-v{APP_VERSION}"
OPENROUTER_PROFILE_ID = f"omos-openrouter-v{APP_VERSION}"


@pytest.mark.parametrize(
    ("profile_slug", "profile_id", "expected"),
    [
        (
            "local",
            LOCAL_PROFILE_ID,
            {
                "ai_mode": "AUTO",
                "manager_provider": "auto",
                "active_model": "Qwen2.5-7B-Instruct",
                "local_model_enabled": "on",
                "cloud_escalation": "off",
                "openrouter_default_model": "",
                "edition_profile": LOCAL_PROFILE_ID,
            },
        ),
        (
            "openrouter",
            OPENROUTER_PROFILE_ID,
            {
                "ai_mode": "AUTO",
                "manager_provider": "auto",
                "active_model": "Qwen2.5-7B-Instruct",
                "local_model_enabled": "off",
                "cloud_escalation": "on",
                "openrouter_default_model": "openrouter/auto",
                "edition_profile": OPENROUTER_PROFILE_ID,
            },
        ),
    ],
)
def test_known_profile_seeds_expected_defaults_only_for_new_database(
    monkeypatch, profile_slug, profile_id, expected
):
    descriptor = Path(__file__).resolve().parents[2] / "packaging" / "profiles" / f"{profile_slug}.json"
    writes = {}

    from app.services.config_service import ConfigService

    monkeypatch.setattr(
        ConfigService,
        "set_setting",
        lambda key, value, conn=None: writes.__setitem__(key, value),
    )

    applied = apply_first_run_profile(descriptor, database_preexisted=False)

    assert applied == profile_id
    assert writes == expected


def test_existing_database_settings_are_never_rewritten(monkeypatch):
    descriptor = (
        Path(__file__).resolve().parents[2]
        / "packaging" / "profiles" / "openrouter.json"
    )

    from app.services.config_service import ConfigService

    def fail_if_written(*args, **kwargs):
        raise AssertionError("existing user settings must remain untouched")

    monkeypatch.setattr(ConfigService, "set_setting", fail_if_written)

    assert apply_first_run_profile(descriptor, database_preexisted=True) is None


def test_descriptor_with_unapproved_routing_defaults_is_rejected(tmp_path, monkeypatch):
    import json
    source = (
        Path(__file__).resolve().parents[2]
        / "packaging" / "profiles" / "openrouter.json"
    )
    descriptor = json.loads(source.read_text(encoding="utf-8"))
    descriptor["initial_settings"]["manager_provider"] = "openrouter"
    path = tmp_path / "edition-profile.json"
    path.write_text(json.dumps(descriptor), encoding="utf-8")

    from app.services.config_service import ConfigService

    def fail_if_written(*args, **kwargs):
        raise AssertionError("invalid descriptors must not write settings")

    monkeypatch.setattr(ConfigService, "set_setting", fail_if_written)

    with pytest.raises(ValueError):
        apply_first_run_profile(path, database_preexisted=False)


@pytest.mark.parametrize("profile_id", [LOCAL_PROFILE_ID, OPENROUTER_PROFILE_ID])
def test_ai_config_exposes_installed_edition_profile(monkeypatch, profile_id):
    from app.services.config_service import ConfigService

    def get_setting(cls, key, default=None, conn=None):
        return profile_id if key == "edition_profile" else default

    monkeypatch.setattr(ConfigService, "get_setting", classmethod(get_setting))
    monkeypatch.setattr(
        ConfigService, "is_openai_configured",
        classmethod(lambda cls, conn=None: False),
    )
    monkeypatch.setattr(
        ConfigService, "get_openrouter_credential_status",
        classmethod(lambda cls, conn=None: "not_configured"),
    )
    monkeypatch.setattr(
        ConfigService, "is_openrouter_configured",
        classmethod(lambda cls, conn=None: False),
    )

    config = ConfigService.get_ai_config(conn=object())

    assert config["edition_profile"] == profile_id
