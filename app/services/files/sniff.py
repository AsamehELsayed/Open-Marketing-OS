"""Magic-byte sniffing. Never trust the client-declared content type.

Detection order: executable magic (reject upstream) -> %PDF -> ZIP family
(docx/xlsx/pptx by member names) -> image signatures -> UTF-8 text
(subclassed by extension hint) -> unsupported.
"""
import io
import zipfile
from dataclasses import dataclass

from app.services.files.safe_name import TEXT_SUBCLASS, extension_of

MIME_PDF = "application/pdf"
MIME_DOCX = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
MIME_XLSX = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
MIME_PPTX = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)

EXECUTABLE_MIMES = frozenset({
    "application/x-dosexec", "application/x-elf", "text/x-shellscript",
})

ALLOWED_MIMES = frozenset({
    MIME_PDF, MIME_DOCX, MIME_XLSX, MIME_PPTX,
    "text/plain", "text/markdown", "text/csv",
    "image/png", "image/jpeg", "image/gif", "image/webp",
})

DOCUMENT_MIMES = frozenset({
    MIME_PDF, MIME_DOCX, MIME_XLSX, MIME_PPTX,
    "text/plain", "text/markdown", "text/csv",
})

IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})


@dataclass(frozen=True)
class SniffResult:
    mime: str
    kind: str  # document | image | data | other


def kind_for(mime: str) -> str:
    if mime in DOCUMENT_MIMES:
        return "document"
    if mime in IMAGE_MIMES:
        return "image"
    if mime.startswith("text/") or mime in (
        "application/json", "application/xml",
        "application/vnd.ms-excel", "application/octet-stream",
    ):
        return "data"
    return "other"


def _zip_mime(data: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
    except (zipfile.BadZipFile, OSError):
        return None
    joined = " ".join(names)
    if any(n.startswith("word/") for n in names) or "[Content_Types].xml" in names and "word/" in joined:
        if any(n.startswith("word/") for n in names):
            return MIME_DOCX
    if any(n.startswith("xl/") for n in names):
        return MIME_XLSX
    if any(n.startswith("ppt/") for n in names):
        return MIME_PPTX
    return None


def _is_text(data: bytes) -> bool:
    if b"\x00" in data[:8192]:
        return False
    try:
        data[:65536].decode("utf-8")
        return True
    except UnicodeDecodeError:
        # Allow a UTF-8 BOM only file.
        try:
            data.decode("utf-8-sig")
            return True
        except UnicodeDecodeError:
            return False


def sniff_bytes(data: bytes, original_name: str = "") -> SniffResult:
    """Detect MIME from content. Raises ValueError on executable payloads."""
    head = data[:16]
    if head[:2] == b"MZ":
        raise ValueError("executable_payload: MZ header")
    if head[:4] == b"\x7fELF":
        raise ValueError("executable_payload: ELF header")
    if head[:2] == b"#!":
        raise ValueError("executable_payload: shebang")
    if head[:2] == b"\xff\xd8":
        return SniffResult("image/jpeg", "image")
    if head[:3] == b"GIF":
        return SniffResult("image/gif", "image")
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return SniffResult("image/png", "image")
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return SniffResult("image/webp", "image")
    if head[:5] == b"%PDF-":
        return SniffResult(MIME_PDF, "document")
    if head[:4] == b"PK\x03\x04":
        mime = _zip_mime(data)
        if mime is None:
            raise ValueError("unsupported_type: unknown zip container")
        return SniffResult(mime, "document")
    if _is_text(data):
        ext = extension_of(original_name)
        mime = TEXT_SUBCLASS.get(ext, "text/plain")
        if mime not in ("text/plain", "text/markdown", "text/csv"):
            mime = "text/plain"
        return SniffResult(mime, "document")
    raise ValueError("unsupported_type: not recognizable as a supported format")


def claimed_compatible(claimed: str, detected: str) -> bool:
    """Client Content-Type vs sniffed MIME. Empty/octet-stream = no claim.

    Family-level tolerance: any text/* claim passes for any text/* detection
    (browsers disagree on markdown/csv text subtypes); images must match
    within image/*; binary formats must match exactly.
    """
    c = (claimed or "").split(";")[0].strip().lower()
    if not c or c in ("application/octet-stream", "application/x-download",
                      "binary/octet-stream"):
        return True
    if c == detected:
        return True
    if c.startswith("text/") and detected.startswith("text/"):
        return True
    if c.startswith("image/") and detected.startswith("image/"):
        return True
    # Common aliases.
    aliases = {
        "application/x-zip-compressed": "application/zip",
        "text/x-markdown": "text/markdown",
        "text/md": "text/markdown",
        # macro-enabled Office claims still are OOXML containers
        "application/vnd.ms-word.document.macroenabled.12": MIME_DOCX,
        "application/vnd.ms-excel.sheet.macroenabled.12": MIME_XLSX,
        "application/vnd.ms-powerpoint.presentation.macroenabled.12":
            MIME_PPTX,
        "application/msword": MIME_DOCX if detected == MIME_DOCX else c,
    }
    return aliases.get(c) == detected
