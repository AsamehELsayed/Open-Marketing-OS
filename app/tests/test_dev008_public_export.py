"""DEV-008 — public-repository export gate.

`scripts/package/export_public_repo.py` is the mechanical expression of the
founder decision to publish to a new, clean repository. It decides what leaves
the private development repository, so its behaviour is a security and
licensing control — not a convenience.

The highest-risk thing it filters is unlicensed font binaries
(`Zain-Bold.ttf`, `Cairo-VF.ttf`), which the license audit names as the single
most important exclusion in the project. Before this file, that exclusion was
enforced by one Python list that ran only when a human remembered to run it.
These tests make it a gate.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "package" / "export_public_repo.py"


@pytest.fixture(scope="module")
def exporter():
    spec = importlib.util.spec_from_file_location("export_public_repo", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["export_public_repo"] = module
    spec.loader.exec_module(module)
    return module


def _public_file_list(exporter) -> list[str]:
    """Every relative path the exporter would write."""
    names: list[str] = []
    for root in exporter.INCLUDE_ROOTS:
        source = REPO_ROOT / root
        if not source.exists():
            continue
        for rel in exporter.plan(source):
            names.append(f"{root}/{rel.as_posix()}")
    for name in exporter.INCLUDE_DIRS:
        source = REPO_ROOT / name
        if source.is_dir():
            for rel in exporter.plan(source):
                names.append(f"{name}/{rel.as_posix()}")
    for name in exporter.INCLUDE_FILES:
        if (REPO_ROOT / name).is_file():
            names.append(name)
    docs_root = REPO_ROOT / "docs"
    if docs_root.is_dir():
        for candidate in docs_root.rglob("*"):
            if candidate.is_file():
                rel_doc = candidate.relative_to(REPO_ROOT).as_posix()
                if rel_doc in exporter.DOCS_ALLOWLIST:
                    names.append(rel_doc)
    return sorted(set(names))


# --- the gate itself --------------------------------------------------------

def test_export_contains_no_forbidden_top_level_path(exporter):
    names = _public_file_list(exporter)
    tops = {name.split("/", 1)[0] for name in names}
    for forbidden in exporter.FORBIDDEN_ROOTS:
        assert forbidden not in tops, (
            f"{forbidden}/ would be published. It holds founder business "
            f"material, an unlicensed skills library, or build output."
        )


def test_export_contains_no_font_binaries(exporter):
    """The highest-risk exclusion, asserted rather than hoped for."""
    names = _public_file_list(exporter)
    font_suffixes = (".ttf", ".otf", ".woff", ".woff2", ".eot")
    offenders = [n for n in names if n.lower().endswith(font_suffixes)]
    assert offenders == [], (
        f"font binaries would be published: {offenders}. Their redistribution "
        f"rights are unverified — see docs/opensource/license-audit.md."
    )


def test_export_contains_no_secrets_databases_or_models(exporter):
    names = _public_file_list(exporter)
    bad_suffixes = (
        ".db", ".sqlite", ".sqlite3", ".gguf", ".ggml", ".safetensors",
        ".onnx", ".pt", ".pth", ".ckpt", ".pem", ".key", ".p12", ".pfx",
        ".exe", ".dll", ".msi", ".zip", ".7z",
    )
    offenders = [n for n in names if n.lower().endswith(bad_suffixes)]
    assert offenders == [], f"secrets/databases/models/binaries would ship: {offenders}"


def test_export_never_includes_a_real_env_file(exporter):
    names = _public_file_list(exporter)
    env_like = [
        n for n in names
        if Path(n).name == ".env" or Path(n).name.startswith(".env.")
        if not any(
            __import__("fnmatch").fnmatch(Path(n).name, allow)
            for allow in exporter.ALLOW_PATTERNS
        )
    ]
    assert env_like == [], f"an environment file would be published: {env_like}"
    # The placeholder template is intentionally public.
    assert ".env.example" in names


def test_export_contains_no_local_databases(exporter):
    """Catches the specific shape a developer machine produces."""
    names = _public_file_list(exporter)
    offenders = [n for n in names if "marketing.db" in n or n.endswith(".db-journal")]
    assert offenders == [], f"a local database would be published: {offenders}"


def test_export_excludes_dependency_and_build_directories(exporter):
    names = _public_file_list(exporter)
    for banned in ("node_modules", "__pycache__", ".pytest_cache", ".venv"):
        offenders = [n for n in names if f"/{banned}/" in f"/{n}"]
        assert offenders == [], f"{banned}/ would be published: {offenders[:5]}"


# --- fail-closed behaviour --------------------------------------------------

def test_fail_closed_check_rejects_overlapping_lists(exporter, monkeypatch):
    monkeypatch.setattr(exporter, "INCLUDE_ROOTS", ("app", "company"))
    with pytest.raises(exporter.ExportError):
        exporter.fail_closed_check()


def test_audit_payload_rejects_a_forbidden_top_level(exporter):
    problems = exporter.audit_payload(["company/company.yaml", "app/main.py"])
    assert problems, "the audit must reject a forbidden top-level path"
    assert any("company" in p for p in problems)


def test_audit_payload_rejects_an_excluded_pattern(exporter):
    problems = exporter.audit_payload(["app/fonts/Zain-Bold.ttf"])
    assert problems
    assert any("Zain-Bold.ttf" in p for p in problems)


def test_audit_payload_accepts_a_clean_tree(exporter):
    clean = [
        "README.md", "LICENSE", "NOTICE", "app/main.py", "app/paths.py",
        "frontend/src/App.tsx", "launcher/omos_launcher.py",
        "packaging/omos.spec", "docs/architecture.md", ".env.example",
        ".github/workflows/ci.yml",
    ]
    assert exporter.audit_payload(clean) == []


# --- the environment file must stay a template ------------------------------

#: Key names that would indicate a real credential had been pasted into the
#: template. Non-secret settings such as OMOS_DATA_DIR legitimately carry values.
SECRET_KEY_MARKERS = (
    "API_KEY", "APIKEY", "_TOKEN", "SECRET", "PASSWORD", "PASSWD",
    "ACCESS_KEY", "PRIVATE_KEY", "CLIENT_SECRET", "APP_SECRET",
    "OPENAI_API", "OPENROUTER_API", "APIFY_API", "BRIGHTDATA_API",
    "META_ACCESS", "META_OAUTH_CLIENT", "META_APP",
)

#: Suffixes that make a key a location rather than a secret.
_LOCATION_SUFFIXES = ("_DIR", "_PATH", "_FILE", "_FOLDER", "_ROOT")


def test_env_example_contains_no_secret_values():
    """`.env.example` ships publicly; it must hold settings, never credentials.

    Non-secret settings legitimately have values (`OMOS_DATA_DIR=data`,
    `AI_RUNTIME=langgraph`). What must never appear is a credential: a
    secret-looking key with a non-empty value.
    """
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    offenders: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().upper()
        value = value.strip()
        if key.endswith(_LOCATION_SUFFIXES):
            # A path to where secrets live is not a secret.
            continue
        looks_secret = any(marker in key for marker in SECRET_KEY_MARKERS)
        if looks_secret and value:
            offenders.append(line)
    assert offenders == [], f".env.example appears to assign a credential: {offenders}"


def test_env_example_has_no_inline_secret_shaped_token():
    """Belt and braces: no value anywhere that looks like a live credential."""
    import re

    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    patterns = (
        r"sk-or-v1-", r"sk-proj-", r"sk-ant-", r"AKIA[0-9A-Z]{16}",
        r"apify_api_", r"gh[pousr]_", r"xox[baprs]-",
    )
    for pattern in patterns:
        assert not re.search(pattern, text), f".env.example contains {pattern}"
