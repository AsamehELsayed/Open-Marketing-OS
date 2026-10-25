"""DEV-040: explicit fail-closed Secret Service and existing vault contracts."""
import sqlite3
from types import SimpleNamespace
import pytest
import importlib
from app.services.credentials import vault
store = importlib.import_module("app.services.credentials.store")
from app.services.credentials.linux_secret_service import LinuxSecretServiceBackend, SecureStorageUnavailable

class Connection:
    def __init__(self): self.closed = False
    def close(self): self.closed = True

class Item:
    def __init__(self, collection, attributes, secret):
        self.collection, self.attributes, self.secret = collection, dict(attributes), secret
        self.locked = False
    def is_locked(self): return self.locked
    def get_attributes(self): return self.attributes
    def get_secret(self): return self.secret
    def delete(self): self.collection.items.remove(self)

class Collection:
    def __init__(self): self.items, self.locked, self.fail = [], False, False
    def is_locked(self): return self.locked
    def search_items(self, attributes):
        if self.fail: raise RuntimeError('FAKESECRET_DO_NOT_LEAK')
        return [item for item in self.items if item.attributes == attributes]
    def create_item(self, label, attributes, secret, replace, content_type):
        assert replace and content_type == 'application/octet-stream'
        self.items[:] = [item for item in self.items if item.attributes != attributes]
        self.items.append(Item(self, attributes, secret))

class Service:
    def __init__(self): self.collection, self.connections = Collection(), []
    def open_session(self, connection): return SimpleNamespace(encrypted=True)
    def dbus_init(self):
        connection = Connection()
        self.connections.append(connection)
        return connection
    def get_collection_by_alias(self, connection, alias):
        assert alias == 'default'
        return self.collection

@pytest.fixture
def backend(tmp_path): return LinuxSecretServiceBackend(tmp_path, service=Service())

KEY = 'installation/' + 'a' * 32

def test_binary_roundtrip_replace_delete_and_missing(backend):
    assert not backend.contains(KEY) and backend.get(KEY) is None
    backend.put(KEY, b'bytes\x00\xff\xfe')
    assert backend.get(KEY) == b'bytes\x00\xff\xfe'
    backend.put(KEY, b'replacement')
    assert len(backend._service.collection.items) == 1
    assert backend.get(KEY) == b'replacement'
    backend.delete(KEY)
    backend.delete(KEY)
    assert backend.get(KEY) is None
    assert all(connection.closed for connection in backend._service.connections)

def test_root_namespace_stable_and_isolated(tmp_path):
    service = Service()
    one = LinuxSecretServiceBackend(tmp_path / 'one', service=service)
    same = LinuxSecretServiceBackend(tmp_path / 'one' / '.', service=service)
    two = LinuxSecretServiceBackend(tmp_path / 'two', service=service)
    one.put(KEY, b'one')
    assert same.get(KEY) == b'one'
    assert two.get(KEY) is None
    two.put(KEY, b'two')
    two.delete(KEY)
    assert one.get(KEY) == b'one'
    assert not tmp_path.joinpath('one').exists()

@pytest.mark.parametrize('operation', ['get', 'contains', 'delete', 'put'])
def test_locked_and_failure_sanitized(backend, operation):
    def run():
        return getattr(backend, operation)(KEY, b'value') if operation == 'put' else getattr(backend, operation)(KEY)
    backend._service.collection.locked = True
    with pytest.raises(SecureStorageUnavailable, match='locked'): run()
    backend._service.collection.locked = False
    backend._service.collection.fail = True
    with pytest.raises(SecureStorageUnavailable) as error: run()
    assert 'FAKESECRET' not in str(error.value)
    assert error.value.__suppress_context__
    assert all(c.closed for c in backend._service.connections)

def test_missing_bus_fails_without_files(tmp_path, monkeypatch):
    import sys, types
    monkeypatch.delenv('DBUS_SESSION_BUS_ADDRESS', raising=False)
    monkeypatch.setitem(sys.modules, 'secretstorage', types.SimpleNamespace())
    backend = LinuxSecretServiceBackend(tmp_path)
    with pytest.raises(SecureStorageUnavailable, match='session D-Bus'): backend.put(KEY, b'secret')
    assert list(tmp_path.iterdir()) == []

def test_missing_provider_and_locked_item(backend):
    backend.put(KEY, b'secret')
    backend._service.collection.items[0].locked = True
    with pytest.raises(SecureStorageUnavailable, match='locked'): backend.get(KEY)
    backend._service.get_collection_by_alias = lambda *args: (_ for _ in ()).throw(RuntimeError('FAKESECRET'))
    with pytest.raises(SecureStorageUnavailable) as error: backend.contains(KEY)
    assert 'FAKESECRET' not in str(error.value)

def test_duplicate_namespace_fails_closed(backend):
    backend.put(KEY, b'one')
    backend._service.collection.items.append(backend._service.collection.items[0])
    with pytest.raises(SecureStorageUnavailable, match='duplicate'): backend.get(KEY)

@pytest.mark.parametrize('key', ['../foo', 'project/' + 'f'*32 + '\n', 'installation/bad'])
def test_invalid_keys_rejected_before_bus(backend, key):
    with pytest.raises(ValueError): backend.get(key)
    assert backend._service.connections == []

def test_vault_scope_isolation_replacement_revoke_and_failed_save(backend):
    conn = sqlite3.connect(':memory:')
    refs = [vault.store('installation', 'provider', 'install', conn=conn, backend=backend),
            vault.store('project', 'provider', 'projectA', project_id='A', conn=conn, backend=backend),
            vault.store('project', 'provider', 'projectB', project_id='B', conn=conn, backend=backend),
            vault.store('user', 'provider', 'userA', user_id='A', conn=conn, backend=backend)]
    assert [vault.resolve(ref, conn=conn, backend=backend) for ref in refs] == ['install', 'projectA', 'projectB', 'userA']
    replacement = vault.store('installation', 'provider', 'new', conn=conn, backend=backend)
    with pytest.raises(ValueError, match='revoked'): vault.resolve(refs[0], conn=conn, backend=backend)
    backend._service.collection.locked = True
    with pytest.raises(SecureStorageUnavailable): vault.store('installation', 'provider', 'failed', conn=conn, backend=backend)
    assert vault.get_secret_ref('installation', 'provider', conn=conn) == replacement
    assert vault.credential_status('installation', 'provider', conn=conn, backend=backend) == 'unreadable'
    backend._service.collection.locked = False
    assert vault.resolve(replacement, conn=conn, backend=backend) == 'new'
    vault.revoke(replacement, conn=conn, backend=backend)
    assert not backend.contains(replacement.removeprefix('vault://'))
    assert len(backend._service.collection.items) == 3
    assert 'projectA' not in str([tuple(row) for row in conn.execute('SELECT * FROM credentials_refs')])

def test_os_selection_explicit(monkeypatch):
    monkeypatch.setattr(store, '_default', None)
    monkeypatch.setattr(store.sys, 'platform', 'linux')
    assert store.default_backend().name == 'linux_secret_service'
    monkeypatch.setattr(store, '_default', None)
    monkeypatch.setattr(store.sys, 'platform', 'win32')
    assert isinstance(store.default_backend(), store.DpapiFileBackend)
    monkeypatch.setattr(store, '_default', None)
    monkeypatch.setattr(store.sys, 'platform', 'unknown')
    with pytest.raises(RuntimeError, match='unsupported'): store.default_backend()


def test_unencrypted_session_and_temporary_collection_rejected(backend):
    backend._service.open_session = lambda connection: SimpleNamespace(encrypted=False)
    with pytest.raises(SecureStorageUnavailable, match='encrypted'): backend.put(KEY, b'never stored')
    assert backend._service.collection.items == []
    backend._service.collection.collection_path = '/org/freedesktop/secrets/collection/session'
    with pytest.raises(SecureStorageUnavailable, match='persistent'): backend.put(KEY, b'never stored')
    assert backend._service.collection.items == []


def test_failed_database_insert_removes_new_secret_preserves_old(backend):
    conn = sqlite3.connect(':memory:')
    previous = vault.store('installation', 'provider', 'previous', conn=conn, backend=backend)
    conn.execute("CREATE TRIGGER fail_new BEFORE INSERT ON credentials_refs BEGIN SELECT RAISE(ABORT, 'forced failure'); END")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError): vault.store('installation', 'provider', 'new', conn=conn, backend=backend)
    assert vault.get_secret_ref('installation', 'provider', conn=conn) == previous
    assert vault.resolve(previous, conn=conn, backend=backend) == 'previous'
    assert len(backend._service.collection.items) == 1
