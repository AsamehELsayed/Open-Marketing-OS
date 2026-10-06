"""Focused provider contract and cache integrity tests for DEV-015 W1."""
from pathlib import Path
import json

import pytest

from app.services.rag.embeddings import LocalHashEmbeddings, LocalMultilingualE5Embeddings


class _CaptureModel:
    def __init__(self):
        self.calls = []

    def encode(self, texts, **kwargs):
        self.calls.append((texts, kwargs))


class _FakeEncoding:
    def __init__(self, text):
        self.text = text
        self.ids = [1, 2]
        self.attention_mask = [1, 1]
        self.type_ids = [0, 0]


class _FakeTokenizer:
    def __init__(self):
        self.encodings = []

    def encode_batch(self, texts):
        self.encodings.extend(texts)
        return [_FakeEncoding(text) for text in texts]


class _FakeSession:
    def __init__(self):
        self.feed = None

    def get_inputs(self):
        return [type("Input", (), {"name": n}) for n in ("input_ids", "attention_mask")]

    def run(self, _outputs, feed):
        self.feed = feed
        import numpy as np
        return [np.tile(np.asarray([[[0.6, 0.8], [0.6, 0.8]]]), (len(feed["input_ids"]), 1, 1))]


def test_e5_query_and_document_contract_and_normalization():
    session, tokenizer = _FakeSession(), _FakeTokenizer()
    import numpy as np
    provider = LocalMultilingualE5Embeddings(model=(session, tokenizer, np))

    query = provider.embed_query("ما سياسة الاسترداد؟")
    documents = provider.embed_documents(["Refund policy for annual plans"])

    assert query == [0.6, 0.8]
    assert documents == [[0.6, 0.8]]
    assert tokenizer.encodings == ["query: ما سياسة الاسترداد؟", "passage: Refund policy for annual plans"]
    assert session.feed["input_ids"].dtype == np.int64
    assert provider.semantic is True
    assert provider.dim == 384
    assert provider.version.endswith("@614241f622f53c4eeff9890bdc4f31cfecc418b3")


def test_e5_empty_batch_does_not_load_model():
    provider = LocalMultilingualE5Embeddings()
    assert provider.embed_documents([]) == []
    assert provider._model is None


def test_pinned_xlm_roberta_pad_token_is_supported():
    class PinnedTokenizer:
        # intfloat/multilingual-e5-small's pinned tokenizer_config.json uses <pad>.
        def token_to_id(self, token):
            return 1 if token == "<pad>" else None

    assert LocalMultilingualE5Embeddings._padding_token(PinnedTokenizer()) == "<pad>"


def test_cache_validation_requires_pinned_model_tokenizer_and_manifest(tmp_path: Path):
    target = tmp_path / "snapshot"
    target.mkdir()
    (target / "config.json").write_text("{}", encoding="utf-8")
    assert not LocalMultilingualE5Embeddings._valid_snapshot(target)
    (target / "tokenizer.json").write_text("{}", encoding="utf-8")
    (target / "onnx").mkdir()
    (target / "onnx" / "model.onnx").write_bytes(b"fixture")
    LocalMultilingualE5Embeddings._write_manifest(target)
    assert LocalMultilingualE5Embeddings._valid_snapshot(target)
    (target / "onnx" / "model.onnx").write_bytes(b"corrupted")
    assert not LocalMultilingualE5Embeddings._valid_snapshot(target)


def _complete_snapshot(target: Path):
    target.mkdir(exist_ok=True)
    (target / "config.json").write_text("{}", encoding="utf-8")
    (target / "tokenizer.json").write_text("{}", encoding="utf-8")
    (target / "onnx").mkdir(exist_ok=True)
    (target / "onnx" / "model.onnx").write_bytes(b"fixture")
    LocalMultilingualE5Embeddings._write_manifest(target)


@pytest.mark.parametrize("bad_name", ["unexpected.json", "../outside.json", "..\\outside.json", "/tmp/outside.json", "C:\\outside.json"])
def test_cache_manifest_rejects_unexpected_or_unsafe_file_names(tmp_path: Path, bad_name: str):
    _complete_snapshot(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][bad_name] = manifest["files"].pop("config.json")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert not LocalMultilingualE5Embeddings._valid_snapshot(tmp_path)


@pytest.mark.parametrize("mutate", [
    lambda m: m.update(extra=True),
    lambda m: m.update(files=[]),
    lambda m: m["files"]["config.json"].update(size=True),
    lambda m: m["files"]["config.json"].update(sha256="not-a-digest"),
    lambda m: m["files"]["config.json"].update(unexpected="value"),
])
def test_cache_manifest_rejects_malformed_schema(tmp_path: Path, mutate):
    _complete_snapshot(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mutate(manifest)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert not LocalMultilingualE5Embeddings._valid_snapshot(tmp_path)


def test_cache_manifest_rejects_missing_required_file_and_has_valid_cache(tmp_path: Path):
    _complete_snapshot(tmp_path)
    assert LocalMultilingualE5Embeddings._valid_snapshot(tmp_path)
    (tmp_path / "tokenizer.json").unlink()
    assert not LocalMultilingualE5Embeddings._valid_snapshot(tmp_path)


def test_cache_manifest_rejects_corrupted_required_file(tmp_path: Path):
    _complete_snapshot(tmp_path)
    (tmp_path / "config.json").write_text("{\"changed\":true}", encoding="utf-8")
    assert not LocalMultilingualE5Embeddings._valid_snapshot(tmp_path)


def test_hash_provider_is_explicitly_nonsemantic_and_compatible():
    provider = LocalHashEmbeddings()
    assert provider.semantic is False
    assert provider.embed_query("campaign") == provider.embed("campaign")
    assert provider.embed_documents(["campaign"])[0] == provider.embed("campaign")
