"""Explicit Linux Secret Service backend. Never falls back to a file store.

Only the persistent default collection is accepted. We never create a
collection, unlock one, or select the temporary session collection implicitly.
The desktop login must provide an unlocked Secret Service over session D-Bus.
Attributes contain opaque references, never credential values/provider keys.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import sys
import uuid

from app.services.credentials.store import default_root, validate_key


class SecureStorageUnavailable(RuntimeError):
    """Sanitized actionable error; original D-Bus error is never exposed."""


class LinuxSecretServiceBackend:
    name = "linux_secret_service"

    def __init__(self, root: Path | str | None = None, *, service=None):
        # Canonical root + OS account form a stable installation identity without
        # writing plaintext identity files. Independent workspace roots cannot
        # alias each other's refs even when a database was copied between them.
        self.root = (Path(root) if root is not None else default_root()).resolve()
        identity = f"{getattr(os, 'getuid', lambda: 'test')()}\0{self.root}"
        self.installation = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        self._service = service  # dependency injection only, not backend discovery

    def _attributes(self, key):
        validate_key(key)
        scope, reference = key.split("/", 1)
        return {"application": "OpenMarketingOS", "schema": "1",
                "installation": self.installation, "scope": scope,
                "reference": reference}

    @contextmanager
    def _collection(self):
        connection = None
        try:
            service = self._service
            if service is None:
                try:
                    import secretstorage as service
                except ImportError:
                    raise SecureStorageUnavailable(
                        "Linux secure storage dependency is missing. Install the desktop SecretStorage dependency."
                    ) from None
                if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
                    raise SecureStorageUnavailable(
                        "Linux secure storage needs a desktop session D-Bus. Start OMOS inside your desktop login."
                    )
            connection = service.dbus_init()
            # Unlike get_default_collection this does not create a collection or
            # fall back to temporary session storage when no default exists.
            collection = service.get_collection_by_alias(connection, "default")
            if collection.is_locked():
                raise SecureStorageUnavailable(
                    "Linux secure storage is locked. Unlock your desktop login keyring and retry."
                )
            if getattr(collection, "collection_path", "") == "/org/freedesktop/secrets/collection/session":
                raise SecureStorageUnavailable("Linux secure storage requires a persistent default keyring, not temporary session storage.")
            if self._service is None:
                from secretstorage.util import open_session
            else:
                open_session = service.open_session
            session = open_session(connection)
            # SecretStorage can negotiate an unencrypted D-Bus session when a
            # provider rejects DH. Reject that negotiation BEFORE any secret
            # read or write; every returned Item inherits this checked session.
            if not session.encrypted:
                raise SecureStorageUnavailable("Linux secure storage requires an encrypted Secret Service session.")
            collection.session = session
            yield collection
        except SecureStorageUnavailable:
            raise
        except Exception:
            # D-Bus exception text may include secret bytes, paths or addresses.
            raise SecureStorageUnavailable(
                "Linux secure storage is unavailable or access was denied. Check your desktop Secret Service and unlocked default keyring."
            ) from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    @staticmethod
    def _items(collection, attributes):
        items = list(collection.search_items(attributes))
        # Fail closed if a provider returns an ambiguous namespace match.
        if len(items) > 1:
            raise SecureStorageUnavailable("Linux secure storage has duplicate credential references. Repair the desktop keyring before retrying.")
        for item in items:
            if item.is_locked():
                raise SecureStorageUnavailable("Linux secure storage is locked. Unlock your desktop login keyring and retry.")
            if item.get_attributes() != attributes:
                raise SecureStorageUnavailable("Linux secure storage returned an invalid credential namespace.")
        return items

    def put(self, key: str, plaintext: bytes) -> None:
        attributes = self._attributes(key)
        if not isinstance(plaintext, bytes) or not plaintext:
            raise ValueError("plaintext must be non-empty bytes")
        with self._collection() as collection:
            self._items(collection, attributes)
            collection.create_item("Open Marketing OS credential", attributes,
                                   plaintext, replace=True,
                                   content_type="application/octet-stream")

    def get(self, key: str) -> bytes | None:
        attributes = self._attributes(key)
        with self._collection() as collection:
            items = self._items(collection, attributes)
            if not items:
                return None
            value = items[0].get_secret()
            if not isinstance(value, bytes):
                raise SecureStorageUnavailable("Linux secure storage returned an invalid credential value.")
            return value

    def contains(self, key: str) -> bool:
        attributes = self._attributes(key)
        with self._collection() as collection:
            return bool(self._items(collection, attributes))

    def delete(self, key: str) -> None:
        attributes = self._attributes(key)
        with self._collection() as collection:
            for item in self._items(collection, attributes):
                item.delete()


def native_self_test() -> None:
    """Synthetic live binary round trip; never touches existing refs/secrets."""
    if not sys.platform.startswith("linux"):
        raise SecureStorageUnavailable("This native check requires Linux.")
    backend = LinuxSecretServiceBackend()
    key = "installation/" + uuid.uuid4().hex
    sample = b"OMOS-native-synthetic\x00\xff\xfe"
    try:
        backend.put(key, sample)
        if not backend.contains(key) or backend.get(key) != sample:
            raise SecureStorageUnavailable("Secret Service native binary round trip failed.")
        backend.delete(key)
        if backend.contains(key) or backend.get(key) is not None:
            raise SecureStorageUnavailable("Secret Service native deletion check failed.")
    finally:
        backend.delete(key)
    print("Secret Service native binary round trip and deletion: PASS")


if __name__ == "__main__":
    if sys.argv[1:] != ["--self-test"]:
        raise SystemExit("Usage: python -m app.services.credentials.linux_secret_service --self-test")
    try:
        native_self_test()
    except SecureStorageUnavailable as error:
        raise SystemExit(str(error)) from None
