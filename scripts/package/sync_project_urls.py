"""Regenerate URL-bearing files from `app/project_urls.py` (DEV-008).

Run after changing `PUBLIC_REPOSITORY_URL`:

    python scripts/package/sync_project_urls.py
    python scripts/package/sync_project_urls.py --check

`--check` exits non-zero if any generated file is stale, so CI can prove the
URLs are in sync without needing to know the URL itself.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app import project_urls  # noqa: E402

GENERATED_FILES = {
    REPO_ROOT / "packaging" / "project_url.iss": project_urls.render_iss_defines,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="verify generated files are current; write nothing")
    args = parser.parse_args(argv)

    stale: list[Path] = []
    for path, render in GENERATED_FILES.items():
        desired = render()
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current == desired:
            continue
        if args.check:
            stale.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(desired, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(REPO_ROOT)}")

    if stale:
        print("Stale generated files (run without --check to update):", file=sys.stderr)
        for path in stale:
            print(f"  - {path.relative_to(REPO_ROOT)}", file=sys.stderr)
        return 1

    if project_urls.is_placeholder():
        print("NOTE: PUBLIC_REPOSITORY_URL still points at the private development")
        print("      repository. Update app/project_urls.py once the public repo exists.")
    else:
        print("public repository:", project_urls.PUBLIC_REPOSITORY_URL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
