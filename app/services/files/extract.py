"""Stdlib-only text extraction for the Files pipeline.

Coverage: PDF (zlib stream decode + Tj/TJ literal scan), DOCX/XLSX/PPTX
(zipfile + ElementTree), TXT/MD/CSV (UTF-8 decode), images (header dims
only — no vision). Office macro parts (vbaProject.bin) are never parsed;
their presence flips the status to "stripped" while original bytes stay
stored on disk.

Known honest limitations (documented in w2.NOTES.md):
- PDF: scans decompressed content streams for (literal) operands of Tj/TJ;
  does not decode embedded font encodings (CID/ToUnicode maps) — garbled
  text in exotic fonts may come back partially. Fixtures and common
  Latin-1/ASCII PDFs extract cleanly.
- XLSX: reads shared strings + inline strings; formula cells yield their
  cached value only when present as <v>; charts/macros ignored.
- PPTX: slide text (<a:t>) only; speaker notes ignored.
"""
import io
import re
import struct
import zipfile
import zlib
from dataclasses import dataclass, field
from xml.etree import ElementTree

from app.services.files import limits
from app.services.files.sniff import (
    IMAGE_MIMES, MIME_DOCX, MIME_PDF, MIME_PPTX, MIME_XLSX,
)

_PDF_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)
_PDF_LITERAL = re.compile(rb"\((?:\\.|[^\\()])*\)")
_OPS = (b"Tj", b"TJ", b"'", b'"')

_TEXT_MIMES = frozenset({"text/plain", "text/markdown", "text/csv"})

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_S_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


@dataclass
class ExtractionResult:
    status: str = "failed"          # ready | stripped | failed
    text: str = ""
    width: int | None = None
    height: int | None = None
    note: str = ""
    errors: list[str] = field(default_factory=list)


def normalize_text(text: str) -> str:
    """CRLF->LF, strip NULs, bound to limits.MAX_EXTRACTED_CHARS."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    cap = limits.MAX_EXTRACTED_CHARS
    if len(text) > cap:
        text = text[:cap]
    return text.strip()


# ---------- PDF ----------

def _pdf_unescape(lit: bytes) -> str:
    # literal includes surrounding parens
    inner = lit[1:-1]
    out = bytearray()
    i = 0
    n = len(inner)
    simple = {b"n": 10, b"r": 13, b"t": 9, b"b": 8, b"f": 12,
              b"(": 40, b")": 41, b"\\": 92}
    while i < n:
        c = inner[i:i + 1]
        if c == b"\\" and i + 1 < n:
            nxt = inner[i + 1:i + 2]
            if nxt in simple:
                out.append(simple[nxt])
                i += 2
                continue
            if nxt.isdigit():
                j = i + 1
                oct_digits = b""
                while j < n and inner[j:j + 1].isdigit() and len(oct_digits) < 3:
                    oct_digits += inner[j:j + 1]
                    j += 1
                out.append(int(oct_digits, 8) & 0xFF)
                i = j
                continue
            out += nxt
            i += 2
            continue
        out += c
        i += 1
    return out.decode("latin-1", errors="replace")


def extract_pdf(data: bytes) -> ExtractionResult:
    res = ExtractionResult()
    pieces: list[str] = []
    for m in _PDF_STREAM.finditer(data):
        blob = m.group(1)
        for candidate in (blob,):
            try:
                content = zlib.decompress(candidate)
            except zlib.error:
                content = candidate
            if not any(op in content for op in _OPS):
                continue
            for lit in _PDF_LITERAL.finditer(content):
                s = _pdf_unescape(lit.group(0))
                if s.strip():
                    pieces.append(s)
    text = normalize_text("\n".join(pieces))
    if text:
        res.status = "ready"
        res.text = text
    else:
        res.status = "failed"
        res.note = "no_extractable_text"
    return res


# ---------- Office (OOXML) ----------

def _office_has_macro(zf: zipfile.ZipFile) -> bool:
    for name in zf.namelist():
        if name.endswith("vbaProject.bin") or name.endswith("vbaData.xml"):
            return True
        if name.endswith(".bin") and "vba" in name.lower():
            return True
    return False


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def extract_docx(data: bytes) -> ExtractionResult:
    res = ExtractionResult()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            macro = _office_has_macro(zf)
            xml = zf.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile, OSError) as e:
        res.status = "failed"
        res.note = f"docx_read_error: {e}"
        return res
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as e:
        res.status = "failed"
        res.note = f"docx_xml_error: {e}"
        return res
    paragraphs: list[str] = []
    for p in root.iter(f"{_W_NS}p"):
        runs = [t.text or "" for t in p.iter(f"{_W_NS}t")]
        paragraphs.append("".join(runs))
    text = normalize_text("\n".join(paragraphs))
    if text:
        res.status = "stripped" if macro else "ready"
        res.text = text
        if macro:
            res.note = "office_macro_stripped"
    else:
        res.status = "failed"
        res.note = "no_extractable_text"
    return res


def extract_xlsx(data: bytes) -> ExtractionResult:
    res = ExtractionResult()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            macro = _office_has_macro(zf)
            shared: list[str] = []
            if "xl/sharedStrings.xml" in zf.namelist():
                sroot = ElementTree.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in sroot.iter(f"{_S_NS}si"):
                    shared.append("".join(
                        t.text or "" for t in si.iter(f"{_S_NS}t")))
            lines: list[str] = []
            sheet_names = sorted(
                n for n in zf.namelist()
                if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
            for sheet in sheet_names:
                wroot = ElementTree.fromstring(zf.read(sheet))
                for row in wroot.iter(f"{_S_NS}row"):
                    cells: list[str] = []
                    for c in row.iter(f"{_S_NS}c"):
                        ctype = c.get("t", "")
                        v = c.find(f"{_S_NS}v")
                        if ctype == "s" and v is not None and v.text is not None:
                            try:
                                cells.append(shared[int(v.text)])
                            except (ValueError, IndexError):
                                cells.append("")
                        elif ctype == "inlineStr":
                            cells.append("".join(
                                t.text or "" for t in c.iter(f"{_S_NS}t")))
                        elif v is not None and v.text is not None:
                            cells.append(v.text)
                        else:
                            cells.append("")
                    lines.append(",".join(cells))
            text = normalize_text("\n".join(lines))
    except (zipfile.BadZipFile, OSError, ElementTree.ParseError) as e:
        res.status = "failed"
        res.note = f"xlsx_read_error: {e}"
        return res
    if text:
        res.status = "stripped" if macro else "ready"
        res.text = text
        if macro:
            res.note = "office_macro_stripped"
    else:
        res.status = "failed"
        res.note = "no_extractable_text"
    return res


def extract_pptx(data: bytes) -> ExtractionResult:
    res = ExtractionResult()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            macro = _office_has_macro(zf)
            slides = sorted(
                n for n in zf.namelist()
                if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
            paragraphs: list[str] = []
            for slide in slides:
                root = ElementTree.fromstring(zf.read(slide))
                for t in root.iter(f"{_A_NS}t"):
                    if t.text:
                        paragraphs.append(t.text)
            text = normalize_text("\n".join(paragraphs))
    except (zipfile.BadZipFile, OSError, ElementTree.ParseError) as e:
        res.status = "failed"
        res.note = f"pptx_read_error: {e}"
        return res
    if text:
        res.status = "stripped" if macro else "ready"
        res.text = text
        if macro:
            res.note = "office_macro_stripped"
    else:
        res.status = "failed"
        res.note = "no_extractable_text"
    return res


# ---------- plain text ----------

def extract_text(data: bytes, mime: str) -> ExtractionResult:
    res = ExtractionResult()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        res.status = "failed"
        res.note = f"utf8_decode_error: {e}"
        return res
    text = normalize_text(text)
    if text:
        res.status = "ready"
        res.text = text
    else:
        res.status = "failed"
        res.note = "no_extractable_text"
    return res


# ---------- images (dims only; no vision in W2) ----------

def _png_dims(data: bytes) -> tuple[int | None, int | None]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None, None
    w, h = struct.unpack(">II", data[16:24])
    return int(w), int(h)


def _gif_dims(data: bytes) -> tuple[int | None, int | None]:
    if len(data) < 10 or data[:3] != b"GIF":
        return None, None
    w, h = struct.unpack("<HH", data[6:10])
    return int(w), int(h)


def _jpeg_dims(data: bytes) -> tuple[int | None, int | None]:
    i = 2
    n = len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if i + 4 > n:
            break
        seg_len = int.from_bytes(data[i + 2:i + 4], "big")
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if i + 9 <= n:
                h = int.from_bytes(data[i + 5:i + 7], "big")
                w = int.from_bytes(data[i + 7:i + 9], "big")
                return int(w), int(h)
            break
        i += 2 + seg_len
    return None, None


def _webp_dims(data: bytes) -> tuple[int | None, int | None]:
    if len(data) < 30:
        return None, None
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        w = int.from_bytes(data[24:27], "little") + 1
        h = int.from_bytes(data[27:30], "little") + 1
        return int(w), int(h)
    if chunk == b"VP8 " and len(data) >= 30:
        # lossy: start code 9D 01 2A then 14-bit width/height
        if data[23:26] == b"\x9d\x01\x2a":
            w = int.from_bytes(data[26:28], "little") & 0x3FFF
            h = int.from_bytes(data[28:30], "little") & 0x3FFF
            return int(w), int(h)
    if chunk == b"VP8L" and len(data) >= 25:
        bits = int.from_bytes(data[21:25], "little")
        w = (bits & 0x3FFF) + 1
        h = ((bits >> 14) & 0x3FFF) + 1
        return int(w), int(h)
    return None, None


def extract_image(data: bytes, mime: str) -> ExtractionResult:
    res = ExtractionResult()
    if mime == "image/png":
        w, h = _png_dims(data)
    elif mime == "image/gif":
        w, h = _gif_dims(data)
    elif mime == "image/jpeg":
        w, h = _jpeg_dims(data)
    elif mime == "image/webp":
        w, h = _webp_dims(data)
    else:
        w, h = None, None
    res.width, res.height = w, h
    if w and h:
        res.status = "ready"
        res.note = "image_dims_only_no_vision"
    else:
        res.status = "failed"
        res.note = "image_header_unreadable"
    return res


# ---------- dispatcher ----------

def extract(data: bytes, mime: str, original_name: str = "") -> ExtractionResult:
    """Extract per detected MIME. Never raises — failures become status=failed."""
    try:
        if mime == MIME_PDF:
            return extract_pdf(data)
        if mime == MIME_DOCX:
            return extract_docx(data)
        if mime == MIME_XLSX:
            return extract_xlsx(data)
        if mime == MIME_PPTX:
            return extract_pptx(data)
        if mime in _TEXT_MIMES:
            return extract_text(data, mime)
        if mime in IMAGE_MIMES:
            return extract_image(data, mime)
        res = ExtractionResult(status="failed")
        res.note = "unsupported_mime"
        return res
    except Exception as e:  # fail-closed: never crash ingest on bad bytes
        res = ExtractionResult(status="failed")
        res.note = f"extract_exception: {type(e).__name__}"
        return res
