"""Local semantic embedding providers.

The hash provider is retained only for deterministic unit fixtures. Production
callers should use :class:`LocalMultilingualE5Embeddings`; if it cannot load,
the caller must report semantic retrieval unavailable and retain lexical FTS.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Protocol, Sequence

_VALIDATED_SNAPSHOT_SIGNATURES: dict[str, tuple[tuple, bool]] = {}


class EmbeddingsProvider(Protocol):
    id: str
    version: str
    dim: int
    semantic: bool

    def embed(self, text: str) -> list[float]: ...
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class LocalHashEmbeddings:
    """Deterministic, explicitly NON-SEMANTIC fixture provider."""

    id = "local-hash"
    version = "1"
    dim = 256
    semantic = False

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in text.lower().split():
            vec[int(hashlib.sha256(token.encode()).hexdigest(), 16) % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.embed(text)


class LocalMultilingualE5Embeddings:
    """Pinned multilingual-e5-small inference using CPU ONNX Runtime.

    Weights are downloaded once into an OS user cache, then every load is
    offline-only. No application text is sent to the hub. A model revision is
    part of the provider identity so vector stores can detect incompatible
    embeddings.
    """

    id = "intfloat/multilingual-e5-small"
    revision = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    version = f"{id}@{revision}"
    dim = 384
    semantic = True
    max_input_tokens = 512
    _snapshot_files = ("config.json", "tokenizer.json", "onnx/model.onnx")

    def __init__(self, cache_dir: str | None = None, *, progress_callback=None, model=None):
        from pathlib import Path
        import os

        self.cache_dir = Path(cache_dir or os.environ.get(
            "OMOS_MODEL_CACHE",
            Path(os.environ.get("LOCALAPPDATA", Path.home())) / "OpenMarketingOS" / "models",
        ))
        self.progress_callback = progress_callback
        self._model = model

    @property
    def ready(self) -> bool:
        """True only for a loaded model or a fully validated local snapshot."""
        return self._model is not None or self._snapshot_cache_valid()

    def _snapshot_cache_valid(self) -> bool:
        from pathlib import Path
        path = (self.cache_dir / "intfloat" / "multilingual-e5-small" / self.revision)
        required = (path / "manifest.json", path / "config.json", path / "tokenizer.json",
                    path / "onnx" / "model.onnx")
        try:
            signature = tuple((str(item), item.stat().st_size, item.stat().st_mtime_ns)
                              for item in required)
        except OSError:
            return False
        key = str(Path(path).resolve())
        cached = _VALIDATED_SNAPSHOT_SIGNATURES.get(key)
        if cached and cached[0] == signature:
            return cached[1]
        valid = self._valid_snapshot(path)
        _VALIDATED_SNAPSHOT_SIGNATURES[key] = (signature, valid)
        return valid

    def _load_model(self):
        if self._model is not None:
            return self._model
        self._report_progress({"stage": "checking_model_cache"})
        try:
            from filelock import FileLock
            from huggingface_hub import snapshot_download
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise RuntimeError("local_embedding_runtime_missing") from exc

        model_dir = self.cache_dir / "intfloat" / "multilingual-e5-small" / self.revision
        model_dir.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(model_dir.parent / f"{self.revision}.lock")):
            if not self._valid_snapshot(model_dir):
                self._report_progress({"stage": "downloading_model", "current": 0, "total": None})
                model_dir.mkdir(parents=True, exist_ok=True)
                snapshot_download(
                    repo_id=self.id,
                    revision=self.revision,
                    cache_dir=str(self.cache_dir / "hub"),
                    local_dir=str(model_dir),
                    allow_patterns=["config.json", "tokenizer.json", "tokenizer_config.json",
                                    "special_tokens_map.json", "onnx/model.onnx"],
                    tqdm_class=_ProgressTqdm(self.progress_callback) if self.progress_callback else None,
                )
                self._report_progress({"stage": "validating_model_cache"})
                if not self._has_required_files(model_dir):
                    raise RuntimeError("local_embedding_download_incomplete")
                self._write_manifest(model_dir)
            if not self._valid_snapshot(model_dir):
                raise RuntimeError("local_embedding_cache_invalid")
        self._report_progress({"stage": "loading_local_model"})
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        session = ort.InferenceSession(str(model_dir / "onnx" / "model.onnx"),
                                       sess_options=options, providers=["CPUExecutionProvider"])
        tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        # XLM-R tokenizer metadata names its padding token ``<pad>``; some
        # compatible tokenizer exports instead expose ``[PAD]``.
        pad_token = self._padding_token(tokenizer)
        pad_id = tokenizer.token_to_id(pad_token) if pad_token else None
        if pad_id is None:
            raise RuntimeError("local_embedding_tokenizer_invalid")
        tokenizer.enable_truncation(max_length=self.max_input_tokens)
        tokenizer.enable_padding(pad_id=pad_id, pad_token=pad_token)
        self._model = (session, tokenizer, np)
        self._report_progress({"stage": "local_model_ready"})
        return self._model

    def _report_progress(self, update):
        if self.progress_callback:
            try:
                self.progress_callback(update)
            except Exception:
                # Observability must not make local inference fail.
                pass

    @staticmethod
    def _padding_token(tokenizer):
        return next((candidate for candidate in ("<pad>", "[PAD]")
                     if tokenizer.token_to_id(candidate) is not None), None)

    @staticmethod
    def _has_required_files(path):
        from pathlib import Path
        path = Path(path)
        return ((path / "config.json").is_file() and (path / "tokenizer.json").is_file()
                and (path / "onnx" / "model.onnx").is_file())

    @classmethod
    def _write_manifest(cls, path):
        from pathlib import Path
        path = Path(path)
        entries = {}
        for name in cls._snapshot_files:
            target = path / name
            digest = hashlib.sha256()
            with target.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
            entries[name] = {"size": target.stat().st_size, "sha256": digest.hexdigest()}
        manifest = {"model": cls.id, "revision": cls.revision, "files": entries}
        temp = path / ".manifest.tmp"
        temp.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
        temp.replace(path / "manifest.json")

    @classmethod
    def _valid_snapshot(cls, path):
        from pathlib import Path
        path = Path(path)
        if not cls._has_required_files(path):
            return False
        try:
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or set(manifest) != {"model", "revision", "files"}:
                return False
            if manifest.get("model") != cls.id or manifest.get("revision") != cls.revision:
                return False
            files = manifest["files"]
            if not isinstance(files, dict) or set(files) != set(cls._snapshot_files):
                return False
            for name in cls._snapshot_files:
                expected = files[name]
                if (not isinstance(expected, dict) or set(expected) != {"size", "sha256"}
                        or isinstance(expected["size"], bool)
                        or not isinstance(expected["size"], int) or expected["size"] <= 0
                        or not isinstance(expected["sha256"], str)
                        or len(expected["sha256"]) != 64
                        or any(ch not in "0123456789abcdef" for ch in expected["sha256"])):
                    return False
                target = path / name
                if not target.is_file() or target.stat().st_size != expected["size"]:
                    return False
                digest = hashlib.sha256()
                with target.open("rb") as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest() != expected["sha256"]:
                    return False
            return True
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def _encode(self, texts: Sequence[str], prefix: str) -> list[list[float]]:
        if not texts:
            return []
        session, tokenizer, np = self._load_model()
        prepared = [f"{prefix}: {text}" for text in texts]
        encodings = tokenizer.encode_batch(prepared)
        input_names = {item.name for item in session.get_inputs()}
        inputs = {
            "input_ids": np.asarray([item.ids for item in encodings], dtype=np.int64),
            "attention_mask": np.asarray([item.attention_mask for item in encodings], dtype=np.int64),
            "token_type_ids": np.asarray([item.type_ids for item in encodings], dtype=np.int64),
        }
        output = session.run(None, {key: value for key, value in inputs.items() if key in input_names})[0]
        mask = inputs["attention_mask"].astype(np.float32)[..., None]
        vectors = (output * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1e-9)
        vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
        return [[float(value) for value in row] for row in vectors]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._encode(texts, "passage")

    def embed_query(self, text: str) -> list[float]:
        return self._encode([text], "query")[0]

    def embed(self, text: str) -> list[float]:
        """Compatibility alias; generic legacy calls are treated as documents."""
        return self.embed_documents([text])[0]


class _ProgressTqdm:
    """tqdm-compatible adapter that reports received bytes through callback."""

    def __new__(cls, callback):
        from tqdm.auto import tqdm

        class Progress(tqdm):
            def update(self, n=1):
                result = super().update(n)
                try:
                    callback({"stage": "downloading_model", "current": self.n,
                              "total": self.total if isinstance(self.total, (int, float)) else None})
                except Exception:
                    pass
                return result
        return Progress
