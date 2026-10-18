"""Keep the security page release status aligned with the canonical app version."""
from __future__ import annotations

from pathlib import Path

from app.paths import APP_VERSION

ROOT = Path(__file__).resolve().parents[1].parent


def test_security_release_status_tracks_the_canonical_version_and_safe_install_guidance():
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")

    assert f"Open Marketing OS `v{APP_VERSION}` is a **PRE-RELEASE / BETA**." in security
    assert "not code-signed" in security
    assert "Unknown Publisher" in security
    assert "SHA-256" in security
    assert "Do not disable operating-system security protections" in security
