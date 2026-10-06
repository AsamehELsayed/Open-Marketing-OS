"""DEV-021 synthetic-only vault readiness and replacement regressions.

No provider client is constructed and no network API is invoked here.
"""
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.database.sqlite import connect
from app.services.credentials import vault
from app.services.credentials.store import DpapiFileBackend


class MemoryBackend:
    name = "memory"

    def __init__(self):
        self.data = {}
        self.fail_next_put = False

    def put(self, key, plaintext):
        if self.fail_next_put:
            self.fail_next_put = False
            raise OSError("synthetic backend failure; do not expose")
        self.data[key] = plaintext

    def get(self, key):
        return self.data.get(key)

    def delete(self, key):
        self.data.pop(key, None)


class RaisingReadBackend(MemoryBackend):
    def get(self, key):
        raise OSError("synthetic decrypt failure; secret-bearing detail omitted")


def _db(tmp_path):
    return connect(tmp_path / "dev021.db")


def _row(conn, ref):
    return conn.execute(
        "SELECT backend_key FROM credentials_refs WHERE secret_ref = ?", (ref,)
    ).fetchone()[0]


def test_credential_status_fixed_state_matrix(tmp_path):
    conn = _db(tmp_path)
    backend = MemoryBackend()
    label = "readiness"
    assert vault.credential_status("installation", label, conn=conn, backend=backend) == "not_configured"

    ref = vault.store("installation", label, "synthetic-secret-value", conn=conn, backend=backend)
    assert vault.credential_status("installation", label, conn=conn, backend=backend) == "configured"

    key = _row(conn, ref)
    backend.delete(key)
    assert vault.credential_status("installation", label, conn=conn, backend=backend) == "missing"

    backend.data[key] = b"synthetic-secret-value"
    assert vault.credential_status("installation", label, conn=conn, backend=RaisingReadBackend()) == "unreadable"
    conn.execute("UPDATE credentials_refs SET backend = 'different_backend' WHERE secret_ref = ?", (ref,))
    conn.commit()
    assert vault.credential_status("installation", label, conn=conn, backend=backend) == "unreadable"

    conn.execute("UPDATE credentials_refs SET backend = ? WHERE secret_ref = ?", (backend.name, ref))
    conn.commit()
    backend.data[key] = b" \t\n"
    assert vault.credential_status("installation", label, conn=conn, backend=backend) == "empty"

    assert "synthetic-secret-value" not in repr(vault.credential_status(
        "installation", label, conn=conn, backend=RaisingReadBackend()
    ))


def test_failed_replacement_preserves_previous_reference_and_value(tmp_path):
    conn = _db(tmp_path)
    backend = MemoryBackend()
    old_value = "synthetic-old-secret-4b1d"
    ref = vault.store("installation", "replace", old_value, conn=conn, backend=backend)
    backend.fail_next_put = True

    with pytest.raises(OSError):
        vault.store("installation", "replace", "synthetic-new-secret-7a2c", conn=conn, backend=backend)

    assert vault.get_secret_ref("installation", "replace", conn=conn) == ref
    assert vault.resolve(ref, conn=conn, backend=backend) == old_value
    assert vault.credential_status("installation", "replace", conn=conn, backend=backend) == "configured"
    rows = conn.execute(
        "SELECT COUNT(*) FROM credentials_refs WHERE status = 'active' AND label = 'replace'"
    ).fetchone()[0]
    assert rows == 1


def test_openrouter_client_initialization_receives_synthetic_vault_key_without_request(
    tmp_path, monkeypatch
):
    """Initialize only; fake SDK constructor proves key delivery, no dispatch."""
    conn = _db(tmp_path)
    backend = MemoryBackend()
    synthetic_key = "DEV021_SYNTHETIC_ADAPTER_KEY_83f6"
    ref = vault.store("installation", "adapter_probe", synthetic_key,
                      conn=conn, backend=backend)
    from app.services.llm import openrouter_provider

    resolved_values = []

    def resolve_synthetic_key():
        value = vault.resolve(ref, conn=conn, backend=backend)
        resolved_values.append(value)
        return value

    constructor_args = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            constructor_args.update(kwargs)

    monkeypatch.setattr(openrouter_provider, "get_key", resolve_synthetic_key)
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    provider = openrouter_provider.OpenRouterProvider()
    client = provider._open_client(3)

    assert isinstance(client, FakeOpenAI)
    assert resolved_values == [synthetic_key]
    assert constructor_args["api_key"] == synthetic_key
    assert constructor_args["base_url"] == openrouter_provider.BASE_URL
    # The fake client has no request methods; only initialization occurred.
    assert set(constructor_args).issuperset({"api_key", "base_url", "timeout", "max_retries"})


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_synthetic_dpapi_vault_roundtrip_survives_process_restart(tmp_path):
    """Persist only a synthetic value, then resolve it in a fresh process."""
    db_path = tmp_path / "restart.db"
    credential_root = tmp_path / "synthetic-credentials"
    value = "DEV021_SYNTHETIC_DPAPI_RESTART_91e4c8d6"
    conn = connect(db_path)
    backend = DpapiFileBackend(credential_root)
    ref = vault.store("installation", "restart_probe", value, conn=conn, backend=backend)
    conn.close()

    # A fresh process and backend instance verify persistence beyond process memory.
    script = (
        "import sys; "
        "from app.database.sqlite import connect; "
        "from app.services.credentials import vault; "
        "from app.services.credentials.store import DpapiFileBackend; "
        "c=connect(sys.argv[1]); "
        "v=vault.resolve(sys.argv[3], conn=c, backend=DpapiFileBackend(sys.argv[2])); "
        "assert v == 'DEV021_SYNTHETIC_DPAPI_RESTART_91e4c8d6' and bool(v.strip()); "
        "c.close()"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(db_path), str(credential_root), ref],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=os.environ.copy(),
    )
    assert result.returncode == 0, "synthetic DPAPI restart resolution failed"
    assert result.stdout == ""
    assert value.encode("utf-8") not in db_path.read_bytes()
    ciphertext_files = list(credential_root.rglob("*.bin"))
    assert len(ciphertext_files) == 1
    assert value.encode("utf-8") not in ciphertext_files[0].read_bytes()

