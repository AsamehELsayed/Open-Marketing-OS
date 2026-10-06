from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "package" / "stage_license_payload.py"
SPEC = importlib.util.spec_from_file_location("stage_license_payload", MODULE_PATH)
assert SPEC and SPEC.loader
LICENSE_STAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LICENSE_STAGE)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_stage_copies_project_python_frontend_and_vendored_notices(tmp_path: Path):
    repo = tmp_path / "repo"
    payload = tmp_path / "payload"
    site_packages = tmp_path / "site-packages"
    for name in LICENSE_STAGE.ROOT_NOTICE_FILES:
        _write(repo / name, f"recipient notice {name}\n")
    _write(repo / "LICENSES" / "htmx-0BSD.txt", "htmx 0BSD license\n")
    _write(repo / ".agents" / "LICENSE", "vendored skills MIT\n")
    _write(repo / "requirements-lock.txt", "sample-pkg==1.2.3\n")
    for fallback_name in {name for files in LICENSE_STAGE.FALLBACKS.values() for name in files}:
        fallback_source = ROOT / "scripts" / "package" / "notices" / "python" / fallback_name
        _write(repo / "scripts" / "package" / "notices" / "python" / fallback_name, fallback_source.read_text(encoding="utf-8"))
    _write(
        site_packages / "sample_pkg-1.2.3.dist-info" / "METADATA",
        "Metadata-Version: 2.1\nName: sample-pkg\nVersion: 1.2.3\n",
    )
    _write(site_packages / "sample_pkg-1.2.3.dist-info" / "licenses" / "LICENSE.txt", "python MIT license\n")
    _write(
        repo / "frontend" / "package-lock.json",
        json.dumps({
            "packages": {
                "": {"version": "0.1.0"},
                "node_modules/react": {"version": "18.3.1", "license": "MIT"},
                "node_modules/dev-only": {"version": "1.0.0", "license": "MIT", "dev": True},
            }
        }),
    )
    _write(repo / "frontend" / "node_modules" / "react" / "LICENSE", "frontend MIT license\n")
    _write(repo / "frontend" / "node_modules" / "dev-only" / "LICENSE", "dev-only license\n")

    manifest = LICENSE_STAGE.stage(repo, payload, site_packages)

    assert (payload / "THIRD-PARTY-NOTICES").is_file()
    assert (payload / "LICENSE-INVENTORY.json").is_file()
    assert (payload / "LICENSES" / "htmx-0BSD.txt").is_file()
    assert (payload / "THIRD-PARTY-LICENSES" / "marketing-skills" / "LICENSE").is_file()
    assert (payload / manifest["python_packages"][0]["license_files"][0]).is_file()
    assert (payload / manifest["frontend_packages"][0]["license_files"][0]).is_file()
    assert manifest["python_package_count"] == 1
    assert manifest["frontend_package_count"] == 1
    assert "dev-only" not in json.dumps(manifest)


def test_installer_includes_staged_notices_and_runtime_notices_only_for_local():
    iss = (ROOT / "packaging" / "omos.iss").read_text(encoding="utf-8")
    builder = (ROOT / "scripts" / "package" / "build_windows.ps1").read_text(encoding="utf-8")
    assert 'Source: "{#PayloadDir}\\*"; DestDir: "{app}"' in iss
    assert "Source: \"{#PayloadDir}\\_internal\\runtime\\*\"" in iss
    runtime_line = 'Source: "{#PayloadDir}\\_internal\\runtime\\*"; DestDir: "{localappdata}\\OpenMarketingOS\\runtime"'
    runtime_index = iss.index(runtime_line)
    local_guard = iss.rindex('#if EditionProfile == "local"', 0, runtime_index)
    assert local_guard < runtime_index < iss.index("#endif", runtime_index)
    assert "stage_license_payload.py" in builder
    assert "--site-packages $sitePackages" in builder
