"""W2 loaders: allow-listed Markdown/YAML → framework Documents.

Wraps IndexingService allow-list policy (DEFAULT_ROOTS/EXCLUDED_DIRS,
secret quarantine) so the LangChain-style loader path indexes exactly the
same files as the legacy pipeline. No new roots, no network.
"""
from pathlib import Path

from app.services.rag import secrets as secret_scan
from app.services.rag.indexing_service import DEFAULT_ROOTS, EXCLUDED_DIRS
from app.services.rag.interfaces import Document


class MarkdownYamlLoader:
    """Load allow-listed workspace files as Documents (metadata: path)."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def load(self) -> list[Document]:
        docs: list[Document] = []
        for entry in DEFAULT_ROOTS:
            path = self.root / entry
            if path.is_file():
                docs.extend(self._load_file(path))
            elif path.is_dir():
                files: list[Path] = []
                files.extend(path.rglob("*.md"))
                files.extend(path.rglob("*.yaml"))
                for f in sorted(set(files)):
                    parts = set(f.relative_to(self.root).parts)
                    if parts & EXCLUDED_DIRS:
                        continue
                    if "simulation" in f.name:
                        continue
                    docs.extend(self._load_file(f))
        return docs

    def _load_file(self, path: Path) -> list[Document]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        if secret_scan.find_secrets(text):
            return []  # quarantined, never indexed (legacy R7 parity)
        rel = path.relative_to(self.root).as_posix()
        return [Document(page_content=text, metadata={"path": rel})]
