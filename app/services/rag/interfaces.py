"""W2 framework-compatible interfaces (no new dependency).

Shapes mirror LangChain core abstractions so a later LangChain swap is
mechanical, without importing langchain here (offline-safe, no new
dependency added):
- Document ~ langchain_core.documents.Document (page_content + metadata)
- BaseLoader ~ BaseDocumentLoader.load()
- BaseSplitter ~ TextSplitter.split_documents() (chunk_size/chunk_overlap)
- BaseEmbeddings ~ Embeddings.embed_documents/embed_query
- BaseRetriever ~ BaseRetriever.invoke/get_relevant_documents
- BaseReranker / BaseCompressor ~ optional rerank/compress boundaries

Sources: https://docs.langchain.com/oss/python/langchain/overview
(verified pattern names only; implementation is local, no network).
"""
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Document:
    page_content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseLoader(Protocol):
    def load(self) -> list[Document]: ...


class BaseSplitter(Protocol):
    chunk_size: int
    chunk_overlap: int

    def split_documents(self, docs: list[Document]) -> list[Document]: ...
    def split_text(self, text: str) -> list[str]: ...


class BaseEmbeddings(Protocol):
    id: str
    version: str
    dim: int

    def embed(self, text: str) -> list[float]: ...
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class BaseRetriever(Protocol):
    def invoke(self, query: str, *, project_id: str, k: int = 6) -> list[dict]: ...


class BaseReranker(Protocol):
    def rerank(self, query: str, hits: list[dict], *, top_n: int) -> list[dict]: ...


class BaseCompressor(Protocol):
    def compress(self, hits: list[dict], *, max_chars: int) -> list[dict]: ...
