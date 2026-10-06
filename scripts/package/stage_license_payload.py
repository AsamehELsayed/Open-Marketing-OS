"""Stage recipient-facing notices and exact package license files for installers."""
from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
import re
import shutil
import sys
from pathlib import Path

LICENSE_FILE = re.compile(
    r"^(?:(?:licen[cs]e|copying|notice|copyright)(?:[-_.].*)?|third[-_.]?party[-_.]?notices?(?:[-_.].*)?)$",
    re.I,
)
FALLBACKS = {
    "flatbuffers": ["Apache-2.0.txt", "flatbuffers-25.12.19-NOTICE.txt"],
    "langsmith": ["langsmith-0.14.1-LICENSE-MIT.txt"],
    "sqlitevec": ["sqlite-vec-0.1.9-LICENSE-MIT.txt", "sqlite-vec-0.1.9-LICENSE-APACHE.txt", "sqlite-vec-0.1.9-NOTICE.txt"],
    "tokenizers": ["Apache-2.0.txt", "tokenizers-0.22.2-NOTICE.txt"],
}
ROOT_NOTICE_FILES = (
    "LICENSE",
    "NOTICE",
    "THIRD-PARTY-NOTICES",
    "LICENSE-INVENTORY.json",
    "LICENSE-INVENTORY.md",
)


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "", name).lower()


def _copy(source: Path, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target.as_posix()


def stage(repo: Path, payload: Path, site_packages: Path) -> dict:
    """Copy project, locked Python, and emitted-frontend license materials."""
    repo, payload, site_packages = repo.resolve(), payload.resolve(), site_packages.resolve()
    for name in ROOT_NOTICE_FILES:
        source = repo / name
        if not source.is_file():
            raise FileNotFoundError(f"Required recipient notice is missing: {source}")
        _copy(source, payload / name)

    htmx = repo / "LICENSES" / "htmx-0BSD.txt"
    if not htmx.is_file():
        raise FileNotFoundError(f"Required htmx 0BSD text is missing: {htmx}")
    _copy(htmx, payload / "LICENSES" / htmx.name)
    skills_license = repo / ".agents" / "LICENSE"
    if not skills_license.is_file():
        raise FileNotFoundError(f"Required vendored-skills license is missing: {skills_license}")
    _copy(skills_license, payload / "THIRD-PARTY-LICENSES" / "marketing-skills" / "LICENSE")

    fallback_root = repo / "scripts" / "package" / "notices" / "python"
    fallback_files: dict[str, Path] = {}
    for name in {item for values in FALLBACKS.values() for item in values}:
        source = fallback_root / name
        if not source.is_file():
            raise FileNotFoundError(f"Required source license fallback is missing: {source}")
        fallback_files[name] = source

    distributions: dict[str, metadata.Distribution] = {}
    for dist in metadata.distributions(path=[str(site_packages)]):
        name = dist.metadata.get("Name")
        if name:
            distributions[canonical(name)] = dist

    python_records = []
    lock = repo / "requirements-lock.txt"
    for raw in lock.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-")) or "==" not in line:
            continue
        package, version = (part.strip() for part in line.split("==", 1))
        dist = distributions.get(canonical(package))
        if dist is None or dist.version != version:
            raise RuntimeError(f"Locked Python package is absent or mismatched: {package}=={version}")
        dest_base = payload / "THIRD-PARTY-LICENSES" / "python" / f"{canonical(package)}-{version}"
        files = []
        dist_path = Path(dist._path).resolve()
        copied_sources: set[Path] = set()
        for source in dist_path.rglob("*"):
            if not source.is_file() or not LICENSE_FILE.match(source.name):
                continue
            files.append(_copy(source, dest_base / source.relative_to(dist_path)))
            copied_sources.add(source.resolve())
        # Some wheels declare their license and third-party notices beside the
        # import package rather than inside dist-info (for example, onnxruntime).
        # Include the exact installed files listed by the wheel RECORD as well.
        for record_path in dist.files or ():
            if not LICENSE_FILE.match(Path(record_path).name):
                continue
            source = Path(dist.locate_file(record_path)).resolve()
            if not source.is_file() or source in copied_sources:
                continue
            try:
                source_relative = source.relative_to(site_packages)
            except ValueError as exc:
                raise RuntimeError(
                    f"Package license file is outside the isolated site-packages: {source}"
                ) from exc
            files.append(_copy(source, dest_base / "distribution" / source_relative))
            copied_sources.add(source)
        fallback_names = FALLBACKS.get(canonical(package), [])
        if not files and not fallback_names:
            raise RuntimeError(f"No exact license text found for locked package {package}=={version}")
        for fallback_name in fallback_names:
            files.append(_copy(fallback_files[fallback_name], dest_base / fallback_name))
        python_records.append({"name": package, "version": version, "license_files": files})

    frontend_lock = json.loads((repo / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    frontend_records = []
    for locator, package_info in sorted(frontend_lock.get("packages", {}).items()):
        if not locator or package_info.get("dev"):
            continue
        source_dir = repo / "frontend" / locator
        license_sources = [
            p for p in source_dir.rglob("*")
            if p.is_file() and LICENSE_FILE.match(p.name)
            and (p.parent == source_dir or "license" in p.parent.name.lower())
        ]
        if not license_sources:
            raise RuntimeError(f"No exact license text found for emitted frontend package {locator}")
        package_dest = payload / "THIRD-PARTY-LICENSES" / "frontend" / locator.removeprefix("node_modules/")
        files = [_copy(p, package_dest / p.relative_to(source_dir)) for p in license_sources]
        frontend_records.append({"package": locator.removeprefix("node_modules/"), "version": package_info.get("version"), "license": package_info.get("license"), "license_files": files})

    manifest = {
        "schema_version": 1,
        "python_package_count": len(python_records),
        "python_packages": python_records,
        "frontend_package_count": len(frontend_records),
        "frontend_packages": frontend_records,
        "htmx_license": "LICENSES/htmx-0BSD.txt",
        "vendored_skills_license": "THIRD-PARTY-LICENSES/marketing-skills/LICENSE",
        "local_runtime_licenses": "_internal/runtime/licenses/llama.cpp (installed only by Local profile)",
    }
    manifest_path = payload / "THIRD-PARTY-LICENSES" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--payload-dir", type=Path, required=True)
    parser.add_argument("--site-packages", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = stage(args.repo_root, args.payload_dir, args.site_packages)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"License staging failed: {exc}", file=sys.stderr)
        return 1
    print(f"Staged licenses for {manifest['python_package_count']} Python and {manifest['frontend_package_count']} frontend packages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
