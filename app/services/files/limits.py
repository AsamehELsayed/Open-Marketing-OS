"""Upload caps. Read dynamically at call time (module attributes) so tests
can monkeypatch; never inlined as literals in ingest/routes."""

MAX_DOCUMENT_BYTES = 25 * 1024 * 1024  # 25 MiB: pdf/docx/xlsx/pptx/txt/md/csv
MAX_IMAGE_BYTES = 15 * 1024 * 1024     # 15 MiB: png/jpeg/webp/gif
MAX_FILENAME_LEN = 180
MAX_EXTRACTED_CHARS = 2_000_000        # normalization bound before chunking


def absolute_max_bytes() -> int:
    return max(MAX_DOCUMENT_BYTES, MAX_IMAGE_BYTES)


def max_bytes_for(kind: str) -> int:
    if kind == "image":
        return MAX_IMAGE_BYTES
    return MAX_DOCUMENT_BYTES
