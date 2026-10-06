"""Pinned local model and llama.cpp runtime metadata.

The model profile is one logical model composed of two GGUF shards. Every
shard is pinned to an immutable upstream revision, exact length, and SHA-256.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_CATALOG_PATH = Path(__file__).resolve().parents[3] / "config" / "local_models.json"


class CatalogError(ValueError):
    """The source-controlled model catalog is invalid or incomplete."""


@dataclass(frozen=True)
class ModelShard:
    filename: str
    url: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ModelProfile:
    model_id: str
    display_name: str
    publisher: str
    source: str
    revision: str
    source_url: str
    license: str
    license_url: str
    quantization: str
    shards: tuple[ModelShard, ...]
    download_bytes: int
    temporary_disk_bytes: int

    @property
    def version(self) -> str:
        return self.revision

    @property
    def active_directory_name(self) -> str:
        return f"{self.model_id}-{self.revision[:12]}"

    @property
    def expected_server_model_id(self) -> str:
        return self.model_id


@dataclass(frozen=True)
class RuntimeProfile:
    name: str
    version: str
    source: str
    revision: str
    asset_name: str
    url: str
    size_bytes: int
    sha256: str
    license: str
    license_url: str
    attestation_url: str
    executable: str


@dataclass(frozen=True)
class LocalModelCatalog:
    schema_version: int
    default_model_id: str
    model: ModelProfile
    runtime: RuntimeProfile

    def public_model_metadata(self) -> dict:
        """Return UI-safe pinned metadata; do not include local file paths."""
        return {
            "model_id": self.model.model_id,
            "display_name": self.model.display_name,
            "source": self.model.source,
            "source_url": self.model.source_url,
            "publisher": self.model.publisher,
            "license": self.model.license,
            "license_url": self.model.license_url,
            "model_bytes": self.model.download_bytes,
            "temporary_disk_bytes": self.model.temporary_disk_bytes,
            "sha256": [shard.sha256 for shard in self.model.shards],
            "shards": [
                {"filename": shard.filename,
                 "size_bytes": shard.size_bytes,
                 "sha256": shard.sha256}
                for shard in self.model.shards
            ],
            "quantization": self.model.quantization,
            "runtime": {
                "name": self.runtime.name,
                "version": self.runtime.version,
                "revision": self.runtime.revision,
                "asset_name": self.runtime.asset_name,
                "asset_bytes": self.runtime.size_bytes,
                "sha256": self.runtime.sha256,
                "license": self.runtime.license,
                "license_url": self.runtime.license_url,
                "attestation_url": self.runtime.attestation_url,
            },
        }


def _require_https(url: str, field: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "huggingface.co", "github.com"
    }:
        raise CatalogError(f"{field} must use an approved HTTPS source")


def _hex_digest(value: str, field: str) -> str:
    value = str(value or "").lower()
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise CatalogError(f"{field} must be a 64-character SHA-256 hex digest")
    return value


def load_catalog(path: str | Path = DEFAULT_CATALOG_PATH) -> LocalModelCatalog:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        model_raw = raw["model"]
        shards = []
        for item in model_raw["shards"]:
            filename = str(item["filename"])
            if Path(filename).name != filename or not filename.lower().endswith(".gguf"):
                raise CatalogError("model shard filename is unsafe")
            url = str(item["url"])
            _require_https(url, "model shard URL")
            size = int(item["size_bytes"])
            if size <= 0:
                raise CatalogError("model shard size must be positive")
            shards.append(ModelShard(
                filename=filename,
                url=url,
                size_bytes=size,
                sha256=_hex_digest(item["sha256"], "model shard SHA-256"),
            ))
        if not shards:
            raise CatalogError("model profile must contain at least one shard")
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", str(model_raw["model_id"])):
            raise CatalogError("model id is not a safe profile identifier")
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", str(model_raw["revision"])):
            raise CatalogError("model revision must be a pinned immutable hash")
        shard_source_prefix = (
            f"https://huggingface.co/{model_raw['source']}/resolve/"
            f"{model_raw['revision']}/"
        )
        if any(not item.url.startswith(shard_source_prefix) for item in shards):
            raise CatalogError("model shard URLs must use the pinned repository revision")
        download_bytes = int(model_raw["download_bytes"])
        temporary_disk_bytes = int(model_raw["temporary_disk_bytes"])
        if download_bytes != sum(item.size_bytes for item in shards):
            raise CatalogError("model download size does not match its pinned shards")
        if temporary_disk_bytes < download_bytes * 2:
            raise CatalogError("temporary disk requirement must cover staged and active model")
        if len({item.filename.casefold() for item in shards}) != len(shards):
            raise CatalogError("model shard filenames must be unique")

        for field in ("source_url", "license_url"):
            _require_https(str(model_raw[field]), field)
        model = ModelProfile(
            model_id=str(model_raw["model_id"]),
            display_name=str(model_raw["display_name"]),
            publisher=str(model_raw["publisher"]),
            source=str(model_raw["source"]),
            revision=str(model_raw["revision"]),
            source_url=str(model_raw["source_url"]),
            license=str(model_raw["license"]),
            license_url=str(model_raw["license_url"]),
            quantization=str(model_raw["quantization"]),
            shards=tuple(shards),
            download_bytes=download_bytes,
            temporary_disk_bytes=temporary_disk_bytes,
        )

        runtime_raw = raw["runtime"]
        for field in ("url", "license_url", "attestation_url"):
            _require_https(str(runtime_raw[field]), field)
        runtime = RuntimeProfile(
            name=str(runtime_raw["name"]),
            version=str(runtime_raw["version"]),
            source=str(runtime_raw["source"]),
            revision=str(runtime_raw["revision"]),
            asset_name=str(runtime_raw["asset_name"]),
            url=str(runtime_raw["url"]),
            size_bytes=int(runtime_raw["size_bytes"]),
            sha256=_hex_digest(runtime_raw["sha256"], "runtime SHA-256"),
            license=str(runtime_raw["license"]),
            license_url=str(runtime_raw["license_url"]),
            attestation_url=str(runtime_raw["attestation_url"]),
            executable=str(runtime_raw["executable"]),
        )
        if Path(runtime.executable).name != runtime.executable:
            raise CatalogError("runtime executable name is unsafe")
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", runtime.revision):
            raise CatalogError("runtime revision must be a pinned immutable hash")
        expected_runtime_prefix = (
            f"https://github.com/{runtime.source}/releases/download/"
            f"{runtime.version}/"
        )
        if not runtime.url.startswith(expected_runtime_prefix):
            raise CatalogError("runtime URL must match the pinned release identity")
        if runtime.size_bytes <= 0:
            raise CatalogError("runtime asset size must be positive")
        if str(raw["default_model_id"]) != model.model_id:
            raise CatalogError("default model id does not match the pinned model")
        return LocalModelCatalog(
            schema_version=int(raw["schema_version"]),
            default_model_id=str(raw["default_model_id"]),
            model=model,
            runtime=runtime,
        )
    except CatalogError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CatalogError("local model catalog could not be loaded") from exc


def default_catalog() -> LocalModelCatalog:
    return load_catalog()
