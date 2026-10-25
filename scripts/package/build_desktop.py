"""Native candidate sidecar builder. Never publishes or downloads model weights."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]

def validate_manifest(manifest, *, target_platform=None):
    from app.paths import APP_VERSION
    target_platform = target_platform or sys.platform
    if (manifest.get("schema_version") != 1 or manifest.get("version") != APP_VERSION
            or manifest.get("edition") not in {"local", "openrouter"}
            or manifest.get("platform") != target_platform or manifest.get("arch") != "x64"
            or not isinstance(manifest.get("build_id"), str) or not manifest["build_id"]):
        raise ValueError("Invalid native build manifest")
    return manifest

def scan_payload(root, *, allow_internal_symlinks=False):
    forbidden = {".env", "marketing.db", "credentials.json", "product-marketing.md"}
    root = Path(root).resolve()
    for path in root.rglob("*"):
        if path.is_symlink():
            if not allow_internal_symlinks:
                raise ValueError("Payload symlinks are forbidden")
            try:
                target = path.resolve(strict=True)
                target.relative_to(root)
            except (OSError, ValueError) as exc:
                raise ValueError("Payload symlink must resolve inside the source bundle") from exc
            if not target.is_file() and not target.is_dir():
                raise ValueError("Payload symlink target is not a file or directory")
        if path.is_file() and (path.name in forbidden or path.suffix.lower() in {".gguf", ".sqlite", ".sqlite3"}):
            raise ValueError("Private state or model weights in payload")

def stage_payload(source, destination, manifest_path):
    manifest = validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
    executable = "omos-sidecar.exe" if sys.platform == "win32" else "omos-sidecar"
    if not (source / executable).is_file() or not (source / "_internal").is_dir():
        raise ValueError("Native one-directory sidecar missing")
    if destination.exists():
        raise ValueError("Stage destination already exists; select a fresh stage")
    scan_payload(source, allow_internal_symlinks=True)
    # PyInstaller may use relative links for shared native libraries. Validate
    # containment above, then dereference them so the installed payload has no
    # symlinks and remains portable across installer/filesystem boundaries.
    shutil.copytree(source, destination, symlinks=False)
    internal = destination / "_internal"
    shutil.copyfile(manifest_path, internal / "build-manifest.json")
    shutil.copyfile(ROOT / "packaging" / "profiles" / (manifest["edition"] + ".json"), internal / "edition-profile.json")
    scan_payload(destination)
    return manifest

def bundle_linux_openmp_runtime(runtime_root, library_path=None, notice_path=None):
    """Bundle libgomp needed by the pinned Linux llama.cpp CPU runtime."""
    runtime_root = Path(runtime_root)
    candidates = ([Path(library_path)] if library_path else [
        Path("/usr/lib/x86_64-linux-gnu/libgomp.so.1"),
        Path("/lib/x86_64-linux-gnu/libgomp.so.1"),
    ])
    source = next((item.resolve() for item in candidates if item.is_file()), None)
    notice = Path(notice_path) if notice_path else Path("/usr/share/doc/libgomp1/copyright")
    if source is None or not source.is_file():
        raise FileNotFoundError("Linux runtime requires the system libgomp.so.1")
    if not notice.is_file():
        raise FileNotFoundError("Linux libgomp copyright notice is missing")
    runtime_root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, runtime_root / "libgomp.so.1")
    (runtime_root / "libgomp.so.1").chmod(0o755)
    notice_target = runtime_root / "licenses" / "libgomp" / "copyright"
    notice_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(notice, notice_target)
    return runtime_root / "libgomp.so.1"

def artifact_hashes(directory, manifest):
    rows = []
    for path in sorted(directory.iterdir()):
        if path.is_file() and path.suffix.lower() in {".exe", ".appimage", ".deb"}:
            digest = hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
            rows.append({"file": path.name, "size_bytes": path.stat().st_size, "sha256": digest})
    if not rows:
        raise ValueError("No native installer candidates found")
    (directory / "candidate-manifest.json").write_text(json.dumps({"build": manifest, "published": False, "artifacts": rows}, indent=2)+"\n", encoding="utf-8")
    (directory / "SHA256SUMS").write_text("".join(row["sha256"]+"  "+row["file"]+"\n" for row in rows), encoding="utf-8")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hash-only", action="store_true")
    parser.add_argument("--stage-only", action="store_true")
    parser.add_argument("--runtime-archive", type=Path)
    parser.add_argument("--runtime-notices", type=Path)
    parser.add_argument("--output-dir", type=Path, help="Candidate output directory (default: dist/electron)")
    parser.add_argument("--manifest", type=Path, help="Build manifest to validate/hash")
    parser.add_argument("--platform", choices=("win32", "linux"), help="Target platform for explicit cross-platform hash-only runs")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    manifest_path = args.manifest or ROOT / "desktop" / "build-manifest.json"
    manifest = validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")), target_platform=args.platform)
    if args.hash_only:
        artifact_hashes(args.output_dir or ROOT / "dist" / "electron", manifest)
        return
    if not args.runtime_archive:
        raise ValueError("Explicit --runtime-archive required; no automatic downloads")
    env = dict(os.environ, OMOS_DESKTOP_MANIFEST=str(manifest_path))
    if not args.stage_only:
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(ROOT / "build" / "desktop" / "frozen"), "--workpath", str(ROOT / "build" / "desktop" / "pyinstaller"), str(ROOT / "packaging" / "sidecar.spec")], check=True, cwd=ROOT, env=env)
    destination = ROOT / "build" / "desktop" / "backend"
    stage_payload(ROOT / "build" / "desktop" / "frozen" / "omos-sidecar", destination, manifest_path)
    from app.services.llm.local_model_catalog import default_catalog
    from app.services.llm.local_model_download import install_runtime_archive
    install_runtime_archive(args.runtime_archive, destination / "_internal" / "runtime", default_catalog().runtime)
    if sys.platform == "linux":
        bundle_linux_openmp_runtime(destination / "_internal" / "runtime")
    notices = destination / "_internal" / "runtime" / "licenses" / "llama.cpp"
    notices.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts" / "package" / "notices" / "llama.cpp-LICENSE", notices / "LICENSE")
    runtime_root = destination / "_internal" / "runtime"
    for source in runtime_root.glob("LICENSE*"):
        if source.is_file():
            shutil.copyfile(source, notices / source.name)
    if sys.platform == "win32" and not (notices / "LICENSE-LLVM-OpenMP").is_file():
        raise ValueError("Pinned Windows runtime requires LLVM OpenMP notice")
    if args.runtime_notices:
        shutil.copytree(args.runtime_notices, notices, dirs_exist_ok=True)
    scan_payload(destination)

if __name__ == "__main__":
    main()
