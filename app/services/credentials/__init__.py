"""Credential vault package (I-3). Refs in SQLite, ciphertext OS-backed.

Public API: store, get_secret_ref, resolve, revoke (from vault); backend
seam SecretBackend / DpapiFileBackend / default_backend (from store).
NOTE: the name `store` is intentionally the vault FUNCTION (frozen I-3
shape); import the seam module explicitly as
`app.services.credentials.store` when you need the backend module.
"""
from app.services.credentials.store import (
    SecretBackend,
    DpapiFileBackend,
    default_backend,
    set_default_backend,
)
from app.services.credentials.vault import (
    SCOPES,
    get_secret_ref,
    get_ref_row,
    export_status,
    resolve,
    revoke,
    store,
)

__all__ = [
    "SecretBackend",
    "DpapiFileBackend",
    "default_backend",
    "set_default_backend",
    "SCOPES",
    "store",
    "get_secret_ref",
    "resolve",
    "revoke",
    "get_ref_row",
    "export_status",
]
