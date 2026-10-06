"""DEV-008 — public URL consistency and packaging-script gates.

The repository URL is user-facing: it is the README download button, the
Windows Add/Remove Programs entry, and the security contact link. DEV-008
originally hard-coded the *private* development repository into all three while
the founder decision was to publish to a new, clean public repository — a
contradiction that would have shipped dead download links and a security
contact pointing at a repo nobody can see.

These tests make the URL single-sourced and make sure a mismatch fails loudly
rather than shipping.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app import project_urls  # noqa: E402

PUBLIC_URL = project_urls.PUBLIC_REPOSITORY_URL


# --- URL single-sourcing ----------------------------------------------------

def test_generated_iss_matches_the_url_source():
    script = REPO_ROOT / "scripts" / "package" / "sync_project_urls.py"
    result = subprocess.run(
        [sys.executable, str(script), "--check"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        "packaging/project_url.iss is stale. Run:\n"
        "    python scripts/package/sync_project_urls.py\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_iss_consumes_the_generated_url():
    """The installer must not hard-code the URL a second time."""
    text = (REPO_ROOT / "packaging" / "omos.iss").read_text(encoding="utf-8")
    assert '#include "project_url.iss"' in text, \
        "omos.iss must include the generated URL defines"
    defines = re.findall(r'#define\s+AppURL\s+"([^"]+)"', text)
    assert not defines, f"omos.iss hard-codes AppURL: {defines}"


def test_readme_links_to_the_public_repository():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    urls = set(re.findall(r"https://github\.com/[\w.\-]+/[\w.\-]+", readme))
    assert urls, "the README should link to the repository"
    for url in urls:
        assert url.rstrip("/") == PUBLIC_URL.rstrip("/"), (
            f"README links to {url}, but the public repository is {PUBLIC_URL}. "
            f"Update app/project_urls.py and regenerate."
        )


def test_public_url_is_not_the_private_repository():
    """Documented as a release gate: this must be flipped before announcement."""
    if project_urls.is_placeholder():
        pytest.skip(
            "PUBLIC_REPOSITORY_URL still points at the private development "
            "repository. Flip app/project_urls.py once the public repository "
            "exists. See development/runs/DEV-008/founder-publication-runbook.md."
        )
    assert project_urls.PUBLIC_REPOSITORY_URL != project_urls.PRIVATE_REPOSITORY_URL


def test_release_notes_tell_users_not_to_download_source_archives():
    workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "Source code" in workflow, (
        "the release body must tell normal users not to download the source "
        "archives, or they will extract a zip and conclude the app is broken"
    )


def test_release_notes_do_not_claim_local_inference():
    """Regression guard for the F2 finding.

    The release body once said OMOS "runs entirely on your machine", which is
    false for the Quick edition: inference happens at OpenRouter or OpenAI. That
    is the most consequential claim a privacy-adjacent tool can get wrong.
    """
    workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    lowered = workflow.lower()
    for claim in ("runs entirely on your machine", "fully private", "100% offline",
                  "works offline", "completely local"):
        assert claim not in lowered, (
            f"the release body claims '{claim}', which is false for OMOS Quick"
        )
    assert "ai requests are sent" in lowered or "inference is not" in lowered, (
        "the release body must state plainly that AI inference is not local"
    )


def test_changelog_and_readme_agree_that_inference_is_not_local():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8").lower()
    assert "inference is not" in readme or "ai requests go to" in readme, (
        "the README must state that AI requests leave the machine"
    )
    for claim in ("fully private", "100% private", "completely offline"):
        assert claim not in readme, f"the README claims '{claim}', which is false"


# --- version single-sourcing -------------------------------------------------

def test_version_is_consistent_everywhere():
    from app import paths

    version = paths.APP_VERSION
    iss = (REPO_ROOT / "packaging" / "omos.iss").read_text(encoding="utf-8")
    match = re.search(r'#define\s+AppVersion\s+"([^"]+)"', iss)
    assert match, "omos.iss must define AppVersion"
    assert match.group(1) == version, (
        f"packaging/omos.iss says {match.group(1)} but app/paths.py says {version}"
    )

    manifest = REPO_ROOT / "dist" / "release-manifest.json"
    if manifest.is_file():
        import json

        data = json.loads(manifest.read_text(encoding="utf-8"))
        assert data["version"] == version, (
            f"dist/release-manifest.json says {data['version']} but the app "
            f"says {version}. Rebuild the artifacts."
        )


def test_health_endpoint_and_version_constant_cannot_drift():
    """`/health` used to return a hardcoded "v1.0.0" literal."""
    from fastapi.testclient import TestClient

    from app import paths
    from app.main import create_app

    with TestClient(create_app()) as client:
        payload = client.get("/health").json()
    assert payload["version"] == paths.APP_VERSION


def test_installer_script_does_not_hardcode_the_version():
    """F27: it used to print a literal that would go stale on the next bump."""
    script = (REPO_ROOT / "scripts" / "package" / "build_installer.ps1").read_text(
        encoding="utf-8"
    )
    literals = re.findall(r"\b\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?\b", script)
    assert not literals, (
        f"build_installer.ps1 hard-codes version literals {literals}; it should "
        f"read the version from packaging/omos.iss or app/paths.py"
    )


# --- export wiring ----------------------------------------------------------

def test_export_script_is_referenced_by_the_runbook():
    """The runbook is the only control between this build and publication.

    It lives under `development/runs/`, which the public export deliberately
    withholds, so this asserts the reference only where the runbook exists. The
    founder's copy is in the private repository; CI checks the mechanism.
    """
    runbook = REPO_ROOT / "development" / "runs" / "DEV-008" / "founder-publication-runbook.md"
    if not runbook.is_file():
        pytest.skip("publication runbook lives in the private development repository")
    text = runbook.read_text(encoding="utf-8")
    assert "export_public_repo.py" in text, \
        "the runbook must tell the founder how to build the public repository"
