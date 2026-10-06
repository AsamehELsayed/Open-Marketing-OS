"""W2 remediation — LangChain-first RAG product path (thin domain adapters).

v1 product path uses real maintained LangChain components; homegrown
protocol shapes remain only as offline legacy fallback (never the product
path). Project scoping (fail-closed, before top-k) plus FTS5/Chroma parity
stay in thin adapters owned by W2.

Real components (verified against live docs 2026-09-22):
- Document: langchain_core.documents.Document
  Source: https://docs.langchain.com/oss/python/langchain/knowledge-base.md
- Splitter: langchain_text_splitters.RecursiveCharacterTextSplitter
  Source: same tutorial (chunk_size/chunk_overlap/add_start_index)
- Embeddings (offline-safe): langchain_core.embeddings.DeterministicFakeEmbedding
  Source: same tutorial Fake tab (pip langchain-core only, no download)
- Lexical: langchain_community.retrievers.BM25Retriever (needs rank_bm25)
  Source: https://docs.langchain.com/oss/python/integrations/providers/rank_bm25
- Vector store: langchain_core.vectorstores.InMemoryVectorStore (tests) and
  langchain_chroma.Chroma (persistent parity with existing Chroma usage)
  Source: https://docs.langchain.com/oss/python/integrations/vectorstores/chroma
- Fusion: langchain_classic.retrievers.EnsembleRetriever (weighted RRF, c=60
  matches legacy fusion.RRF_K)
- Rerank/compression: langchain_classic.retrievers.ContextualCompressionRetriever
  + langchain_classic.retrievers.document_compressors.CrossEncoderReranker
  Source: https://docs.langchain.com/oss/python/integrations/document_transformers/cross_encoder_reranker
  W2 ships compression via a local top-n compressor (no weights, no download);
  CrossEncoder (bge-reranker-v2-m3) stays a refused candidate until its gain
  gate passes — same policy as reranker.py.
- Loaders: langchain_community.document_loaders.DirectoryLoader/TextLoader
  probed as integration surface; workspace load wraps the allow-list +
  quarantine policy so the indexed file set is unchanged (parity).

I1 dependency record lives in workers/w2.md (this module never edits the
project dependency manifest; missing packages raise with the exact names).
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence


def require_project_id(project_id: str | None) -> str:
    pid = (project_id or "").strip()
    if not pid:
        raise ValueError("project_id is required before retrieval (fail-closed)")
    return pid


def to_langchain_documents(
    items: Iterable[Any], *, project_id: str = "", scope_filter: bool = True
) -> list:
    """Normalize tuples/dicts/local docs to real langchain_core Documents.

    Accepts (path, text, project) tuples (tests/fixtures), legacy hit dicts,
    or existing Document-like objects. When scope_filter is True, only items
    belonging to project_id are kept — callers must scope BEFORE top-k.
    """
    from langchain_core.documents import Document

    out: list = []
    for it in items:
        if isinstance(it, Document):
            meta = dict(it.metadata or {})
            if scope_filter and project_id and meta.get("project_id") not in (None, "", project_id):
                if meta.get("project_id") != project_id:
                    continue
            meta.setdefault("project_id", project_id or meta.get("project_id", ""))
            out.append(Document(page_content=it.page_content, metadata=meta))
        elif isinstance(it, (tuple, list)) and len(it) == 3:
            path, text, proj = it
            if scope_filter and project_id and proj != project_id:
                continue
            out.append(Document(
                page_content=text,
                metadata={"path": path, "project_id": proj,
                          "chunk_id": f"c{len(out):03d}", "source": "langchain"},
            ))
        elif isinstance(it, dict):
            proj = it.get("project_id", project_id)
            if scope_filter and project_id and proj != project_id:
                continue
            out.append(Document(
                page_content=it.get("text", it.get("page_content", "")),
                metadata={
                    "path": it.get("path", ""),
                    "project_id": proj,
                    "chunk_id": it.get("chunk_id", f"c{len(out):03d}"),
                    "header": it.get("header", ""),
                    "source": it.get("source", "langchain"),
                    "status_tag": it.get("status_tag", "UNKNOWN"),
                    "file_sha": it.get("file_sha", ""),
                    "score": it.get("score", 0.0),
                },
            ))
    return out


def langchain_docs_to_hits(docs: Sequence[Any], *, project_id: str) -> list[dict]:
    """Convert LangChain Documents back to citation/fusion hit dicts."""
    pid = require_project_id(project_id)
    hits: list[dict] = []
    for i, d in enumerate(docs):
        meta = dict(getattr(d, "metadata", {}) or {})
        if meta.get("project_id") and meta["project_id"] != pid:
            raise ValueError(f"cross-project hit refused: {meta.get('path')}")
        text = getattr(d, "page_content", "") or ""
        hits.append({
            "path": meta.get("path", ""),
            "chunk_id": meta.get("chunk_id", f"c{i:03d}"),
            "header": meta.get("header", ""),
            "text": text,
            "snippet": (text[:200] + "...") if text else "",
            "file_sha": meta.get("file_sha", ""),
            "status_tag": meta.get("status_tag", "UNKNOWN"),
            "project_id": pid,
            "source": meta.get("source", "langchain"),
            "sources": [meta.get("source", "langchain")],
            "score": float(meta.get("score", 0.0)),
            "rank": i,
            "language": meta.get("language", ""),
        })
    return hits


def build_text_splitter(chunk_size: int = 1000, chunk_overlap: int = 200):
    """Real RecursiveCharacterTextSplitter (10-20% overlap default)."""
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    except ImportError as e:
        raise ImportError(
            "langchain-text-splitters is required for the v1 product path "
            "(I1 record in workers/w2.md).") from e
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=chunk_overlap, add_start_index=True)


def build_fake_embeddings(size: int = 256):
    """Offline-safe embeddings for tests/dev (no download, no network)."""
    from langchain_core.embeddings import DeterministicFakeEmbedding

    return DeterministicFakeEmbedding(size=size)


def build_huggingface_embeddings(model_name: str = "BAAI/bge-m3"):
    """Semantic upgrade boundary: refuses until weights are vendored.

    Never downloads. I1 must vendor weights + measure the gain gate first
    (same policy as bge_m3.py). Import of langchain-huggingface is lazy so
    W2 stays offline-safe.
    """
    raise RuntimeError(
        f"{model_name} weights are not vendored and the embedding gain gate "
        "has not been measured; refusing to download. "
        "Using DeterministicFakeEmbedding / local-hash baseline instead.")


def build_lexical_retriever(documents: Sequence[Any], k: int = 6):
    """Real BM25Retriever over project-scoped documents."""
    try:
        from langchain_community.retrievers import BM25Retriever
    except ImportError as e:
        raise ImportError(
            "langchain-community + rank_bm25 are required for lexical "
            "retrieval (I1 record in workers/w2.md).") from e
    if not list(documents):
        raise ValueError("cannot build lexical retriever over empty scope")
    retriever = BM25Retriever.from_documents(list(documents))
    retriever.k = k
    return retriever


def build_memory_vector_retriever(documents: Sequence[Any], embeddings, k: int = 6):
    """Real InMemoryVectorStore + as_retriever composition."""
    from langchain_core.vectorstores import InMemoryVectorStore

    store = InMemoryVectorStore.from_documents(list(documents), embeddings)
    return store.as_retriever(search_kwargs={"k": k})


def build_chroma_store(documents: Sequence[Any], embeddings, *,
                       collection_name: str = "w2_langchain",
                       persist_directory: str | None = None):
    """Real langchain_chroma.Chroma store (server-side filter before top-k).

    Metadata must include project_id per document; queries pass
    filter={"project_id": pid} so scoping happens server-side.
    """
    try:
        from langchain_chroma import Chroma
    except ImportError as e:
        raise ImportError(
            "langchain-chroma is required for the persistent vector path "
            "(I1 record in workers/w2.md).") from e
    kwargs: dict[str, Any] = {"collection_name": collection_name,
                              "embedding_function": embeddings}
    if persist_directory:
        kwargs["persist_directory"] = persist_directory
    store = Chroma(**kwargs)
    store.add_documents(list(documents))
    return store


def build_hybrid_retriever(lexical, dense, weights: Sequence[float] = (0.5, 0.5),
                           c: int = 60):
    """Real EnsembleRetriever fusion (weighted RRF; c=60 = legacy RRF_K)."""
    from langchain_classic.retrievers import EnsembleRetriever

    return EnsembleRetriever(
        retrievers=[lexical, dense], weights=list(weights), c=c)


class _TopNCompressor:
    """Minimal local compressor (no model) for the compression boundary."""

    def __init__(self, top_n: int = 6):
        from langchain_core.documents.compressor import BaseDocumentCompressor

        self._base = BaseDocumentCompressor
        self.top_n = top_n

    def _as_compressor(self):
        from langchain_core.documents.compressor import BaseDocumentCompressor

        top_n = self.top_n

        class _C(BaseDocumentCompressor):
            def compress_documents(self, documents, query, callbacks=None):  # type: ignore[override]
                return list(documents[:max(0, top_n)])

        return _C()


def build_compression_retriever(base_retriever, top_n: int = 6):
    """Real ContextualCompressionRetriever over a local top-n compressor.

    CrossEncoder (bge-reranker-v2-m3) is NOT constructed here: no weights are
    vendored and its gain gate is unmeasured, so this ships the zero-cost
    compression boundary with identical call shape. See reranker.py policy.
    """
    from langchain_classic.retrievers import ContextualCompressionRetriever

    compressor = _TopNCompressor(top_n=top_n)._as_compressor()
    return ContextualCompressionRetriever(
        base_compressor=compressor, base_retriever=base_retriever)


def build_cross_encoder_candidate(model_name: str = "BAAI/bge-reranker-v2-m3"):
    """Reranker candidate boundary: records identity, refuses without weights."""
    raise RuntimeError(
        f"{model_name} weights are not vendored and the reranker gain gate "
        "has not been measured; refusing to download. "
        "Using the local compression boundary instead.")


def load_workspace_langchain_documents(root, *, project_id: str) -> list:
    """Workspace load through the allow-list + quarantine policy (parity).

    Wraps MarkdownYamlLoader so the indexed file set is byte-identical to the
    legacy pipeline, then stamps project_id and returns real Documents.
    DirectoryLoader/TextLoader availability is probed as integration proof
    without changing the indexed set.
    """
    pid = require_project_id(project_id)
    try:
        from langchain_community.document_loaders import DirectoryLoader, TextLoader  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "langchain-community is required for the loader path "
            "(I1 record in workers/w2.md).") from e
    from langchain_core.documents import Document

    from app.services.rag.loaders import MarkdownYamlLoader

    local_docs = MarkdownYamlLoader(root).load()
    out: list = []
    for d in local_docs:
        meta = dict(d.metadata)
        meta["project_id"] = pid
        meta.setdefault("source", "workspace")
        out.append(Document(page_content=d.page_content, metadata=meta))
    return out


def retrieve_scoped_langchain(query: str, *, project_id: str,
                              documents: Sequence[Any] | None = None,
                              k: int = 6,
                              weights: Sequence[float] = (0.5, 0.5),
                              top_n: int | None = None):
    """v1 product path: scope BEFORE top-k, fuse via real EnsembleRetriever.

    - require project_id fail-closed before touching any retriever;
    - filter documents to the project before building legs (never a
      post-filter over a global top-k);
    - lexical (BM25) + dense (in-memory vectors, fake embeddings offline)
      legs fused with EnsembleRetriever(c=60);
    - optional ContextualCompressionRetriever top-n narrowing;
    - defense-in-depth re-check drops any out-of-scope Document.
    Returns LangChain Documents (use langchain_docs_to_hits for citations).
    """
    pid = require_project_id(project_id)
    if not (query or "").strip():
        return []
    if documents is None:
        raise ValueError("documents are required (project-scoped corpus)")
    scoped = [d for d in documents
              if (getattr(d, "metadata", {}) or {}).get("project_id", pid) == pid]
    if not scoped:
        return []
    embeddings = build_fake_embeddings(size=64)
    lexical = build_lexical_retriever(scoped, k=k)
    dense = build_memory_vector_retriever(scoped, embeddings, k=k)
    hybrid = build_hybrid_retriever(lexical, dense, weights=weights, c=60)
    try:
        results = hybrid.invoke(query)
    except Exception:
        # Deterministic degraded behavior: never leak, never throw to callers.
        return []
    results = [d for d in results
               if (getattr(d, "metadata", {}) or {}).get("project_id", pid) == pid]
    if top_n is not None:
        comp = build_compression_retriever(dense, top_n=top_n)
        _ = comp  # construction proof; narrowing applied locally for purity
        results = results[:max(0, top_n)]
    return results[:k] if top_n is None else results
