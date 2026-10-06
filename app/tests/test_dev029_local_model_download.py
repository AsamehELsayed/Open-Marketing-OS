"""Synthetic-only streaming download, verification and activation tests."""
import hashlib
import io
import json
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

import pytest

from app.services.llm.local_model_catalog import (
    LocalModelCatalog, ModelProfile, ModelShard, RuntimeProfile,
)
from app.services.llm.local_model_download import (
    LocalModelDownloader, ModelDownloadError,
)


def _catalog(payloads):
    shards = tuple(ModelShard(
        filename=f"synthetic-part-{i}.gguf",
        url=f"https://huggingface.co/synthetic/repo/resolve/{'a' * 40}/part-{i}.gguf",
        size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
    ) for i, data in enumerate(payloads, 1))
    model = ModelProfile(
        model_id="synthetic-model", display_name="Synthetic Model",
        publisher="Test Publisher", source="synthetic/repo", revision="a" * 40,
        source_url="https://huggingface.co/synthetic/repo/commit/" + "a" * 40,
        license="Apache-2.0", license_url="https://huggingface.co/synthetic/repo/blob/main/LICENSE",
        quantization="Q4_K_M", shards=shards,
        download_bytes=sum(len(data) for data in payloads),
        temporary_disk_bytes=sum(len(data) for data in payloads) * 2,
    )
    runtime = RuntimeProfile(
        name="llama.cpp", version="b-test", source="ggml-org/llama.cpp",
        revision="b" * 40, asset_name="runtime.zip",
        url="https://github.com/ggml-org/llama.cpp/releases/download/test/runtime.zip",
        size_bytes=1, sha256="c" * 64, license="MIT",
        license_url="https://github.com/ggml-org/llama.cpp/blob/master/LICENSE",
        attestation_url="https://github.com/ggml-org/llama.cpp/attestations/test",
        executable="llama-server.exe",
    )
    return LocalModelCatalog(1, model.model_id, model, runtime)


class FakeResponse(io.BytesIO):
    status = 200

    def __init__(self, data, url="https://huggingface.co/synthetic/repo/file.gguf"):
        super().__init__(data)
        self.url = url

    def geturl(self):
        return self.url


def test_redirect_allowlist_accepts_observed_cdn_and_rejects_unrelated_host():
    observed = FakeResponse(b"", "https://us.aws.cdn.hf.co/file.gguf")
    LocalModelDownloader._validate_redirect(observed)

    unrelated = FakeResponse(b"", "https://unrelated-cdn.example/file.gguf")
    with pytest.raises(ModelDownloadError) as exc:
        LocalModelDownloader._validate_redirect(unrelated)
    assert exc.value.code == "source_redirect_rejected"


def test_download_requires_explicit_action_and_activates_only_after_all_checks(tmp_path):
    payloads = [b"synthetic shard one", b"synthetic shard two"]
    catalog = _catalog(payloads)
    requested = []

    def open_url(request, timeout):
        path = urlparse(request.full_url).path
        index = 0 if path.endswith("part-1.gguf") else 1
        requested.append(path)
        return FakeResponse(payloads[index])

    downloader = LocalModelDownloader(
        tmp_path, catalog=catalog, open_url=open_url,
        free_bytes=lambda _path: catalog.model.temporary_disk_bytes,
        chunk_bytes=4096,
    )
    with pytest.raises(ModelDownloadError) as exc:
        downloader.download()
    assert exc.value.code == "explicit_action_required"
    assert requested == []
    assert downloader.active_directory is None

    result = downloader.download(explicit=True)
    assert result.verified and result.activated and result.state == "ready"
    assert result.downloaded_bytes == sum(map(len, payloads))
    assert len(requested) == 2
    assert downloader.active_directory == result.directory
    assert [p.read_bytes() for p in sorted(result.directory.glob("*.gguf"))] == payloads
    marker = json.loads(downloader.active_marker.read_text(encoding="utf-8"))
    assert marker["verified_shards"][0]["sha256"] == catalog.model.shards[0].sha256


@pytest.mark.parametrize("failure", ["short", "wrong_hash", "oversize"])
def test_failed_download_never_activates_and_cleans_partial_bytes(tmp_path, failure):
    expected = b"right-data"
    catalog = _catalog([expected])
    payload = {
        "short": b"right",
        "wrong_hash": b"wrong-data",
        "oversize": expected + b"extra",
    }[failure]

    def open_url(_request, timeout):
        return FakeResponse(payload)

    downloader = LocalModelDownloader(
        tmp_path, catalog=catalog, open_url=open_url,
        free_bytes=lambda _path: catalog.model.temporary_disk_bytes,
    )
    with pytest.raises(ModelDownloadError):
        downloader.download(explicit=True)
    assert downloader.active_directory is None
    assert not list((tmp_path / "models").rglob("*.gguf"))
    assert not list((tmp_path / "models").rglob("active.json"))
    assert not list((tmp_path / "models").rglob("*.tmp"))


def test_download_checks_space_before_request_or_staging(tmp_path):
    catalog = _catalog([b"payload"])
    calls = []
    downloader = LocalModelDownloader(
        tmp_path, catalog=catalog,
        open_url=lambda *args, **kwargs: calls.append("network"),
        free_bytes=lambda _path: 0,
    )
    with pytest.raises(ModelDownloadError) as exc:
        downloader.download(explicit=True)
    assert exc.value.code == "insufficient_space"
    assert calls == []
    assert not (tmp_path / "models").exists()


def test_second_shard_failure_keeps_model_inactive(tmp_path):
    good = [b"first-good", b"second-good"]
    catalog = _catalog(good)

    def open_url(request, timeout):
        return FakeResponse(good[0] if request.full_url.endswith("part-1.gguf") else b"second-evil")

    downloader = LocalModelDownloader(
        tmp_path, catalog=catalog, open_url=open_url,
        free_bytes=lambda _path: catalog.model.temporary_disk_bytes,
    )
    with pytest.raises(ModelDownloadError) as exc:
        downloader.download(explicit=True)
    assert exc.value.code == "checksum_mismatch"
    assert downloader.active_directory is None
    assert not list((tmp_path / "models").rglob("*.gguf"))
    assert not list((tmp_path / "models").rglob("active.json"))


def test_existing_active_profile_is_rediscovered_without_network(tmp_path):
    payloads = [b"one", b"two"]
    catalog = _catalog(payloads)
    first = LocalModelDownloader(
        tmp_path, catalog=catalog,
        open_url=lambda request, timeout: FakeResponse(payloads[0 if request.full_url.endswith("part-1.gguf") else 1]),
        free_bytes=lambda _path: catalog.model.temporary_disk_bytes,
    )
    first.download(explicit=True)
    second = LocalModelDownloader(
        tmp_path, catalog=catalog,
        open_url=lambda *args, **kwargs: pytest.fail("rediscovery must not download"),
        free_bytes=lambda _path: catalog.model.temporary_disk_bytes,
    )
    assert second.active_directory is not None
    assert second.active_model_path.name == catalog.model.shards[0].filename
