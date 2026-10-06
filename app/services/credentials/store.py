"""OS-backed secret ciphertext store — the cross-platform backend seam.

The vault (vault.py) holds SQLite refs only; secret bytes live behind this
seam. A backend implements put/get/delete of ciphertext keyed by
"<scope>/<uuid4hex>" (for example "installation/9f2c..."). Default backend
is the Windows DPAPI file store (DpapiFileBackend), bound to the current
Windows user. Future OS backends (macOS Keychain, Linux libsecret, Windows
Credential Manager) plug in here by implementing SecretBackend and being
passed to vault calls or installed via set_default_backend().

Windows Credential Manager (CredMan) evaluation — DECISION: DPAPI file
store, CredMan is not strictly better:
- Equivalent protection: CredMan credential blobs are DPAPI-protected
  under the hood for the calling user, so confidentiality matches this
  user-bound DPAPI file store. No gain in strength.
- Size limit: CredMan caps a credential blob at 2560 bytes
  (CRED_MAX_CREDENTIAL_BLOB_SIZE); DPAPI files have no such cap.
- API surface: CredMan needs CredWrite/CredRead/CredDelete via ctypes or
  pywin32 (no new dependency allowed here), plus a target-name namespace
  convention we would have to invent; revoke sweeps are per-target with no
  clean bulk enumeration story.
- Testability/portability: a file layout is trivially unit-testable with
  an injected fake and gives one seam for non-Windows backends later.
- CredMan remains a valid future backend behind this same seam if product
  ever wants credentials visible in the Windows Credential Manager UI.
"""
import ctypes
import os
import re
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Protocol, runtime_checkable

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DIR_ENV = "OMOS_CREDENTIALS_DIR"
KEY_RE = re.compile(r"^[a-z]+/[0-9a-f]{32}$")
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


def default_root() -> Path:
    """Directory for encrypted credential files.

    Order of precedence:
      1. ``OMOS_CREDENTIALS_DIR`` (the launcher always sets this),
      2. ``app.paths.credentials_dir()`` — the per-user data root in a frozen
         build, the repository root in a source checkout,
      3. legacy ``<repo>/data/credentials`` for callers that stub the module.

    DEV-008: the vault must never resolve inside the read-only install folder,
    because a Program Files write failure would surface as an unrecoverable
    "could not save your key" on a machine that is otherwise fine.
    """
    override = (os.environ.get(DEFAULT_DIR_ENV, "") or "").strip()
    if override:
        return Path(override)
    try:
        from app import paths

        return paths.credentials_dir()
    except Exception:
        return ROOT / "data" / "credentials"


def validate_key(key: str) -> str:
    """Backend keys are strictly '<scope>/<32-lowercase-hex>' — no path tricks."""
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise ValueError("backend key must be '<scope>/<32-hex>'")
    return key


@runtime_checkable
class SecretBackend(Protocol):
    """Seam every OS backend implements. put/get/delete operate on opaque bytes."""

    name: str

    def put(self, key: str, plaintext: bytes) -> None: ...

    def get(self, key: str) -> bytes | None: ...

    def delete(self, key: str) -> None: ...


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


if sys.platform == "win32":
    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), wintypes.LPWSTR, ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
else:
    _crypt32 = None
    _kernel32 = None


def _as_blob(data: bytes):
    buf = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))), buf


def _dpapi_protect(data: bytes) -> bytes:
    if _crypt32 is None:
        raise RuntimeError("DPAPI requires Windows (use a platform backend elsewhere)")
    src, _keep = _as_blob(data)
    dst = _DATA_BLOB()
    ok = _crypt32.CryptProtectData(
        ctypes.byref(src), None, None, None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(dst)
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptProtectData failed")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(dst.pbData, ctypes.c_void_p))


def _dpapi_unprotect(data: bytes) -> bytes:
    if _crypt32 is None:
        raise RuntimeError("DPAPI requires Windows (use a platform backend elsewhere)")
    src, _keep = _as_blob(data)
    dst = _DATA_BLOB()
    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(src), None, None, None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(dst)
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData failed")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(dst.pbData, ctypes.c_void_p))


class DpapiFileBackend:
    """Windows DPAPI-backed encrypted file store (default backend).

    One ciphertext file per secret at <root>/<scope>/<hex>.bin, encrypted
    with user-bound DPAPI (CRYPTPROTECT_UI_FORBIDDEN — never shows UI).
    Plaintext never written to disk. Writes are atomic (tmp + replace).
    """

    name = "dpapi_file"

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else default_root()

    def _path(self, key: str) -> Path:
        validate_key(key)
        scope, token = key.split("/", 1)
        return self.root / scope / f"{token}.bin"

    def put(self, key: str, plaintext: bytes) -> None:
        if not isinstance(plaintext, bytes) or not plaintext:
            raise ValueError("plaintext must be non-empty bytes")
        if _crypt32 is None:
            raise RuntimeError("DpapiFileBackend requires Windows (DPAPI)")
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = _dpapi_protect(plaintext)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(blob)
        os.replace(tmp, path)

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        if not path.exists():
            return None
        return _dpapi_unprotect(path.read_bytes())

    def contains(self, key: str) -> bool:
        """Report ciphertext presence without attempting decryption."""
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        path = self._path(key)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


_default: SecretBackend | None = None


def default_backend() -> SecretBackend:
    """Process default backend — DPAPI file store until set_default_backend() overrides."""
    global _default
    if _default is None:
        _default = DpapiFileBackend()
    return _default


def set_default_backend(backend: SecretBackend | None) -> None:
    """Install an alternate backend (tests: fake; future OS: keychain/libsecret)."""
    global _default
    _default = backend
