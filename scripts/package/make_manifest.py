"""Release manifest + SHA256 checksums (DEV-008).

Writes, into the directory given by ``--out-dir`` (default ``dist/``):

- ``release-manifest.json`` — public build metadata: version, commit, build
  date, platform, per-artifact checksums, minimum Windows version, and the
  status of the editions in this release.
- ``SHA256SUMS.txt`` — the standard ``<sha256>  <filename>`` format that
  ``sha256sum -c`` and ``Get-FileHash`` users expect.

What is deliberately *not* in the manifest: secrets, absolute developer paths,
internal run identifiers, founder gates, or anything else that says more about
the build machine than the product. A release manifest is a public document and
is treated as one.

Usage:
    python scripts/package/make_manifest.py --out-dir dist
    python scripts/package/make_manifest.py --out-dir dist --no-verify-present
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

MIN_WINDOWS = "10.0"
PLATFORM = "windows"
ARCHITECTURE = "x64"

#: Human-facing names of the artifacts a user downloads.
ARTIFACTS = {
    "installer": "OpenMarketingOS-Quick-Setup.exe",
    "portable": "OpenMarketingOS-Portable.zip",
}

#: A 1 MiB read size keeps hashing a ~150 MB installer fast without loading it
#: into memory.
_CHUNK = 1024 * 1024


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True, text=True, errors="replace", timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def app_version() -> str:
    """Read the single authoritative version constant.

    Imported rather than re-parsed so the manifest, `/health` and the installer
    can never disagree about what this build is.

    Fails closed. A release manifest is a public provenance document; writing
    one that says `0.0.0-unknown` because an import failed would be worse than
    writing none, because it looks authoritative while being wrong.
    """
    try:
        from app import paths
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            f"Refusing to write a release manifest: could not import app.paths "
            f"({type(exc).__name__}: {exc}). Run this from the repository root."
        )
    version = (paths.APP_VERSION or "").strip()
    if not re.match(r"^\d+\.\d+\.\d+", version):
        raise SystemExit(
            f"Refusing to write a release manifest: app.paths.APP_VERSION is "
            f"{version!r}, which is not a usable version."
        )
    return version


def build_metadata() -> dict[str, str]:
    commit = git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain"))
    tag = git("describe", "--tags", "--exact-match") or git(
        "describe", "--tags", "--always")
    return {
        "commit": commit,
        "commit_short": commit[:12],
        "ref": tag,
        "dirty_tree": dirty,
    }


def scan_for_forbidden_content(
    payload: Path, *, reject_model_weights: bool = False
) -> list[str]:
    """Refuse to publish a payload containing developer leftovers.

    Cheap, and it turns "we were careful" into an actual gate.
    """
    problems: list[str] = []
    if not payload.is_dir():
        return problems
    for path in payload.rglob("*"):
        name = path.name.lower()
        if name == ".env" or name.startswith(".env."):
            problems.append(f"stray environment file: {path.relative_to(payload)}")
        if path.suffix.lower() in {".pyc"} and "__pycache__" in path.parts:
            continue
        if name in {".git", "pytest.ini", "conftest.py"}:
            problems.append(f"developer-only file in payload: {path.relative_to(payload)}")
    for marker in ("keys", "secrets", "credentials"):
        if (payload / marker).exists():
            problems.append(f"unexpected '{marker}' directory inside the install payload")
    if reject_model_weights and any(
        path.is_file() and path.suffix.lower() == ".gguf"
        for path in payload.rglob("*")
    ):
        problems.append("model weights (.gguf) must never be bundled in the installer")
    return problems


def dev029_local_profile(payload: Path) -> dict:
    """Return verified runtime identity and file hashes for a DEV-029 payload."""
    catalog_path = REPO_ROOT / "config" / "local_models.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    runtime = catalog["runtime"]
    runtime_dir = payload / "_internal" / "runtime"
    server = runtime_dir / runtime["executable"]
    if not server.is_file():
        raise SystemExit("Refusing DEV-029 manifest: packaged llama-server.exe is missing.")
    notice_dir = runtime_dir / "licenses" / "llama.cpp"
    notices = sorted(p for p in notice_dir.rglob("*") if p.is_file()) if notice_dir.is_dir() else []
    notice_names = {path.name.upper() for path in notices}
    if not {"LICENSE", "LICENSE-LLVM-OPENMP"}.issubset(notice_names):
        raise SystemExit(
            "Refusing DEV-029 manifest: both llama.cpp MIT and LLVM OpenMP notices are required."
        )
    files = []
    for path in sorted(p for p in runtime_dir.rglob("*") if p.is_file()):
        if path.suffix.lower() == ".gguf":
            raise SystemExit("Refusing DEV-029 manifest: model weights are present in runtime payload.")
        files.append({
            "path": path.relative_to(payload).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_of(path),
        })
    return {
        "name": runtime["name"],
        "version": runtime["version"],
        "source": runtime["source"],
        "revision": runtime["revision"],
        "archive_asset": runtime["asset_name"],
        "archive_sha256": runtime["sha256"],
        "license": runtime["license"],
        "license_url": runtime["license_url"],
        "executable": runtime["executable"],
        "files": files,
    }


def network_behavior(*, local_available: bool = False) -> dict[str, object]:
    """Describe the inference paths actually shipped in the manifest."""
    if local_available:
        inference = (
            "Can run on this machine through the verified Local llama.cpp model, "
            "or through configured OpenRouter/OpenAI cloud providers, depending "
            "on the user's routing settings."
        )
    else:
        inference = (
            "Performed by the user's configured cloud provider "
            "(OpenRouter or OpenAI), not on this machine."
        )
    return {
        "binds": "127.0.0.1",
        "exposes_to_lan": False,
        "ai_inference": inference,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="dist", help="where the artifacts live")
    parser.add_argument("--payload", default="build/payload",
                        help="frozen payload directory, checked for leftovers")
    parser.add_argument("--no-verify-present", action="store_true",
                        help="write the manifest even if the installer is missing "
                             "(for dry runs; a real release must not use this)")
    parser.add_argument("--dev-run-id", choices=("DEV-029",),
                        help="write a clearly pre-production DEV-029 Local manifest")
    args = parser.parse_args(argv)

    out_dir = (REPO_ROOT / args.out_dir).resolve() if not Path(args.out_dir).is_absolute() \
        else Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    forbidden = scan_for_forbidden_content(
        REPO_ROOT / args.payload,
        reject_model_weights=bool(args.dev_run_id),
    )
    if forbidden:
        print("Refusing to write a manifest. Payload problems:", file=sys.stderr)
        for item in forbidden:
            print(f"  - {item}", file=sys.stderr)
        return 1

    artifacts: dict[str, dict] = {}
    checksums: list[tuple[str, str]] = []
    missing: list[str] = []

    for key, filename in ARTIFACTS.items():
        path = out_dir / filename
        if not path.is_file():
            missing.append(filename)
            continue
        digest = sha256_of(path)
        artifacts[key] = {
            "filename": filename,
            "size_bytes": path.stat().st_size,
            "sha256": digest,
        }
        checksums.append((digest, filename))

    required_artifacts = [ARTIFACTS["installer"]] if args.dev_run_id else list(ARTIFACTS.values())
    missing_required = [name for name in required_artifacts if not (out_dir / name).is_file()]
    if missing_required and not args.no_verify_present:
        print(
            "Refusing to write a release manifest. Missing artifacts: "
            + ", ".join(missing_required),
            file=sys.stderr,
        )
        print("Build them first, or pass --no-verify-present for a dry run.",
              file=sys.stderr)
        return 1

    version = app_version()
    meta = build_metadata()
    built_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")

    manifest = {
        "product": "Open Marketing OS",
        "version": version,
        "release_channel": "beta",
        "built_at": built_at,
        "build_commit": meta["commit"],
        "build_commit_short": meta["commit_short"],
        "build_ref": meta["ref"],
        "build_tree_dirty": meta["dirty_tree"],
        "platform": PLATFORM,
        "architecture": ARCHITECTURE,
        "minimum_windows_version": MIN_WINDOWS,
        "editions": {
            "quick": {
                "available": True,
                "requires_cloud_ai": True,
                "providers": ["openrouter", "openai"],
                "notes": (
                    "Bring your own provider API key. Stored in a Windows "
                    "DPAPI-encrypted credential vault on your machine."
                ),
            },
            "local": {
                "available": False,
                "notes": (
                    "Local on-device AI is not part of this beta. No model is "
                    "downloaded and nothing is bundled with the installer."
                ),
            },
        },
        "artifacts": artifacts,
        "portable_shipped": "portable" in artifacts,
        "code_signed": False,
        "code_signing_note": (
            "This beta is not code-signed. Windows SmartScreen may show an "
            "'Unknown publisher' warning. Verify downloads with SHA256SUMS.txt."
        ),
        "network_behavior": network_behavior(local_available=bool(args.dev_run_id)),
        "verification": {
            "checksums_file": "SHA256SUMS.txt",
            "checksum_algorithm": "sha256",
        },
    }

    if args.dev_run_id:
        runtime = dev029_local_profile(REPO_ROOT / args.payload)
        model = json.loads((REPO_ROOT / "config" / "local_models.json").read_text(encoding="utf-8"))["model"]
        built_stamp = built_at.replace("-", "").replace(":", "").replace("+00:00", "Z")
        source_state = hashlib.sha256(
            (meta["commit"] + "\n" + git("status", "--porcelain")).encode("utf-8")
        ).hexdigest()
        source_fingerprint = hashlib.sha256(
            (source_state + "\n" + "\n".join(
                f"{item['filename']}:{item['sha256']}" for item in artifacts.values()
            ) + "\n" + "\n".join(
                f"{item['path']}:{item['sha256']}" for item in runtime["files"]
            )).encode("utf-8")
        ).hexdigest()[:12]
        manifest["build_identity"] = f"{args.dev_run_id}-{built_stamp}-{source_fingerprint}"
        manifest["source_tree_fingerprint"] = source_state
        manifest["build_classification"] = "PRE-PRODUCTION"
        manifest["release_channel"] = "pre-production"
        manifest["editions"]["local"] = {
            "available": True,
            "model_bundled": False,
            "model_id": model["model_id"],
            "model_display_name": model["display_name"],
            "model_source": model["source"],
            "model_revision": model["revision"],
            "model_license": model["license"],
            "model_download_bytes": model["download_bytes"],
            "model_temporary_disk_bytes": model["temporary_disk_bytes"],
            "model_shards": [
                {"filename": shard["filename"], "size_bytes": shard["size_bytes"],
                 "sha256": shard["sha256"]} for shard in model["shards"]
            ],
            "runtime": runtime,
            "setup_ui_included": True,
        }
        manifest["licensing"] = {
            "installer_status": "PRE-PRODUCTION",
            "production_ci_status": "NEEDS-FOUNDER-CONFIRMATION",
            "production_ready": False,
        }

    manifest_path = out_dir / "release-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )

    sums_path = out_dir / "SHA256SUMS.txt"
    lines = [f"{digest}  {name}" for digest, name in sorted(checksums, key=lambda x: x[1])]
    sums_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote {manifest_path.relative_to(REPO_ROOT)}")
    print(f"Wrote {sums_path.relative_to(REPO_ROOT)}")
    for key, info in artifacts.items():
        print(f"  {key:9s} {info['filename']:38s} "
              f"{info['size_bytes'] / 1_048_576:8.1f} MB  {info['sha256'][:16]}...")
    if missing:
        print("  (missing: " + ", ".join(missing) + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
