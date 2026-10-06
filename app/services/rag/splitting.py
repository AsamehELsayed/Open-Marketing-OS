"""W2 splitters: legacy header chunking + language-aware recursive splitter.

- HeaderMarkdownSplitter: wraps indexing_service._chunk_markdown exactly
  (legacy parity; header-only behavior preserved).
- RecursiveLanguageSplitter: char-recursive splitter with chunk_size/
  chunk_overlap (LangChain RecursiveCharacterTextSplitter-compatible
  params). Language-aware only in separator priority: Arabic text favors
  newline/space boundaries that survive Arabic script + mixed
  Arabic/English; overlap is 10-20% default per skill guidance.
"""
from app.services.rag.indexing_service import _chunk_markdown
from app.services.rag.interfaces import Document


def _detect_arabic(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06FF" for ch in text)


class HeaderMarkdownSplitter:
    """Legacy H1-H3 splitter → Documents with header metadata."""

    def split_documents(self, docs: list[Document]) -> list[Document]:
        out: list[Document] = []
        for d in docs:
            for i, (header, body) in enumerate(_chunk_markdown(d.page_content)):
                if not body:
                    continue
                meta = dict(d.metadata)
                meta.update({"header": header, "chunk_id": f"c{i:03d}"})
                out.append(Document(page_content=body, metadata=meta))
        return out


class RecursiveLanguageSplitter:
    """Recursive char splitter, separator priority adapts to Arabic/mixed."""

    chunk_size: int
    chunk_overlap: int

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if not 0 <= chunk_overlap < chunk_size:
            raise ValueError("chunk_overlap must satisfy 0 <= overlap < chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def _separators(self, text: str) -> list[str]:
        if _detect_arabic(text):
            return ["\n\n", "\n", " ", ""]
        return ["\n\n", "\n", " ", ""]

    def split_text(self, text: str) -> list[str]:
        seps = self._separators(text)
        return self._split_recursive(text, seps)

    def _split_recursive(self, text: str, seps: list[str]) -> list[str]:
        if len(text) <= self.chunk_size:
            return [text] if text.strip() else []
        sep = next((s for s in seps if s and s in text), "")
        parts = text.split(sep) if sep else list(text)
        chunks, buf = [], ""
        for p in parts:
            piece = p if not sep else (p + sep if sep != "" else p)
            if len(buf) + len(piece) <= self.chunk_size:
                buf += piece
            else:
                if buf.strip():
                    chunks.append(buf.strip())
                # overlap: keep tail of previous buffer
                buf = (buf[-self.chunk_overlap:] if self.chunk_overlap else "") + piece
                while len(buf) > self.chunk_size:
                    chunks.append(buf[: self.chunk_size].strip())
                    buf = buf[self.chunk_size - self.chunk_overlap:]
        if buf.strip():
            chunks.append(buf.strip())
        # Merge tiny trailing chunk to avoid single-word tails.
        if len(chunks) > 1 and len(chunks[-1]) < self.chunk_overlap // 2:
            chunks[-2] = (chunks[-2] + " " + chunks.pop()).strip()
        return [c for c in chunks if c]

    def split_documents(self, docs: list[Document]) -> list[Document]:
        out: list[Document] = []
        for d in docs:
            lang = "ar" if _detect_arabic(d.page_content) else "en"
            for i, chunk in enumerate(self.split_text(d.page_content)):
                meta = dict(d.metadata)
                meta.update({"chunk_id": f"c{i:03d}", "language": lang})
                out.append(Document(page_content=chunk, metadata=meta))
        return out
