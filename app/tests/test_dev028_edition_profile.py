from pathlib import Path

import pytest

from app.services.edition_profile import apply_first_run_profile


@pytest.mark.parametrize(
    ("profile_slug", "profile_id", "expected"),
    [
        (
            "local",
            "omos-local-v0.1.0-beta.1",
            {
                "ai_mode": "AUTO",
                "manager_provider": "auto",
                "active_model": "Qwen2.5-7B-Instruct",
                "local_model_enabled": "on",
                "cloud_escalation": "off",
                "openrouter_default_model": "",
                "edition_profile": "omos-local-v0.1.0-beta.1",
            },
        ),
        (
            "openrouter",
            "omos-openrouter-v0.1.0-beta.1",
            {
                "ai_mode": "AUTO",
                "manager_provider": "auto",
                "active_model": "Qwen2.5-7B-Instruct",
                "local_model_enabled": "off",
                "cloud_escalation": "on",
                "openrouter_default_model": "openrouter/auto",
                "edition_profile": "omos-openrouter-v0.1.0-beta.1",
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
