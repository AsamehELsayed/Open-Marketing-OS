"""Gate 7 secret scan for the public repository (DEV-008-PUBLISH-GATE).

Run against the **exported** tree, not the development checkout, because the
development checkout legitimately contains a founder ``.env`` and private
workspace files that are never published.

    python scripts/package/secret_scan.py --root <export-dir>
    python scripts/package/secret_scan.py --root <export-dir> --json report.json

Exits non-zero on any real finding, so it can be a CI step.

Design notes
------------
**Sentinels are allowlisted per file, never per pattern.** A test that proves a
secret is never leaked has to put a secret-shaped value in the repository. Those
values are marked at their definition site with a comment naming them as
sentinels, and each is listed in :data:`ALLOWED_SENTINELS` together with the test
that needs it. An allowlist entry is a specific literal in a specific file, so
deleting the test removes the exemption automatically rather than leaving a
permanent hole.

**A scanner that cries wolf gets switched off.** The AWS example access key that
previously appeared in ``packaging/omos.spec`` (inside a comment) and in a
retrieval test was harmless, but it is exactly the pattern every hosted scanner
flags. A gate that reports noise on every run trains people to ignore it. Those
two occurrences were removed rather than allowlisted.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

#: Text file extensions worth scanning.
TEXT_SUFFIXES = frozenset({
    ".md", ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".yaml", ".yml",
    ".sql", ".iss", ".spec", ".ps1", ".sh", ".bat", ".txt", ".cfg", ".ini",
    ".toml", ".html", ".css", ".env", ".example", ".gitignore", "",
})

#: Never scanned: build output, dependencies, VCS metadata, local state.
SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "dist", "build", ".idea", ".vscode",
})

#: Top-level trees that are **withheld from the public repository by policy**.
#:
#: These are kept separate from :data:`SKIP_DIRS` on purpose. SKIP_DIRS is about
#: machine-local noise; this list is about material that is deliberately not
#: published, and it mirrors ``FORBIDDEN_ROOTS`` in
#: ``scripts/package/export_public_repo.py``.
#:
#: Their contents are not this scanner's business. The export script already
#: fails closed if any of them is ever requested for publication, so a secret in
#: `development/runs/` cannot reach a release through this path. Scanning them
## anyway would mean every local run reports the founder's own `.env` and private
#: workspace, and a gate that reports the same unavoidable findings every time is
#: a gate people learn to skip.
PRIVATE_DIRS = frozenset({
    "development", "data", "knowledge", "production", "research", "strategy",
    "company", "state", "execution", "creative", "templates", "workflows",
    "analytics", "content", "maintenance", "tests", "credentials", "logs",
    ".agents", ".opencode", ".dogfood-scratch", ".reproof-scratch",
})

#: (pattern id, compiled pattern, what a hit would mean)
PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{24,}"),
     "OpenAI-style API key"),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9\-_]{24,}"),
     "Anthropic API key"),
    ("openrouter_key", re.compile(r"sk-or-v1-[A-Za-z0-9]{24,}"),
     "OpenRouter API key"),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
     "GitHub personal access token"),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}"),
     "AWS access key id"),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
     "Google API key"),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{20,}"),
     "Slack token"),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
     "PEM private key"),
    ("npm_token", re.compile(r"npm_[A-Za-z0-9]{36}"),
     "npm publish token"),
    # The value is captured separately from the whole match so an allowlist
    # entry names the literal credential-shaped string, not the whole assignment
    # (quotes and `token =` included), which would be unreadable and brittle.
    ("assigned_secret", re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*"
        r"[\"'](?P<value>[A-Za-z0-9\-_/+]{20,})[\"']"),
     "hardcoded credential assignment"),
)

#: Developer home directories. The scan is for a *published* tree, so a real
#: username in a path is both a privacy leak and a non-reproducible instruction.
HOME_PATH = re.compile(r"[A-Za-z]:\\Users\\([^\\\s\"']+)")

#: Paths that legitimately contain a home directory reference.
ALLOWED_HOME_PATHS = frozenset({
    # Windows' own shell variables, not a person.
    "app/paths.py",
    "docs/user-data-and-backup.md",
    "docs/troubleshooting.md",
    "packaging/omos.spec",
    "scripts/package/build_windows.ps1",
    "scripts/setup-dev.ps1",
    "scripts/run-dev.ps1",
    "README.md",
})

#: (file, literal) pairs that are deliberate test fixtures, not credentials.
#:
#: Two kinds live here, and the distinction matters:
#:
#: 1. **Leak-detection sentinels.** Tests that assert a secret is never echoed
#:    back must put a secret-shaped value in the repository. These are
#:    format-realistic on purpose -- a sentinel that does not look like a secret
#:    would not exercise the redaction path under test -- and they are literal
#:    nonsense with no account and no access.
#: 2. **Named fixture tokens.** Ordinary doubles such as
#:    ``vault-revocation-fixture``. The ``assigned_secret`` pattern matches any
#:    long string bound to a variable called ``token`` or ``secret``, which in a
#:    test suite is almost always a fixture rather than a credential. Each of
#:    these names itself a fixture.
#:
#: Every entry is a specific literal in a specific file, so deleting the test
#: removes the exemption automatically rather than leaving a permanent hole. A
#: real credential elsewhere still fails the gate.
ALLOWED_SENTINELS: tuple[tuple[str, str, str], ...] = (
    # --- leak-detection sentinels -------------------------------------------
    ("app/tests/test_dev007_tool_registry.py",
     "sk-EXAMPLE-NOT-A-REAL-KEY-abcdefghijklmnop",
     "test asserts the registry never echoes a tool argument"),
    ("app/tests/test_dev007_tool_registry.py",
     "ghp_EXAMPLE0000000000000000000000NOTREAL",
     "test asserts the registry never echoes a tool argument"),
    ("app/tests/test_dev007_tool_registry.py",
     "sk-EXAMPLE-PROVIDERLEAK-NOT-REAL",
     "test asserts a provider string is not returned to the caller"),
    ("app/tests/test_dev007_integrations_ui.py",
     "sk-EXAMPLE-SENTINEL-NOT-A-REAL-KEY-w8",
     "test asserts the settings API never echoes a stored credential"),
    # --- named fixture tokens -----------------------------------------------
    ("app/tests/test_dev007_integrations_ui.py",
     "w8-super-secret-token-987654321",
     "fixture double; asserts it is never rendered in the UI"),
    ("app/tests/test_dev007rfinal_config.py",
     "vault-revocation-fixture",
     "named vault fixture"),
    ("app/tests/test_dev007rfinal_config.py",
     "vault-apify-provider",
     "named vault fixture"),
    ("app/tests/test_dev007rfinal_config.py",
     "vault-openrouter-stream",
     "named vault fixture"),
    ("app/tests/test_dev007rfinal_s2_remediation.py",
     "s2-meta-access-token",
     "named vault fixture"),
    ("app/tests/test_dev007rfinal_s2_remediation.py",
     "s2-meta-code-access-token",
     "named vault fixture"),
    ("app/tests/test_v021_instagram.py",
     "fixture-apify-vault-token",
     "named vault fixture"),
    ("app/tests/test_dev008so_event_catalog.py",
     "-----BEGIN RSA PRIVATE KEY-----",
     "test asserts PEM private-key-shaped event text is redacted"),
)

#: Home-directory users that are obviously not a person.
ALLOWED_HOME_USERS = frozenset({
    "User", "Public", "Default", "AllUsers", "omos-test-user",
})

#: This scanner's own path within the exported tree.
SELF_PATH = "scripts/package/secret_scan.py"


def _sentinel_exemptions(rel: str) -> set[str]:
    """Literals exempt in this file.

    ``secret_scan.py`` declares the sentinels, so scanning itself would match its
    own allowlist and fail on every run -- a scanner that cannot scan itself is
    a scanner nobody runs. The exemption is exactly the declared literals, so it
    cannot mask anything else in the file.
    """
    if rel == SELF_PATH:
        return {lit for _path, lit, _why in ALLOWED_SENTINELS}
    return {lit for path, lit, _why in ALLOWED_SENTINELS if path == rel}


def scan(root: Path) -> dict:
    findings: list[dict] = []
    scanned = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        if SKIP_DIRS & set(rel_parts):
            continue
        # A withheld top-level tree only counts once (nested paths repeat it).
        if PRIVATE_DIRS & set(rel_parts[:1]):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        rel = path.relative_to(root).as_posix().replace("\\", "/")
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scanned += 1
        exempt = _sentinel_exemptions(rel)

        for pid, pattern, meaning in PATTERNS:
            for match in pattern.finditer(text):
                # Prefer a named `value` group when the pattern provides one.
                candidate = (match.groupdict().get("value")
                             or match.group(0))
                if candidate in exempt:
                    continue
                line = text.count("\n", 0, match.start()) + 1
                findings.append({
                    "kind": "secret-pattern",
                    "id": pid,
                    "meaning": meaning,
                    "file": rel,
                    "line": line,
                    "preview": _redact(candidate),
                })

        if rel not in ALLOWED_HOME_PATHS:
            for match in HOME_PATH.finditer(text):
                user = match.group(1)
                if user in ALLOWED_HOME_USERS:
                    continue
                line = text.count("\n", 0, match.start()) + 1
                findings.append({
                    "kind": "developer-home-path",
                    "id": "home_path",
                    "meaning": "developer home directory in a published file",
                    "file": rel,
                    "line": line,
                    "preview": _redact(match.group(0)),
                })

    return {
        "root": str(root),
        "files_scanned": scanned,
        "findings": findings,
        "real_secret_hits": sum(
            1 for f in findings if f["kind"] == "secret-pattern"),
        "home_path_hits": sum(
            1 for f in findings if f["kind"] == "developer-home-path"),
        "allowlisted_sentinels": len(ALLOWED_SENTINELS),
    }


def _redact(value: str) -> str:
    """Never echo a full candidate secret into a report or a log."""
    if len(value) <= 8:
        return value[:2] + "***"
    return f"{value[:4]}...{value[-2:]} (len {len(value)})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True,
                        help="directory to scan (the exported public tree)")
    parser.add_argument("--json", help="also write the full report here")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    report = scan(root)

    if args.json:
        Path(args.json).write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(f"scanned {report['files_scanned']} text files under {root}")
    print(f"allowlisted sentinels: {report['allowlisted_sentinels']}")
    print(f"real secret hits     : {report['real_secret_hits']}")
    print(f"home-path hits       : {report['home_path_hits']}")

    if not report["findings"]:
        print("\nSECRET_SCAN: PASS")
        return 0

    print("\nfindings:")
    for finding in report["findings"]:
        print(f"  [{finding['kind']}/{finding['id']}] {finding['file']}:"
              f"{finding['line']}  {finding['preview']}")
        print(f"      {finding['meaning']}")
    print("\nSECRET_SCAN: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
