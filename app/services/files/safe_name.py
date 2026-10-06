"""Filename validation + safe on-disk naming. Fail-closed on traversal."""
import re

from app.services.files import limits

# Executable/script extensions never accepted, regardless of sniffed MIME.
EXECUTABLE_EXTENSIONS = frozenset({
    "exe", "dll", "bat", "cmd", "ps1", "psm1", "psd1",
    "js", "mjs", "cjs", "vbs", "vbe", "wsf", "wsh",
    "com", "pif", "scr", "msi", "reg", "lnk", "hta", "jar",
    "sh", "csh", "ksh",
})

# Extension hint for text-family sniff refinement.
TEXT_SUBCLASS = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".text": "text/plain",
}

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._\-]+")
_DRIVE = re.compile(r"^[A-Za-z]:")


class UnsafeFilename(ValueError):
    """Rejected before any disk/DB touch."""


def reject_unsafe_filename(name: str | None) -> str:
    """Validate an upload filename. Returns the original name when safe.

    Rejects: empty/blank, null bytes, `..` segments, absolute paths
    (unix or drive-letter), control characters, over-length names.
    """
    if name is None:
        raise UnsafeFilename("empty filename")
    raw = name.strip()
    if not raw:
        raise UnsafeFilename("empty filename")
    if "\x00" in raw:
        raise UnsafeFilename("null byte in filename")
    if any(ord(c) < 32 for c in raw):
        raise UnsafeFilename("control character in filename")
    if len(raw) > limits.MAX_FILENAME_LEN:
        raise UnsafeFilename("filename too long")
    if raw.startswith("/") or raw.startswith("\\") or _DRIVE.match(raw):
        raise UnsafeFilename("absolute path in filename")
    segments = re.split(r"[/\\]", raw)
    if any(seg == ".." for seg in segments):
        raise UnsafeFilename("path traversal in filename")
    if any(seg == "" for seg in segments):
        raise UnsafeFilename("empty path segment in filename")
    return raw


def extension_of(name: str) -> str:
    """Lowercased final extension including dot ('' when none)."""
    base = re.split(r"[/\\]", name)[-1]
    if "." not in base:
        return ""
    return "." + base.rsplit(".", 1)[-1].lower()


def is_executable_extension(name: str) -> bool:
    ext = extension_of(name).lstrip(".")
    return ext in EXECUTABLE_EXTENSIONS


def sanitize_basename(name: str) -> str:
    """Last path component, safe charset only, bounded length, never empty."""
    base = re.split(r"[/\\]", name)[-1]
    base = base.replace("\x00", "")
    base = _SAFE_CHARS.sub("_", base).strip("._")
    if not base:
        base = "file"
    if len(base) > 80:
        stem, dot, ext = base.rpartition(".")
        if dot and 0 < len(ext) <= 8:
            base = stem[: 80 - len(ext) - 1] + "." + ext
        else:
            base = base[:80]
    return base


def build_safe_name(file_id: str, original_name: str, ext: str) -> str:
    """{file_id[:12]}_{sanitized_basename}{ext} — collision-proof, traversal-proof."""
    base = sanitize_basename(original_name)
    stem, dot, cur_ext = base.rpartition(".")
    body = stem if (dot and cur_ext) else base
    if len(ext) > 9 or not re.match(r"^\.[A-Za-z0-9]{1,8}$", ext):
        ext = ""
    if body and not body.lower().endswith(ext.lower()):
        return f"{file_id[:12]}_{body}{ext}"
    return f"{file_id[:12]}_{base}{ext}"


def extension_for(mime: str, original_name: str) -> str:
    table = {
        "application/pdf": ".pdf",
        "text/markdown": ".md",
        "text/csv": ".csv",
        "text/plain": ".txt",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }
    if mime in table:
        # text/plain keeps a sensible original subclass when present.
        if mime == "text/plain":
            orig = extension_of(original_name)
            if orig in TEXT_SUBCLASS and TEXT_SUBCLASS[orig] == "text/plain":
                return orig
        return table[mime]
    return ""
