"""Explicit, checksum-first downloader for the pinned Local GGUF profile."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .local_model_catalog import LocalModelCatalog, ModelProfile, default_catalog


CHUNK_BYTES = 1024 * 1024
_REDIRECT_HOSTS = {
    "huggingface.co", "cdn-lfs.huggingface.co", "cas-bridge.xethub.hf.co",
    "cas-server.xethub.hf.co", "transfer.xethub.hf.co", "us.aws.cdn.hf.co",
}


class ModelDownloadError(RuntimeError):
    """Safe, stable downloader error; contains no URL or local path."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DownloadResult:
    model_id: str
    state: str
    verified: bool
    activated: bool
    directory: Path
    downloaded_bytes: int


class LocalModelDownloader:
    """Download every pinned shard, verify all, then atomically activate.

    `explicit=True` is mandatory so calling this from startup/settings reads
    can never fetch model bytes. HTTP and filesystem-related seams are
    injectable for deterministic tests.
    """

    def __init__(
        self,
        install_root: str | Path,
        *,
        catalog: LocalModelCatalog | None = None,
        open_url: Callable | None = None,
        free_bytes: Callable[[Path], int] | None = None,
        replace: Callable[[str | Path, str | Path], None] | None = None,
        chunk_bytes: int = CHUNK_BYTES,
    ) -> None:
        self.install_root = Path(install_root).expanduser().resolve()
        self.catalog = catalog or default_catalog()
        self._open_url = open_url or self._urlopen
        self._free_bytes = free_bytes or (lambda p: shutil.disk_usage(p).free)
        self._replace = replace or os.replace
        self.chunk_bytes = max(4096, int(chunk_bytes))
        self.state = "not_downloaded"
        self.progress_bytes = 0
        self._active_directory: Path | None = None

    @staticmethod
    def _urlopen(request, timeout=60):
        return urllib.request.urlopen(request, timeout=timeout)

    @property
    def active_marker(self) -> Path:
        return self.install_root / "models" / self.catalog.model.model_id / "active.json"

    @property
    def active_directory(self) -> Path | None:
        if self._active_directory:
            return self._active_directory
        try:
            model_root = self._model_root()
            versions_root = model_root / "versions"
            model_root.resolve().relative_to(self.install_root)
            versions_root.resolve().relative_to(self.install_root)
            marker = json.loads(self.active_marker.read_text(encoding="utf-8"))
            if marker.get("profile_revision") != self.catalog.model.revision:
                return None
            name = str(marker.get("directory", ""))
            if Path(name).name != name:
                return None
            path = (versions_root / name).resolve()
            path.relative_to(versions_root.resolve())
            path.relative_to(self.install_root)
            if self._verify_existing(path):
                self._active_directory = path
                return path
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        return None

    @property
    def active_model_path(self) -> Path | None:
        directory = self.active_directory
        return directory / self.catalog.model.shards[0].filename if directory else None

    def _model_root(self) -> Path:
        return self.install_root / "models" / self.catalog.model.model_id

    def _version_path(self) -> Path:
        return self._model_root() / "versions" / self.catalog.model.active_directory_name

    def _verify_existing(self, directory: Path) -> bool:
        for shard in self.catalog.model.shards:
            path = directory / shard.filename
            try:
                if path.is_symlink() or not path.is_file():
                    return False
                try:
                    path.resolve().relative_to(directory.resolve())
                except ValueError:
                    return False
                if path.stat().st_size != shard.size_bytes:
                    return False
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(self.chunk_bytes), b""):
                        digest.update(block)
                if digest.hexdigest() != shard.sha256:
                    return False
            except OSError:
                return False
        return True

    @staticmethod
    def _validate_redirect(response) -> None:
        final_url = response.geturl() if hasattr(response, "geturl") else ""
        parsed = urlparse(final_url)
        if parsed.scheme != "https" or parsed.hostname not in _REDIRECT_HOSTS:
            raise ModelDownloadError("source_redirect_rejected", "Model source redirect was rejected")

    def _download_shard(self, shard, destination: Path) -> int:
        request = urllib.request.Request(
            shard.url,
            headers={"User-Agent": "OpenMarketingOS-LocalModel/1.0"},
            method="GET",
        )
        digest = hashlib.sha256()
        received = 0
        try:
            response = self._open_url(request, timeout=120)
        except Exception as exc:
            raise ModelDownloadError("download_failed", "Model download failed") from exc
        try:
            status = getattr(response, "status", 200)
            if status != 200:
                raise ModelDownloadError("source_http_error", "Model source returned an error")
            self._validate_redirect(response)
            with destination.open("xb") as output:
                while True:
                    block = response.read(self.chunk_bytes)
                    if not block:
                        break
                    received += len(block)
                    if received > shard.size_bytes:
                        raise ModelDownloadError("size_mismatch", "Downloaded model shard exceeded its pinned size")
                    digest.update(block)
                    output.write(block)
                    self.progress_bytes += len(block)
                output.flush()
                os.fsync(output.fileno())
        except ModelDownloadError:
            raise
        except Exception as exc:
            raise ModelDownloadError("download_failed", "Model download failed") from exc
        finally:
            try:
                response.close()
            except Exception:
                pass
        if received != shard.size_bytes:
            raise ModelDownloadError("size_mismatch", "Downloaded model shard size did not match the pin")
        if digest.hexdigest() != shard.sha256:
            raise ModelDownloadError("checksum_mismatch", "Downloaded model shard checksum did not match the pin")
        return received

    def download(self, *, explicit: bool = False) -> DownloadResult:
        if explicit is not True:
            raise ModelDownloadError("explicit_action_required", "Model download requires an explicit user action")
        profile: ModelProfile = self.catalog.model
        self.install_root.mkdir(parents=True, exist_ok=True)
        if self.active_directory is not None:
            self.state = "ready"
            return DownloadResult(profile.model_id, self.state, True, True,
                                  self.active_directory, profile.download_bytes)
        if self._free_bytes(self.install_root) < profile.temporary_disk_bytes:
            self.state = "insufficient_space"
            raise ModelDownloadError("insufficient_space", "Not enough free disk space for safe model activation")

        root = self._model_root()
        staging_root = root / ".staging"
        versions = root / "versions"
        root.mkdir(parents=True, exist_ok=True)
        staging_root.mkdir(parents=True, exist_ok=True)
        versions.mkdir(parents=True, exist_ok=True)
        try:
            root.resolve().relative_to(self.install_root)
            staging_root.resolve().relative_to(self.install_root)
            versions.resolve().relative_to(self.install_root)
        except ValueError as exc:
            self.state = "failed"
            raise ModelDownloadError("path_outside_install", "Model storage path is outside the install root") from exc
        final = self._version_path()
        if final.exists():
            # Never overwrite unexpected content: preserve it for investigation.
            self.state = "verification_failed"
            raise ModelDownloadError("existing_version_invalid", "An existing model version failed verification")

        stage = Path(tempfile.mkdtemp(prefix="model-", dir=staging_root))
        self.progress_bytes = 0
        self.state = "downloading"
        total = 0
        try:
            for shard in profile.shards:
                total += self._download_shard(shard, stage / shard.filename)
            # Recheck all files as a group immediately before promotion.
            self.state = "verifying"
            if not self._verify_existing(stage):
                raise ModelDownloadError("checksum_mismatch", "Downloaded model profile failed verification")
            self._replace(stage, final)
            marker = {
                "model_id": profile.model_id,
                "profile_revision": profile.revision,
                "directory": final.name,
                "verified_shards": [
                    {"filename": shard.filename, "size_bytes": shard.size_bytes,
                     "sha256": shard.sha256}
                    for shard in profile.shards
                ],
            }
            marker_tmp = root / f"active-{os.getpid()}-{id(self)}.tmp"
            try:
                with marker_tmp.open("x", encoding="utf-8") as stream:
                    json.dump(marker, stream, sort_keys=True)
                    stream.flush()
                    os.fsync(stream.fileno())
                self._replace(marker_tmp, self.active_marker)
            finally:
                marker_tmp.unlink(missing_ok=True)
            self._active_directory = final
            self.state = "ready"
            return DownloadResult(profile.model_id, self.state, True, True, final, total)
        except Exception:
            self.state = "failed"
            raise
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)


def download_pinned_model(install_root: str | Path, *, explicit: bool = False,
                          catalog: LocalModelCatalog | None = None) -> DownloadResult:
    """Convenience function for a single explicit download request."""
    return LocalModelDownloader(install_root, catalog=catalog).download(explicit=explicit)
