"""Provenance and schema checks for the pinned Local profile."""
import json

import pytest

from app.services.llm.local_model_catalog import (
    DEFAULT_CATALOG_PATH, CatalogError, load_catalog,
)


def test_catalog_pins_authoritative_model_shards_and_runtime():
    catalog = load_catalog()
    model = catalog.model
    assert model.model_id == "qwen2.5-7b-instruct-q4_k_m"
    assert model.source == "Qwen/Qwen2.5-7B-Instruct-GGUF"
    assert model.revision == "293ca9a10157b0e5fc5cb32af8b636a88bede891"
    assert model.license == "Apache-2.0"
    assert [s.size_bytes for s in model.shards] == [3993201344, 689872288]
    assert [s.sha256 for s in model.shards] == [
        "dfce12e3862a5283ccfb88221b48480e58745165de856439950d0f22590580db",
        "539cf93f78e887edea1c04e2d7d8cdaca9d01dae9c9025bcb8accbe29df3d72a",
    ]
    assert model.download_bytes == sum(s.size_bytes for s in model.shards)
    assert model.temporary_disk_bytes >= model.download_bytes * 2

    runtime = catalog.runtime
    assert runtime.version == "b11429"
    assert runtime.source == "ggml-org/llama.cpp"
    assert runtime.revision == "d81235049384534c167caea52b85a694f6103d14"
    assert runtime.size_bytes == 19398918
    assert runtime.license == "MIT"
    assert runtime.sha256 == "1283323272b04cd07905816a597a0da810918102de958f4ff6f7bbaa70ed2efe"


def test_catalog_rejects_size_hash_and_path_drift(tmp_path):
    source = json.loads(DEFAULT_CATALOG_PATH.read_text(encoding="utf-8"))
    source["model"]["download_bytes"] += 1
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(CatalogError, match="size does not match"):
        load_catalog(path)

    source["model"]["download_bytes"] -= 1
    source["model"]["shards"][0]["filename"] = "../outside.gguf"
    path.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(CatalogError, match="filename is unsafe"):
        load_catalog(path)


def test_public_metadata_has_no_runtime_install_path():
    metadata = load_catalog().public_model_metadata()
    assert metadata["model_id"] == "qwen2.5-7b-instruct-q4_k_m"
    assert metadata["shards"][0]["filename"].endswith("00001-of-00002.gguf")
    assert all(not key.lower().endswith("path") for key in metadata)
