"""SHA-256 of raw upload bytes (never stored in SQLite — hash only)."""
import hashlib


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
