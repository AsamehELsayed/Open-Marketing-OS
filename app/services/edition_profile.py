"""Apply immutable installer defaults to a brand-new OMOS database only.

The installer profile is a package identity hint, not a user preference. A
profile can initialize routing defaults once when no database existed before
startup. Existing user settings, projects, and credentials are never rewritten.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.config_service import ConfigService


_RELEASE_VERSION = "0.1.0-beta.1"
_PROFILE_SETTINGS: dict[str, dict[str, Any]] = {
    "omos-local-v0.1.0-beta.1": {
        "ai_mode": "AUTO",
        "manager_provider": "auto",
        "active_model": "Qwen2.5-7B-Instruct",
        "local_model_enabled": True,
        "cloud_escalation": "off",
        "openrouter_default_model": "",
    },
    "omos-openrouter-v0.1.0-beta.1": {
        # AUTO selects the configured cloud provider once Local is disabled.
        "manager_provider": "auto",
        "ai_mode": "AUTO",
        "active_model": "Qwen2.5-7B-Instruct",
        "local_model_enabled": False,
        "cloud_escalation": "on",
        "openrouter_default_model": "openrouter/auto",
    },
}
_PROFILE_CREDENTIALS = {
    "omos-local-v0.1.0-beta.1": {"provider": "user-selected", "credential_bundled": False},
    "omos-openrouter-v0.1.0-beta.1": {
        "provider": "openrouter",
        "credential_bundled": False,
    },
}
_MODEL_CATALOG = {
    "path": "config/local_models.json",
    "default_model_id": "qwen2.5-7b-instruct-q4_k_m",
    "source": "Qwen/Qwen2.5-7B-Instruct-GGUF",
    "revision": "293ca9a10157b0e5fc5cb32af8b636a88bede891",
    "model_bundled": False,
}
_SETTINGS_POLICY = (
    "apply only when initializing a pristine settings database; never overwrite "
    "saved settings, projects, workspace files, or credentials"
)


def apply_first_run_profile(
    profile_path: str | Path,
    *,
    database_preexisted: bool,
    conn: Any = None,
) -> str | None:
    """Persist a known profile only when the database is newly created.

    The profile descriptor contains only ``profile_id``. Defaults are fixed in
    application code so an installer file cannot set arbitrary configuration.
    Returns the applied profile ID, or ``None`` when user data already existed
    or no descriptor is installed.
    """
    if database_preexisted:
        return None

    path = Path(profile_path)
    if not path.is_file():
        return None

    try:
        descriptor = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Installer edition profile is unreadable") from exc
    expected_fields = {
        "schema_version", "profile_id", "edition", "release_version",
        "initial_settings", "local_model_catalog", "credential_policy",
        "settings_policy",
    }
    if not isinstance(descriptor, dict) or set(descriptor) != expected_fields:
        raise ValueError("Installer edition profile has an invalid shape")

    profile_id = descriptor.get("profile_id")
    if not isinstance(profile_id, str) or profile_id not in _PROFILE_SETTINGS:
        raise ValueError("Installer edition profile is not supported")

    edition = "Local" if profile_id.startswith("omos-local-") else "OpenRouter"
    expected_profile_id = f"omos-{edition.lower()}-v{_RELEASE_VERSION}"
    if (
        descriptor["schema_version"] != 1
        or descriptor["edition"] != edition
        or descriptor["release_version"] != _RELEASE_VERSION
        or profile_id != expected_profile_id
        or descriptor["initial_settings"] != _PROFILE_SETTINGS[profile_id]
        or descriptor["local_model_catalog"] != _MODEL_CATALOG
        or descriptor["credential_policy"] != _PROFILE_CREDENTIALS[profile_id]
        or descriptor["settings_policy"] != _SETTINGS_POLICY
    ):
        raise ValueError("Installer edition profile does not match the supported release")

    defaults = _PROFILE_SETTINGS[profile_id]
    for key, value in defaults.items():
        if key == "local_model_enabled":
            value = "on" if value else "off"
        ConfigService.set_setting(key, value, conn=conn)
    ConfigService.set_setting("edition_profile", profile_id, conn=conn)
    return profile_id
