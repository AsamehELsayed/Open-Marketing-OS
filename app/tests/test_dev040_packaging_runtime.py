"""Native runtime selection and hostile archive tests (no model downloads)."""
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
from dataclasses import replace
import pytest
from app.services.llm.local_model_catalog import load_catalog, CatalogError
from app.services.llm.local_model_download import install_runtime_archive, ModelDownloadError
from scripts.package.build_desktop import bundle_linux_openmp_runtime, scan_payload, stage_payload


def test_platform_selection_has_no_windows_fallback():
    windows = load_catalog(target_platform="win32", machine="AMD64")
    linux = load_catalog(target_platform="linux", machine="x86_64")
    assert windows.runtime.executable == "llama-server.exe"
    assert windows.runtime.sha256 == "1283323272b04cd07905816a597a0da810918102de958f4ff6f7bbaa70ed2efe"
    assert linux.runtime.executable == "llama-server"
    assert linux.runtime.sha256 == "f6d25dde8f51133143d1453da4fd5f73b145127177612a283bf7995957af3392"
    with pytest.raises(CatalogError, match="unsupported"):
        load_catalog(target_platform="linux", machine="arm64")


def archive_fixture(tmp_path, name="release/llama-server", kind=None, linkname="/outside"):
    archive = tmp_path / "runtime.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        item = tarfile.TarInfo(name)
        item.mode = 0o755
        if kind:
            item.type = kind
            item.linkname = linkname
            tar.addfile(item)
        else:
            item.size = 7
            tar.addfile(item, io.BytesIO(b"fixture"))
    runtime = replace(load_catalog(target_platform="linux", machine="x86_64").runtime,
                      size_bytes=archive.stat().st_size,
                      sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    return archive, runtime


def test_verified_tar_installs_wrapped_executable(tmp_path):
    archive, runtime = archive_fixture(tmp_path)
    server = install_runtime_archive(archive, tmp_path / "installed", runtime)
    assert server.read_bytes() == b"fixture"
    if os.name != "nt":
        assert server.stat().st_mode & 0o111
    with pytest.raises(ModelDownloadError, match="already exists"):
        install_runtime_archive(archive, tmp_path / "installed", runtime)


@pytest.mark.parametrize("name,kind", [("../llama-server", None), ("release/llama-server", tarfile.SYMTYPE), ("release/llama-server", tarfile.FIFOTYPE)])
def test_runtime_rejects_traversal_links_special_files(tmp_path, name, kind):
    archive, runtime = archive_fixture(tmp_path, name, kind)
    with pytest.raises(ModelDownloadError):
        install_runtime_archive(archive, tmp_path / "installed", runtime)
    assert not (tmp_path / "installed").exists()


def test_verified_tar_materializes_contained_runtime_symlinks(tmp_path):
    archive = tmp_path / "runtime-links.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        link = tarfile.TarInfo("release/libalias.so")
        link.type = tarfile.SYMTYPE
        link.linkname = "libchain.so"
        tar.addfile(link)
        chain = tarfile.TarInfo("release/libchain.so")
        chain.type = tarfile.SYMTYPE
        chain.linkname = "libreal.so"
        tar.addfile(chain)
        for name, payload, mode in (
            ("release/libreal.so", b"runtime shared library", 0o755),
            ("release/llama-server", b"native server", 0o755),
        ):
            item = tarfile.TarInfo(name)
            item.mode, item.size = mode, len(payload)
            tar.addfile(item, io.BytesIO(payload))
    runtime = replace(load_catalog(target_platform="linux", machine="x86_64").runtime,
                      size_bytes=archive.stat().st_size,
                      sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    server = install_runtime_archive(archive, tmp_path / "installed-links", runtime)
    alias = server.parent / "libalias.so"
    assert alias.read_bytes() == b"runtime shared library"
    assert not alias.is_symlink()


@pytest.mark.parametrize("linkname", ["/outside", "../../outside", "missing.so"])
def test_runtime_rejects_absolute_traversal_and_dangling_symlinks(tmp_path, linkname):
    archive, runtime = archive_fixture(tmp_path, kind=tarfile.SYMTYPE, linkname=linkname)
    with pytest.raises(ModelDownloadError):
        install_runtime_archive(archive, tmp_path / "installed", runtime)
    assert not (tmp_path / "installed").exists()


def test_runtime_rejects_symlink_cycles(tmp_path):
    archive = tmp_path / "runtime-cycle.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, linkname in (("release/a", "b"), ("release/b", "a")):
            item = tarfile.TarInfo(name)
            item.type, item.linkname = tarfile.SYMTYPE, linkname
            tar.addfile(item)
        server = tarfile.TarInfo("release/llama-server")
        payload = b"native server"
        server.mode, server.size = 0o755, len(payload)
        tar.addfile(server, io.BytesIO(payload))
    runtime = replace(load_catalog(target_platform="linux", machine="x86_64").runtime,
                      size_bytes=archive.stat().st_size,
                      sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    with pytest.raises(ModelDownloadError, match="missing or escapes"):
        install_runtime_archive(archive, tmp_path / "installed-cycle", runtime)
    assert not (tmp_path / "installed-cycle").exists()


def test_runtime_hash_pin_precedes_extraction(tmp_path):
    archive, runtime = archive_fixture(tmp_path)
    with pytest.raises(ModelDownloadError, match="checksum"):
        install_runtime_archive(archive, tmp_path / "installed", replace(runtime, sha256="0"*64))
    assert not (tmp_path / "installed").exists()


def test_private_state_and_weights_block_staging(tmp_path):
    (tmp_path / "model.gguf").write_bytes(b"never bundle")
    with pytest.raises(ValueError, match="Private state"):
        scan_payload(tmp_path)


def test_stage_materializes_only_internal_symlinks(tmp_path):
    from app.paths import APP_VERSION
    source = tmp_path / "source"
    internal = source / "_internal"
    internal.mkdir(parents=True)
    (source / "omos-sidecar").write_bytes(b"sidecar")
    (internal / "libreal.so").write_bytes(b"native library")
    try:
        (internal / "libalias.so").symlink_to("libreal.so")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1, "version": APP_VERSION, "edition": "openrouter",
        "build_id": "test", "platform": __import__("sys").platform, "arch": "x64",
    }), encoding="utf-8")

    destination = tmp_path / "staged"
    stage_payload(source, destination, manifest)
    assert (destination / "_internal" / "libalias.so").read_bytes() == b"native library"
    assert not (destination / "_internal" / "libalias.so").is_symlink()
    scan_payload(destination)


def test_source_scan_rejects_escaping_or_dangling_symlinks(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    try:
        (root / "escape").symlink_to(outside)
        (root / "dangling").symlink_to(root / "missing")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(ValueError, match="resolve inside"):
        scan_payload(root, allow_internal_symlinks=True)


def test_linux_runtime_bundles_openmp_library_and_notice(tmp_path):
    source = tmp_path / "libgomp.so.1"
    notice = tmp_path / "copyright"
    source.write_bytes(b"GNU OpenMP runtime")
    notice.write_text("GCC Runtime Library Exception notice", encoding="utf-8")
    runtime = tmp_path / "payload" / "runtime"
    bundled = bundle_linux_openmp_runtime(runtime, source, notice)
    assert bundled.read_bytes() == source.read_bytes()
    assert not bundled.is_symlink()
    assert (runtime / "licenses" / "libgomp" / "copyright").read_text(encoding="utf-8") == notice.read_text(encoding="utf-8")
