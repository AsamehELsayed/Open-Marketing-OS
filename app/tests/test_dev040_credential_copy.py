"""Regression coverage for platform-specific credential protection reporting."""
from app.services.config_service import _credential_encryption_label


def test_credential_encryption_label_matches_selected_backend():
    assert _credential_encryption_label("dpapi_file") == (
        "Windows DPAPI (user-bound encrypted storage)"
    )
    assert _credential_encryption_label("linux_secret_service") == (
        "Linux Secret Service (encrypted session and persistent keyring)"
    )
    assert _credential_encryption_label("unknown") == "Unavailable"
