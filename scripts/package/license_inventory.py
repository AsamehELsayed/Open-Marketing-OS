"""Generate the third-party licence inventory for NOTICE (DEV-008).

QA finding F4: the hand-written third-party section in `NOTICE` and the table in
`docs/opensource/license-audit.md` did not match what actually ships. They named
packages that are not in the installer and omitted packages that are — including
`sqlite-vec`, a *native* extension that ships as a compiled binary.

This script replaces prose with a reproducible measurement:

1. Read `requirements-lock.txt` — the exact set that ships.
2. For each distribution, read the installed `*.dist-info/METADATA` for
   `License`, `License-Expression` and `Classifier: License ::` values.
3. Group by SPDX id and emit both a Markdown table and a JSON sidecar.

Run:
    python scripts/package/license_inventory.py
    python scripts/package/license_inventory.py --json dist/license-inventory.json

If a distribution's licence cannot be determined, it is reported under
`UNKNOWN` and the script exits non-zero. An unattributed package in a public
distribution is a finding, not a rounding error, so it must be resolved rather
than filed under "probably permissive".
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Strong copyleft. Any of these in the shipped set is a release blocker: they
#: would change the licensing posture of the whole product. This set is what the
#: exit-2 gate uses.
STRONG_COPYLEFT = {
    "GPL-2.0", "GPL-3.0", "AGPL-3.0", "LGPL-2.1", "LGPL-3.0",
    "AGPL-3.0-only", "AGPL-3.0-or-later", "GPL-2.0-only", "GPL-3.0-only",
    "GPL-2.0-or-later", "GPL-3.0-or-later", "LGPL-2.1-only", "LGPL-3.0-only",
}

#: Weak, file-level copyleft. NOT a blocker, and deliberately kept out of the
#: gate above. MPL-2.0 permits distribution inside a larger work under a
#: different licence and requires only that the MPL-covered files remain
#: available under MPL-2.0. This project legitimately ships two MPL-2.0 packages
#: (`certifi`, `orjson`).
#:
#: DEV-008 review finding N7: an earlier draft had ONE set named `COPLEFT`
#: containing MPL-2.0, documented as a hard blocker but never referenced. An
#: editor "correcting" the gate to use it would have permanently broken the
#: release build. The two concerns are now separate sets, each with one purpose,
#: and the weak set is reported for disclosure.
WEAK_COPYLEFT = {
    "MPL-2.0", "MPL-1.0", "MPL-2.0-no-copyleft-exception",
    "EPL-1.0", "EPL-2.0", "CDDL-1.0", "EUPL-1.1", "EUPL-1.2",
}

_CLASSIFIER = re.compile(r"^Classifier:\s*License\s*::\s*(.+?)\s*$", re.MULTILINE)

#: Some projects (Jinja2 among them) put the entire licence *text* in the
#: `License:` field instead of an SPDX id, with no classifier. Rather than guess,
#: these are matched by their well-known licence text so the result is a
#: measurement with a stated basis rather than an assumption.
_TEXT_SIGNATURES = (
    (re.compile(r"redistributions? of source code must retain", re.IGNORECASE),
     "BSD-3-Clause"),
    (re.compile(r"Permission is hereby granted, free of charge", re.IGNORECASE),
     "MIT"),
    (re.compile(r"Apache License\s+Version 2\.0", re.IGNORECASE),
     "Apache-2.0"),
    (re.compile(r"Mozilla Public License Version 2\.0", re.IGNORECASE),
     "MPL-2.0"),
)

#: Collapse the many ways packages spell the same licence into one SPDX-ish id,
#: so the NOTICE groups them instead of listing near-duplicate rows. The raw
#: value is kept in each record for audit.
_NORMALISE = {
    "Apache Software": "Apache-2.0",
    "Apache Software License": "Apache-2.0",
    "Apache License, Version 2.0": "Apache-2.0",
    "Apache License 2.0": "Apache-2.0",
    "Apache License, Version 2.0 (http://www.apache.org/licenses/LICENSE-2.0)": "Apache-2.0",
    "MIT License, Apache License, Version 2.0": "MIT OR Apache-2.0",
    "BSD": "BSD-3-Clause",
    "BSD License": "BSD-3-Clause",
    "BSD-2": "BSD-2-Clause",
    "3-Clause BSD License": "BSD-3-Clause",
    "2-Clause BSD License": "BSD-2-Clause",
    "PSF": "PSF-2.0",
    "PSF License": "PSF-2.0",
    "ISC License (ISCL)": "ISC",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
}


def read_lock(path: Path) -> list[tuple[str, str]]:
    """Return [(name, version)] from a pip freeze style lock file."""
    out: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-")):
            continue
        name, _, version = line.partition("==")
        if name:
            out.append((name.strip(), version.strip()))
    return out


def classify(name: str) -> dict:
    """Resolve a distribution's declared licence from its installed metadata."""
    import importlib.metadata as md

    try:
        meta = md.metadata(name)
    except md.PackageNotFoundError:
        return {"license": None, "source": "not-installed", "classifiers": []}

    expression = (meta.get("License-Expression") or "").strip()
    declared = (meta.get("License") or "").strip()
    # `get_all("Classifier")` yields the *values* (e.g.
    # "License :: OSI Approved :: MIT License"), not "Classifier: <value>" lines,
    # so they are matched directly rather than through _CLASSIFIER.
    classifiers = [
        c for c in (meta.get_all("Classifier") or [])
        if c.strip().startswith("License ::")
    ]

    spdx = expression or None
    source = "License-Expression" if expression else ""
    if not spdx:
        for candidate in classifiers:
            cleaned = candidate.strip()
            if not cleaned.endswith(" License"):
                continue
            # Classifier values are `License :: OSI Approved :: BSD License`.
            # The SPDX id is the *last* segment, not the whole remainder.
            spdx = cleaned[: -len(" License")].split("::")[-1].strip()
            source = source or "Classifier"
            break
    if not spdx and declared and len(declared) <= 40 and "\n" not in declared:
        spdx = declared
        source = source or "License"

    # Last resort: identify the licence from its own text. Recorded as
    # `License-text` so the basis is auditable, and still reported as resolved
    # because the signature match is deterministic.
    if not spdx and declared:
        for pattern, lic in _TEXT_SIGNATURES:
            if pattern.search(declared):
                spdx = lic
                source = "License-text-signature"
                break

    raw = spdx
    if raw:
        spdx = _NORMALISE.get(raw.strip(), raw.strip())

    return {
        "license": spdx or None,
        "license_raw": raw,
        "source": source or "unresolved",
        "classifiers": [c.strip() for c in classifiers],
    }


def build(lock: Path) -> dict:
    packages = read_lock(lock)
    records: list[dict] = []
    unknown: list[str] = []
    strong: list[tuple[str, str]] = []
    weak: list[tuple[str, str]] = []

    for name, version in sorted(packages, key=lambda x: x[0].lower()):
        info = classify(name)
        spdx = info["license"]
        record = {
            "name": name,
            "version": version,
            "license": spdx,
            "source": info["source"],
        }
        records.append(record)
        if not spdx:
            unknown.append(f"{name}=={version}")
        elif any(c in spdx for c in STRONG_COPYLEFT):
            strong.append((name, spdx))
        elif any(c in spdx for c in WEAK_COPYLEFT):
            weak.append((name, spdx))

    grouped: dict[str, list[str]] = {}
    for record in records:
        key = record["license"] or "UNKNOWN"
        grouped.setdefault(key, []).append(f"{record['name']}=={record['version']}")

    return {
        "lock_file": str(lock.relative_to(REPO_ROOT)),
        "package_count": len(records),
        "packages": records,
        "by_license": {k: sorted(v) for k, v in sorted(grouped.items())},
        "unresolved": sorted(unknown),
        "strong_copyleft": sorted(strong),
        "weak_copyleft": sorted(weak),
    }


def to_markdown(inventory: dict) -> str:
    lines = [
        "| License | Packages (name==version) |",
        "|---|---|",
    ]
    for lic, names in inventory["by_license"].items():
        lines.append(f"| **{lic}** | " + ", ".join(f"`{n}`" for n in names) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", default="requirements-lock.txt")
    parser.add_argument("--json", default="", help="write the JSON sidecar here")
    parser.add_argument("--markdown", default="", help="write the Markdown table here")
    args = parser.parse_args(argv)

    lock = REPO_ROOT / args.lock
    if not lock.is_file():
        print(f"Lock file not found: {lock}", file=sys.stderr)
        return 1

    inventory = build(lock)
    print(f"{inventory['package_count']} packages from {inventory['lock_file']}")
    for lic, names in inventory["by_license"].items():
        print(f"  {lic:24s} {len(names)}")

    if args.json:
        out = REPO_ROOT / args.json
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.json}")

    if args.markdown:
        out = REPO_ROOT / args.markdown
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(to_markdown(inventory) + "\n", encoding="utf-8")
        print(f"wrote {args.markdown}")

    if inventory["weak_copyleft"]:
        print(
            "\nNote: weak (file-level) copyleft present — compatible with "
            "Apache-2.0 redistribution, but disclosed in NOTICE:"
        )
        for name, lic in inventory["weak_copyleft"]:
            print(f"  - {name}: {lic}")

    if inventory["strong_copyleft"]:
        print("\nBLOCKER: strong copyleft in the shipped dependency set:",
              file=sys.stderr)
        for name, lic in inventory["strong_copyleft"]:
            print(f"  - {name}: {lic}", file=sys.stderr)
        return 2

    if inventory["unresolved"]:
        print("\nUnresolved licence for:", file=sys.stderr)
        for item in inventory["unresolved"]:
            print(f"  - {item}", file=sys.stderr)
        print("Resolve these before publication; do not assume permissive.",
              file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
