from pathlib import Path

import pytest

from scripts.package import make_manifest


def test_dev029_manifest_includes_local_profile_runtime_hashes_and_preproduction(tmp_path: Path):
    payload = tmp_path / "payload"
    runtime = payload / "_internal" / "runtime"
    notice = runtime / "licenses" / "llama.cpp"
    notice.mkdir(parents=True)
    (runtime / "llama-server.exe").write_bytes(b"synthetic runtime executable")
    (runtime / "ggml-base.dll").write_bytes(b"synthetic runtime library")
    (notice / "LICENSE").write_text("MIT synthetic notice", encoding="utf-8")
    (notice / "LICENSE-LLVM-OpenMP").write_text("LLVM OpenMP synthetic notice", encoding="utf-8")

    profile = make_manifest.dev029_local_profile(payload)
    assert profile["version"] == "b11429"
    assert profile["revision"] == "d81235049384534c167caea52b85a694f6103d14"
    assert {item["path"] for item in profile["files"]} == {
        "_internal/runtime/llama-server.exe",
        "_internal/runtime/ggml-base.dll",
        "_internal/runtime/licenses/llama.cpp/LICENSE",
        "_internal/runtime/licenses/llama.cpp/LICENSE-LLVM-OpenMP",
    }
    for item in profile["files"]:
        file_path = payload / item["path"]
        assert item["size_bytes"] == file_path.stat().st_size
        assert item["sha256"] == make_manifest.sha256_of(file_path)


def test_dev029_manifest_describes_local_and_cloud_inference_paths():
    behavior = make_manifest.network_behavior(local_available=True)

    assert behavior["binds"] == "127.0.0.1"
    assert behavior["exposes_to_lan"] is False
    assert "verified Local llama.cpp model" in behavior["ai_inference"]
    assert "OpenRouter/OpenAI cloud providers" in behavior["ai_inference"]
    assert "depending on the user's routing settings" in behavior["ai_inference"]


def test_standard_manifest_keeps_cloud_only_inference_description():
    behavior = make_manifest.network_behavior()

    assert "OpenRouter or OpenAI" in behavior["ai_inference"]
    assert "not on this machine" in behavior["ai_inference"]


def test_dev029_profile_refuses_model_weights(tmp_path: Path):
    runtime = tmp_path / "_internal" / "runtime"
    (runtime / "licenses" / "llama.cpp").mkdir(parents=True)
    (runtime / "llama-server.exe").write_bytes(b"synthetic runtime")
    (runtime / "licenses" / "llama.cpp" / "LICENSE").write_text("MIT", encoding="utf-8")
    (runtime / "licenses" / "llama.cpp" / "LICENSE-LLVM-OpenMP").write_text("LLVM", encoding="utf-8")
    (runtime / "unexpected.gguf").write_bytes(b"must not bundle")

    with pytest.raises(SystemExit, match="model weights"):
        make_manifest.dev029_local_profile(tmp_path)


def test_payload_scan_rejects_model_weights(tmp_path: Path):
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / "model.gguf").write_bytes(b"synthetic only")

    assert any("model weights" in issue for issue in make_manifest.scan_for_forbidden_content(
        tmp_path, reject_model_weights=True
    ))


def test_standard_payload_scan_keeps_legacy_model_file_behavior(tmp_path: Path):
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / "model.gguf").write_bytes(b"synthetic only")

    assert make_manifest.scan_for_forbidden_content(tmp_path) == []


def test_dev029_profile_requires_the_frozen_internal_runtime_layout(tmp_path: Path):
    runtime = tmp_path / "runtime"
    notice = runtime / "licenses" / "llama.cpp"
    notice.mkdir(parents=True)
    (runtime / "llama-server.exe").write_bytes(b"synthetic runtime")
    (notice / "LICENSE").write_text("MIT", encoding="utf-8")
    (notice / "LICENSE-LLVM-OpenMP").write_text("LLVM", encoding="utf-8")

    with pytest.raises(SystemExit, match="packaged llama-server.exe is missing"):
        make_manifest.dev029_local_profile(tmp_path)


def test_isolated_packaging_contract_stages_runtime_and_avoids_shared_outputs():
    root = Path(__file__).resolve().parents[2]
    build_script = (root / "scripts/package/build_windows.ps1").read_text(encoding="utf-8")
    installer_script = (root / "scripts/package/build_installer.ps1").read_text(encoding="utf-8")
    iss = (root / "packaging/omos.iss").read_text(encoding="utf-8")
    spec = (root / "packaging/omos.spec").read_text(encoding="utf-8")

    assert "[string]$RunRoot" in build_script
    assert "[string]$RunRoot" in installer_script
    assert "1283323272b04cd07905816a597a0da810918102de958f4ff6f7bbaa70ed2efe" in build_script
    assert "Get-FileHash -LiteralPath $runtimeArchive -Algorithm SHA256" in build_script
    assert "OMOS_RUNTIME_DIR" in spec and "Model weights must not be included" in spec
    assert '"LICENSE-LLVM-OPENMP"' in spec
    assert "LICENSE-LLVM-OpenMP" in build_script
    assert "llama.cpp-LICENSE" in build_script
    assert 'OutputDir={#OutputDir}' in iss
    assert 'Excludes: "_internal\\runtime\\*"' in iss
    assert 'Source: "{#PayloadDir}\\_internal\\runtime\\*"' in iss
    assert 'DestDir: "{localappdata}\\OpenMarketingOS\\runtime"' in iss
    manifest_script = (root / "scripts/package/make_manifest.py").read_text(encoding="utf-8")
    assert 'runtime_dir = payload / "_internal" / "runtime"' in manifest_script


@pytest.mark.parametrize("missing_notice", ["LICENSE", "LICENSE-LLVM-OpenMP"])
def test_dev029_manifest_requires_both_upstream_runtime_notices(tmp_path: Path, missing_notice: str):
    runtime = tmp_path / "_internal" / "runtime"
    notice = runtime / "licenses" / "llama.cpp"
    notice.mkdir(parents=True)
    (runtime / "llama-server.exe").write_bytes(b"synthetic runtime")
    other_notice = "LICENSE-LLVM-OpenMP" if missing_notice == "LICENSE" else "LICENSE"
    (notice / other_notice).write_text("synthetic notice", encoding="utf-8")

    with pytest.raises(SystemExit, match="both llama.cpp MIT and LLVM OpenMP notices"):
        make_manifest.dev029_local_profile(tmp_path)


def test_source_controlled_notice_matches_pinned_upstream_license_header():
    root = Path(__file__).resolve().parents[2]
    notice = (root / "scripts/package/notices/llama.cpp-LICENSE").read_text(encoding="utf-8")
    assert notice.startswith("MIT License\n\nCopyright (c) 2023-2026 The ggml authors\n")
    assert "THE SOFTWARE IS PROVIDED \"AS IS\"" in notice
