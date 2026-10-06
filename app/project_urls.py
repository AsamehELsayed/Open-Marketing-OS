"""Single source of truth for public-facing project URLs (DEV-008).

Every user-visible link — the README download button, the badges, the Windows
Add/Remove Programs entry, the security contact link — must point at the
**public** repository.

DEV-008 initially hard-coded the private development repository
(`<PRIVATE-ORIGIN-REDACTED>`) into the README and `packaging/omos.iss`,
while the founder decision was to publish to a *new, clean* public repository.
That contradiction would have shipped download links that point nowhere useful,
and baked the wrong URL into every user's installed application.

`packaging/omos.iss` is preprocessed by Inno Setup, so it cannot import a Python
module. It therefore includes `packaging/project_url.iss`, which is generated
from this file. `test_public_urls_are_consistent` enforces that the two agree,
so the URL is defined once and cannot drift.

The founder-confirmed public owner is `AsamehELsayed`; the repository does not
become public until a separate publication approval. Keep the public identity
separate from the redacted private-development identity below.

    python scripts/package/sync_project_urls.py
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Founder-confirmed public repository target; publication is separately gated.
PUBLIC_REPOSITORY_URL = "https://github.com/AsamehELsayed/Open-Marketing-OS"

#: The private development repository. Never linked from user-facing material.
PRIVATE_REPOSITORY_URL = "https://github.com/<PRIVATE-ORIGIN-REDACTED>"

#: Public repository slug. This does not indicate that the repository exists.
PUBLIC_REPOSITORY_SLUG = "AsamehELsayed/Open-Marketing-OS"

#: Repository name inside the slug, used for in-repo relative links.
PUBLIC_REPO_NAME = "Open-Marketing-OS"


def releases_url() -> str:
    return f"{PUBLIC_REPOSITORY_URL}/releases"


def latest_release_url() -> str:
    return f"{PUBLIC_REPOSITORY_URL}/releases/latest"


def issues_url() -> str:
    return f"{PUBLIC_REPOSITORY_URL}/issues"


def security_advisory_url() -> str:
    return f"{PUBLIC_REPOSITORY_URL}/security/advisories/new"


def is_placeholder() -> bool:
    """True if owner-specific public URL metadata has not been supplied.

    Surfaced in the manifest and asserted in tests so it is impossible to
    publish without noticing.
    """
    return "<OWNER>" in PUBLIC_REPOSITORY_URL


def render_iss_defines() -> str:
    """The Inno Setup `#define` block, generated so it cannot drift."""
    return "\n".join([
        "; GENERATED FILE - do not edit by hand.",
        "; Source of truth: app/project_urls.py :: PUBLIC_REPOSITORY_URL",
        "; Regenerate with: python scripts/package/sync_project_urls.py",
        '#define AppURL         "' + PUBLIC_REPOSITORY_URL + '"',
        '#define AppReleasesURL "' + releases_url() + '"',
        "",
    ])


if __name__ == "__main__":
    print("public repository :", PUBLIC_REPOSITORY_URL)
    print("releases          :", releases_url())
    if is_placeholder():
        print()
        print("WARNING: the public URL is still a placeholder.")
