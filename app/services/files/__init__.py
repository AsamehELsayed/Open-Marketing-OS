"""DEV-007 W2 — project-scoped Files pipeline.

Bytes on disk (data/projects/<project_id>/files/), metadata in SQLite
(project_files), project-scoped chunk/FTS indexing for attach_scope=project.
Chat attachment scoping: attach_scope "turn" (ephemeral, never indexed) |
"project" (indexed under the owning project).

Stdlib-only extraction (no pypdf/langchain/openpyxl in requirements).
"""
from app.services.files.chunk_index import chat_file_summary
from app.services.files.ingest import (
    ATTACH_SCOPES,
    FileRecord,
    IngestError,
    ingest,
    tool_specs,
)

__all__ = [
    "ATTACH_SCOPES",
    "FileRecord",
    "IngestError",
    "chat_file_summary",
    "ingest",
    "tool_specs",
]
