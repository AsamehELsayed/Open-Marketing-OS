"""DEV-007 W9: independent hardening verification across 6 security domains.

Subprocess-per-domain runs against sibling worktrees (cwd isolation avoids
`app` package collision with this baseline tree). Static scans use pathlib.
Never imports sibling product code in-process.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

MARKER = "@@JSON@@"
HERE = pathlib.Path(__file__).resolve()
# Resolve DEV007 worktrees dir whether running from main repo or from w9 worktree
_repo_root = HERE.parents[2]
if (_repo_root / "development" / "worktrees" / "DEV-007").exists():
    DEV007 = _repo_root / "development" / "worktrees" / "DEV-007"
elif len(HERE.parents) >= 4 and HERE.parents[3].name == "DEV-007":
    DEV007 = HERE.parents[3]
else:
    DEV007 = _repo_root / "development" / "worktrees" / "DEV-007"

SIBLINGS = {
    name: (DEV007 / name if (DEV007 / name).exists() else _repo_root)
    for name in ("w1", "w2", "w3-impl", "w4", "w5", "w6", "w7", "w8", "w9")
}
W9 = SIBLINGS.get("w9", _repo_root)

_FORBIDDEN_OPENCODE = re.compile(
    r"opencode_service|opencode_provider|OpenCodeProvider|OpenCodeUnavailable"
    r"|opencode_runtime_available|opencode_contract",
    re.I,
)


def _env() -> dict:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def run_probe(tree: str, code: str, *, timeout: int = 90) -> dict:
    """Run a JSON-emitting probe in a sibling worktree; assert clean exit."""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(SIBLINGS[tree]),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_env(),
        timeout=timeout,
    )
    assert proc.returncode == 0, (
        f"{tree} probe failed rc={proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    for line in proc.stdout.splitlines():
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER):])
    raise AssertionError(f"{tree}: no JSON marker in stdout:\n{proc.stdout}")


def run_expect_module_not_found(tree: str, module: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", f"import importlib; importlib.import_module({module!r})"],
        cwd=str(SIBLINGS[tree]),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_env(),
        timeout=60,
    )
    assert proc.returncode != 0, f"{module} unexpectedly importable in {tree}"
    assert "ModuleNotFoundError" in proc.stderr, (
        f"expected ModuleNotFoundError for {module}, got:\n{proc.stderr}"
    )


# ---------------------------------------------------------------------------
# Domain 1 — Vault / secrets
# ---------------------------------------------------------------------------

VAULT_PROBE = r"""
import json, re, sqlite3, tempfile, os
os.environ["OMOS_CREDENTIALS_DIR"] = tempfile.mkdtemp(prefix="omos_w9_")
from app.services.credentials import vault
from app.services.integrations.registry import (
    IntegrationRecord, register_integration, resolve_provider,
    integration_status, IntegrationNotFound,
)
conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
out = {}
ref = vault.store("project", "k", "SUPER_SECRET_VALUE_123",
                  project_id="projA", conn=conn)
out["ref_fmt"] = bool(re.match(r"^vault://project/[0-9a-f]{32}$", ref))
out["resolve_ok"] = vault.resolve(ref, conn=conn) == "SUPER_SECRET_VALUE_123"
dump = str([dict(r) for r in
            conn.execute("SELECT * FROM credentials_refs").fetchall()])
out["plaintext_not_in_db"] = "SUPER_SECRET_VALUE_123" not in dump
st = vault.export_status(conn=conn)
blob = json.dumps(st)
out["status_presence_only"] = (
    "secret_ref" not in blob
    and "SUPER_SECRET" not in blob
    and '"value"' not in blob
)
try:
    vault.store("org", "k", "V", conn=conn)
    out["bad_scope"] = "ACCEPTED"
except ValueError:
    out["bad_scope"] = "rejected"
try:
    vault.store("project", "k2", "V", conn=conn)
    out["project_no_pid"] = "ACCEPTED"
except ValueError:
    out["project_no_pid"] = "rejected"
vault.revoke(ref, conn=conn)
try:
    vault.resolve(ref, conn=conn)
    out["revoked_resolve"] = "ACCEPTED"
except Exception as e:
    out["revoked_resolve"] = type(e).__name__
try:
    vault.resolve("vault://project/zzz", conn=conn)
    out["malformed"] = "ACCEPTED"
except ValueError:
    out["malformed"] = "rejected"
# integrations: forbidden config keys + fail-closed resolve
rec = IntegrationRecord("i1", "web.search", "apify", "project",
                        "connected", {"endpoint": "https://x"}, None, None)
register_integration(rec, project_id="projA", conn=conn)
out["resolve_a"] = resolve_provider("web.search", "projA", conn=conn)
bad = IntegrationRecord("i2", "web.search", "apify", "project",
                        "connected", {"token": "x"}, None, None)
try:
    register_integration(bad, project_id="projA", conn=conn)
    out["forbidden_cfg"] = "ACCEPTED"
except ValueError:
    out["forbidden_cfg"] = "rejected"
try:
    resolve_provider("web.search", None, conn=conn)
    out["int_no_pid"] = "ACCEPTED"
except ValueError:
    out["int_no_pid"] = "rejected"
try:
    resolve_provider("unknown.cap", "projA", conn=conn)
    out["unknown_cap"] = "ACCEPTED"
except Exception:
    out["unknown_cap"] = "rejected"
cards = integration_status("projA", conn=conn)
out["status_no_secret_ref"] = "secret_ref" not in json.dumps(cards)
print(MARKER_PH + json.dumps(out))
""".replace("MARKER_PH", repr(MARKER))


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI vault probes require Windows")
def test_domain1_vault_refs_scopes_and_presence_only():
    data = run_probe("w5", VAULT_PROBE)
    assert data["ref_fmt"] is True
    assert data["resolve_ok"] is True
    assert data["plaintext_not_in_db"] is True
    assert data["status_presence_only"] is True
    assert data["bad_scope"] == "rejected"
    assert data["project_no_pid"] == "rejected"
    assert data["revoked_resolve"] in ("ValueError", "KeyError")
    assert data["malformed"] == "rejected"
    assert data["resolve_a"] == "apify"
    assert data["forbidden_cfg"] == "rejected"
    assert data["int_no_pid"] == "rejected"
    assert data["unknown_cap"] == "rejected"
    assert data["status_no_secret_ref"] is True


# ---------------------------------------------------------------------------
# Domain 2 — Log scrub / secret redaction on outbound surfaces
# ---------------------------------------------------------------------------

W4_PROBE = r"""
import json, re, sqlite3
from app.services.tools import registry
out = {}
out["scrub_secret"] = registry._scrub("sk-ABCDEFGHIJKLMNOP")
out["scrub_plain"] = registry._scrub("ok-value")
out["scrub_long_capped"] = len(registry._scrub("x" * 500)) <= 200
ddl = registry.TOOL_RUNS_DDL
if isinstance(ddl, (list, tuple)):
    ddl = "".join(ddl)
cols = [c[0] for c in re.findall(r"([A-Za-z_]+)\s+(TEXT|INTEGER|REAL|BLOB)", ddl)]
out["ddl_cols"] = cols
out["no_arg_or_secret_cols"] = not any(
    c in cols for c in
    ("args", "arguments", "payload", "input", "secret", "value", "params")
)
insert = registry._TOOL_RUN_INSERT.lower()
out["insert_no_args"] = "arg" not in insert and "secret" not in insert
conn = sqlite3.connect(":memory:")
registry.ensure_tool_runs(conn)
runtime = [r[0] for r in
           conn.execute("SELECT name FROM pragma_table_info('tool_runs')")]
out["runtime_no_args"] = not any(
    c in runtime for c in
    ("args", "arguments", "payload", "input", "secret", "value", "params")
)
print(MARKER_PH + json.dumps(out))
""".replace("MARKER_PH", repr(MARKER))

W3_PROBE = r"""
import json
from app.services.vision import scrub
jwt = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
    "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c"
)
t = scrub.scrub_text(
    "hello vault://project/" + "a" * 32
    + " Bearer abc.def sk-ABCDEFGHIJKLMNOP password=hunter2 " + jwt
)
out = {
    "no_vault": "vault://" not in t,
    "no_bearer": "Bearer abc.def" not in t,
    "no_sk": "sk-ABCDEFGHIJKLMNOP" not in t,
    "no_pw": "hunter2" not in t,
    "no_jwt": jwt not in t,
    "has_redaction": "[REDACTED]" in t,
    # residual check: after scrubbing a raw secret, nothing credential-like remains
    "residual_clean": scrub.contains_credential_like(
        scrub.scrub_text("sk-ABCDEFGHIJKLMNOP password=hunter2")) is False,
}
print(MARKER_PH + json.dumps(out))
""".replace("MARKER_PH", repr(MARKER))


def test_domain2_telemetry_schema_has_no_argument_columns():
    data = run_probe("w4", W4_PROBE)
    assert data["scrub_secret"] == "redacted"
    assert data["scrub_plain"] == "ok-value"
    assert data["scrub_long_capped"] is True
    assert data["no_arg_or_secret_cols"] is True
    assert data["insert_no_args"] is True
    assert data["runtime_no_args"] is True


def test_domain2_vision_scrub_redacts_credentials():
    data = run_probe("w3-impl", W3_PROBE)
    assert data["no_vault"] is True
    assert data["no_bearer"] is True
    assert data["no_sk"] is True
    assert data["no_pw"] is True
    assert data["no_jwt"] is True
    assert data["has_redaction"] is True
    assert data["residual_clean"] is True


def test_domain2_rag_deny_list_patterns():
    """Baseline RAG deny-list (read-only; w9 tree is in-process safe)."""
    sys.path.insert(0, str(W9))
    try:
        from app.services.rag import secrets as rag_secrets
    finally:
        sys.path.pop(0)
    text = "sk-ABCDEFGHIJKLMNOP ghp_abcdefghij password=hunter2"
    assert rag_secrets.contains_secrets(text) is True
    kinds = rag_secrets.find_secrets(text)
    assert kinds, "expected pattern kinds"
    # kinds only — never the secret values themselves
    for kind in kinds:
        assert "hunter2" not in kind
        assert "ghp_abcdefghij" not in kind


def test_domain2_w8_templates_never_echo_secret_fields():
    """Static: W8 integrations cards are presence-only (no secret interpolation)."""
    templates = SIBLINGS["w8"] / "app" / "templates"
    offenders = []
    for tpl in templates.rglob("*.html"):
        text = tpl.read_text(encoding="utf-8", errors="replace")
        # password/token inputs must never carry a prefilled server value
        if re.search(
            r'type="password"[^>]*value="\{\{[^"]+\}\}"', text
        ):
            offenders.append(f"{tpl.name}: password input echoes a template value")
        if re.search(r"\{\{\s*[^}]*secret_ref[^}]*\}\}", text):
            offenders.append(f"{tpl.name}: interpolates secret_ref")
        if re.search(r"\{\{\s*[^}]*\b(secret|api_key|token_value)\b[^}]*\}\}", text):
            offenders.append(f"{tpl.name}: interpolates a secret-like field")
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# Domain 3 — Path traversal
# ---------------------------------------------------------------------------

TRAVERSAL_PROBE = r"""
import json, sqlite3, tempfile, pathlib
from app.services.files import repo, safe_name, sniff
from app.services.files.ingest import ingest, _validate_name, _require_project_id
conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
repo.ensure_schema(conn)
conn.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY)")
conn.execute("INSERT OR IGNORE INTO projects VALUES ('projA')")
conn.commit()
out = {}
tr = []
for name in ["../evil.txt", "..\\evil.txt", "/etc/passwd", "C:\\x.txt",
             "a\x00b.txt", "sub/../../x.txt", ""]:
    try:
        safe_name.reject_unsafe_filename(name)
        tr.append([name, "ACCEPTED"])
    except Exception:
        tr.append([name, "rejected"])
out["all_rejected"] = all(s == "rejected" for _, s in tr)
try:
    _validate_name("evil.exe")
    out["exe"] = "ACCEPTED"
except Exception:
    out["exe"] = "rejected"
try:
    _require_project_id("..")
    out["dotdot_pid"] = "ACCEPTED"
except Exception:
    out["dotdot_pid"] = "rejected"
try:
    sniff.sniff_bytes(b"MZ" + b"\x00" * 20, "x.txt")
    out["mz"] = "ACCEPTED"
except ValueError:
    out["mz"] = "rejected"
try:
    sniff.sniff_bytes(b"\x7fELF" + b"\x00" * 20, "x.bin")
    out["elf"] = "ACCEPTED"
except ValueError:
    out["elf"] = "rejected"
try:
    sniff.sniff_bytes(b"#!/bin/sh\ntrue\n", "x.txt")
    out["shebang"] = "ACCEPTED"
except ValueError:
    out["shebang"] = "rejected"
sn = safe_name.build_safe_name("abcdef0123456789", "../../etc/passwd", ".txt")
out["safe_name_clean"] = (
    ".." not in sn and "/" not in sn and "\\" not in sn and "\x00" not in sn
)
# full ingest: traversal name must fail before any disk write
root = pathlib.Path(tempfile.mkdtemp(prefix="omos_files_"))
conn.execute(
    "CREATE TABLE IF NOT EXISTS documents ("
    " id TEXT PRIMARY KEY, project_id TEXT NOT NULL DEFAULT 'starter',"
    " path TEXT NOT NULL UNIQUE, file_sha TEXT NOT NULL DEFAULT '',"
    " status_tag TEXT NOT NULL DEFAULT 'UNKNOWN',"
    " indexed_at TEXT NOT NULL)"
)
conn.execute(
    "CREATE TABLE IF NOT EXISTS chunks ("
    " id TEXT PRIMARY KEY, project_id TEXT NOT NULL DEFAULT 'starter',"
    " document_id TEXT NOT NULL, chunk_id TEXT NOT NULL,"
    " header TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '',"
    " token_est INTEGER NOT NULL DEFAULT 0,"
    " UNIQUE (document_id, chunk_id))"
)
conn.execute(
    "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
    " text, header, path, tokenize = 'porter')"
)
conn.commit()
try:
    ingest(conn, project_id="projA", filename="../evil.txt",
           data=b"hello", root=str(root))
    out["ingest_traversal"] = "ACCEPTED"
except Exception as e:
    out["ingest_traversal"] = type(e).__name__
out["no_outside_write"] = not any(
    "evil" in p.name for p in root.rglob("*") if p.is_file()
)
# sandbox path shape under root/data/projects (static via source not needed:
# a successful ingest lands only inside root/data/projects/<pid>/files/)
try:
    res = ingest(conn, project_id="projA", filename="ok.txt",
                 data=b"hello world", root=str(root))
    out["ingest_ok"] = True
    files_dir = root / "data" / "projects" / "projA" / "files"
    out["written_in_sandbox"] = files_dir.is_dir() and any(files_dir.iterdir())
    # rel_path from DB row (bytes never in SQLite; path is project-scoped)
    row = conn.execute(
        "SELECT rel_path FROM project_files WHERE project_id='projA'"
    ).fetchone()
    rel = row["rel_path"] if row else ""
    out["no_escape"] = ".." not in rel and not rel.startswith(("/", "\\"))
except Exception as e:
    out["ingest_ok"] = False
    out["ingest_err"] = type(e).__name__ + ":" + str(e)[:300]
print(MARKER_PH + json.dumps(out))
""".replace("MARKER_PH", repr(MARKER))


def test_domain3_path_traversal_rejected():
    data = run_probe("w2", TRAVERSAL_PROBE)
    assert data["all_rejected"] is True
    assert data["exe"] == "rejected"
    assert data["dotdot_pid"] == "rejected"
    assert data["mz"] == "rejected"
    assert data["elf"] == "rejected"
    assert data["shebang"] == "rejected"
    assert data["safe_name_clean"] is True
    assert data["ingest_traversal"] in (
        "UnsafeFilename", "IngestError", "rejected"
    )
    assert data["no_outside_write"] is True
    assert data.get("ingest_ok") is True, data.get("ingest_err", data)
    assert data["written_in_sandbox"] is True
    assert data["no_escape"] is True


# ---------------------------------------------------------------------------
# Domain 4 — Cross-project isolation
# ---------------------------------------------------------------------------

ISOLATION_W5_PROBE = r"""
import json, sqlite3, tempfile, os
os.environ["OMOS_CREDENTIALS_DIR"] = tempfile.mkdtemp(prefix="omos_w9_")
from app.services.credentials import vault
from app.services.integrations.registry import (
    IntegrationRecord, register_integration, resolve_provider,
    IntegrationNotFound,
)
out = {}
conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
ref = vault.store("project", "k", "V_A", project_id="projA", conn=conn)
out["vault_own"] = vault.get_secret_ref(
    "project", "k", project_id="projA", conn=conn) == ref
out["vault_cross_none"] = vault.get_secret_ref(
    "project", "k", project_id="projB", conn=conn) is None
rec = IntegrationRecord("i1", "web.search", "apify", "project",
                        "connected", {"endpoint": "https://x"}, None, None)
register_integration(rec, project_id="projA", conn=conn)
out["int_own"] = resolve_provider("web.search", "projA", conn=conn)
try:
    resolve_provider("web.search", "projB", conn=conn)
    out["int_cross"] = "ACCEPTED"
except IntegrationNotFound:
    out["int_cross"] = "IntegrationNotFound"
print(MARKER_PH + json.dumps(out))
""".replace("MARKER_PH", repr(MARKER))

ISOLATION_W2_PROBE = r"""
import json, sqlite3
from app.services.files import repo
conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
repo.ensure_schema(conn)
conn.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY)")
conn.execute("INSERT OR IGNORE INTO projects VALUES ('projA')")
conn.execute("INSERT OR IGNORE INTO projects VALUES ('projB')")
conn.commit()
conn.execute(
    "INSERT INTO project_files (file_id, project_id, original_name,"
    " safe_name, mime_detected, size, sha256, kind, rel_path, created_at)"
    " VALUES ('f1','projA','a.txt','f1_a.txt','text/plain',3,'00',"
    " 'document','data/projects/projA/files/f1_a.txt','2026-01-01')"
)
conn.commit()
out = {
    "file_own": bool(repo.get_file(conn, "f1", "projA")),
    "file_cross_none": repo.get_file(conn, "f1", "projB") is None,
    "file_cross_list_empty": repo.list_files(conn, "projB") == [],
}
try:
    repo.list_files(conn, "")
    out["file_blank"] = "no_raise"
except ValueError:
    out["file_blank"] = "rejected"
try:
    repo.get_file(conn, "f1", None)
    out["file_none"] = "no_raise"
except ValueError:
    out["file_none"] = "rejected"
print(MARKER_PH + json.dumps(out))
""".replace("MARKER_PH", repr(MARKER))

MCP_CROSS_PROBE = r"""
import json
from app.services.mcp.gateway import McpGateway

class FakeTransport:
    def list_tools(self, endpoint):
        return [{"name": "get_status", "description": "read", "parameters": {}}]
    def call_tool(self, endpoint, tool, args, credential=None, timeout_s=30):
        return {"ok": True, "result": "done"}

gw = McpGateway(transport=FakeTransport())
gw.register_mcp_server("s1", "http://example.invalid", "project")
gw.discover_mcp_tools("s1")
gw.set_mcp_tool_allowlist("s1", "p1", ["get_status"])
out = {
    "p1_allowed": gw.is_mcp_tool_allowed("s1", "p1", "get_status") is True,
    "p2_denied": gw.is_mcp_tool_allowed("s1", "p2", "get_status") is False,
    "p2_exec_denied": gw.execute_mcp_tool(
        "s1", "p2", "get_status", {"k": "v"}).get("status") == "denied",
    "blank_denied": gw.is_mcp_tool_allowed("s1", "", "get_status") is False,
}
print(MARKER_PH + json.dumps(out))
""".replace("MARKER_PH", repr(MARKER))


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI vault probes require Windows")
def test_domain4_cross_project_isolation_vault_and_integrations():
    data = run_probe("w5", ISOLATION_W5_PROBE)
    assert data["vault_own"] is True
    assert data["vault_cross_none"] is True
    assert data["int_own"] == "apify"
    assert data["int_cross"] == "IntegrationNotFound"


def test_domain4_cross_project_isolation_files():
    data = run_probe("w2", ISOLATION_W2_PROBE)
    assert data["file_own"] is True
    assert data["file_cross_none"] is True
    assert data["file_cross_list_empty"] is True
    assert data["file_blank"] == "rejected"
    assert data["file_none"] == "rejected"


def test_domain4_cross_project_isolation_mcp_allowlist():
    data = run_probe("w6", MCP_CROSS_PROBE)
    assert data["p1_allowed"] is True
    assert data["p2_denied"] is True
    assert data["p2_exec_denied"] is True
    assert data["blank_denied"] is True


# ---------------------------------------------------------------------------
# Domain 5 — MCP approval matrix + default-deny
# ---------------------------------------------------------------------------

MCP_PROBE = r"""
import json
from app.services.mcp.manager import (
    APPROVAL_MATRIX, classify_mcp_tool, approval_for_category,
    scrub_mapping, audit_arg_keys,
)
from app.services.mcp.gateway import McpGateway

class FakeTransport:
    def list_tools(self, endpoint):
        return [
            {"name": "get_status", "description": "read a status", "parameters": {}},
            {"name": "create_note", "description": "write a note", "parameters": {}},
            {"name": "send_email", "description": "send an email", "parameters": {}},
            {"name": "delete_record", "description": "delete a record", "parameters": {}},
        ]
    def call_tool(self, endpoint, tool, args, credential=None, timeout_s=30):
        return {"ok": True, "result": "done"}

out = {}
out["matrix"] = APPROVAL_MATRIX
out["approvals"] = {c: approval_for_category(c) for c in APPROVAL_MATRIX}
out["cats"] = {
    "get_status": classify_mcp_tool("get_status", ""),
    "create_note": classify_mcp_tool("create_note", ""),
    "send_email": classify_mcp_tool("send_email", ""),
    "delete_record": classify_mcp_tool("delete_record", ""),
    "ambiguous": classify_mcp_tool("frobnicate_thing", ""),
}
out["ambiguous_fails_closed"] = out["cats"]["ambiguous"] == "WRITE"
out["order_destructive"] = (
    classify_mcp_tool("delete_then_notify", "") == "DESTRUCTIVE"
)
out["order_external_over_write"] = (
    classify_mcp_tool("send_email", "") == "EXTERNAL_ACTION"
)
gw = McpGateway(transport=FakeTransport())
try:
    gw.register_mcp_server("s2", "http://x.invalid", "project",
                           secret_ref="sk-abcdef123456")
    out["raw_secret_rejected"] = False
except ValueError:
    out["raw_secret_rejected"] = True
gw.register_mcp_server("s1", "http://x.invalid", "project", secret_ref=None)
specs = gw.discover_mcp_tools("s1")
out["discovered"] = sorted(s["name"] for s in specs)
out["deny_before"] = gw.is_mcp_tool_allowed("s1", "p1", "get_status") is False
ex0 = gw.execute_mcp_tool("s1", "p1", "get_status", {"k": "v"})
out["exec_before"] = ex0.get("status")
gw.set_mcp_tool_allowlist("s1", "p1", ["get_status"])
out["allow_read"] = gw.is_mcp_tool_allowed("s1", "p1", "get_status") is True
out["deny_unlisted"] = (
    gw.is_mcp_tool_allowed("s1", "p1", "send_email") is False
)
ex_read = gw.execute_mcp_tool("s1", "p1", "get_status", {"k": "v"})
out["read_ok"] = ex_read.get("status") == "succeeded"
ex_denied = gw.execute_mcp_tool("s1", "p1", "send_email", {"k": "v"})
out["email_denied"] = ex_denied.get("status")
gw.set_mcp_tool_allowlist("s1", "p1", ["get_status", "send_email"])
ex_y = gw.execute_mcp_tool("s1", "p1", "send_email", {"token": "sk-XYZ"})
out["yellow_pre"] = [ex_y.get("status"), ex_y.get("approval_level")]
gw.grant_approval("s1", "p1", "send_email")
ex_y2 = gw.execute_mcp_tool("s1", "p1", "send_email", {"token": "sk-XYZ"})
out["yellow_post"] = ex_y2.get("status")
gw.set_mcp_tool_allowlist(
    "s1", "p1", ["get_status", "send_email", "delete_record"])
ex_r = gw.execute_mcp_tool("s1", "p1", "delete_record", {"k": "v"})
out["red_pre"] = [ex_r.get("status"), ex_r.get("approval_level")]
gw.grant_approval("s1", "p1", "delete_record")
ex_r2 = gw.execute_mcp_tool("s1", "p1", "delete_record", {"k": "v"})
out["red_post"] = ex_r2.get("status")
audit = gw.audit_rows()
blob = json.dumps(audit)
out["audit_no_secret_values"] = "sk-XYZ" not in blob
exec_rows = [r for r in audit if r.get("action") == "execute"]
out["arg_keys_only"] = all(
    set(r.get("arg_keys") or []) <= {"k", "token"} for r in exec_rows
)
# values never appear as JSON string values of arg-bearing fields
out["no_raw_arg_values"] = '"v"' not in blob and '"password"' not in blob
sm = scrub_mapping({"info": "sk-ABCDEFGHIJKLMNOP"})
out["scrub_mapping"] = (
    "redacted" in json.dumps(sm).lower()
    or "sk-ABCDEFGHIJKLMNOP" not in json.dumps(sm)
)
out["audit_arg_keys_fn"] = (
    audit_arg_keys({"password": "x", "b": 1}) == ["b", "password"]
)
# vault-bound registration accepted
try:
    gw.register_mcp_server(
        "s3", "http://x.invalid", "project",
        secret_ref="vault://project/" + "a" * 32)
    out["vault_ref_accepted"] = True
except ValueError:
    out["vault_ref_accepted"] = False
print(MARKER_PH + json.dumps(out))
""".replace("MARKER_PH", repr(MARKER))


def test_domain5_mcp_approval_matrix_and_default_deny():
    data = run_probe("w6", MCP_PROBE)
    assert data["matrix"] == {
        "READ": "GREEN",
        "WRITE": "YELLOW",
        "EXTERNAL_ACTION": "YELLOW",
        "DESTRUCTIVE": "RED",
    }
    assert data["approvals"] == {
        "READ": "GREEN",
        "WRITE": "YELLOW",
        "EXTERNAL_ACTION": "YELLOW",
        "DESTRUCTIVE": "RED",
    }
    assert data["cats"] == {
        "get_status": "READ",
        "create_note": "WRITE",
        "send_email": "EXTERNAL_ACTION",
        "delete_record": "DESTRUCTIVE",
        "ambiguous": "WRITE",
    }
    assert data["ambiguous_fails_closed"] is True
    assert data["order_destructive"] is True
    assert data["order_external_over_write"] is True
    assert data["raw_secret_rejected"] is True
    assert data["vault_ref_accepted"] is True
    assert data["deny_before"] is True
    assert data["exec_before"] in ("denied", "failed")
    assert data["allow_read"] is True
    assert data["deny_unlisted"] is True
    assert data["read_ok"] is True
    assert data["email_denied"] in ("denied", "approval_required")
    assert data["yellow_pre"][0] == "approval_required"
    assert data["yellow_pre"][1] == "YELLOW"
    assert data["yellow_post"] == "succeeded"
    assert data["red_pre"][0] == "approval_required"
    assert data["red_pre"][1] == "RED"
    assert data["red_post"] == "succeeded"
    assert data["audit_no_secret_values"] is True
    assert data["arg_keys_only"] is True
    assert data["no_raw_arg_values"] is True
    assert data["scrub_mapping"] is True
    assert data["audit_arg_keys_fn"] is True


# ---------------------------------------------------------------------------
# Domain 6 — opencode zero-dependency (W1 product code)
# ---------------------------------------------------------------------------

W1_ROUTER_PROBE = r"""
import json
from app.services.llm import router
out = {
    "no_opencode_in_valid": "opencode" not in router._VALID,
    "no_runtime_attr": not hasattr(router, "opencode_runtime_available"),
}
cands = router.resolve_candidates(
    "auto", ("openai", "deterministic"), openai_ok=False)
out["cands_no_openai_no_opencode"] = (
    cands == ["deterministic"] and "opencode" not in cands
)
cands2 = router.resolve_candidates(
    "auto", ("openai", "deterministic"), openai_ok=True)
out["cands_openai_no_opencode"] = (
    cands2 and cands2[0] == "openai" and "opencode" not in cands2
)
llm_pkg_names = list(getattr(
    __import__("app.services.llm", fromlist=["*"]), "__all__", []))
out["pkg_exports_clean"] = all("opencode" not in n.lower()
                               for n in llm_pkg_names)
print(MARKER_PH + json.dumps(out))
""".replace("MARKER_PH", repr(MARKER))


def test_domain6_w1_runtime_has_no_opencode_symbols():
    offenders = []
    app_root = SIBLINGS["w1"] / "app"
    for path in sorted(app_root.rglob("*.py")):
        if {"tests", "static", "templates"} & set(path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if _FORBIDDEN_OPENCODE.search(text):
            offenders.append(str(path.relative_to(SIBLINGS["w1"])))
    assert not offenders, f"opencode symbols in W1 runtime: {offenders}"


def test_domain6_w1_opencode_modules_not_importable():
    run_expect_module_not_found("w1", "app.services.llm.opencode_provider")
    run_expect_module_not_found("w1", "app.services.opencode_service")


def test_domain6_w1_router_has_no_opencode():
    data = run_probe("w1", W1_ROUTER_PROBE)
    assert data["no_opencode_in_valid"] is True
    assert data["no_runtime_attr"] is True
    assert data["cands_no_openai_no_opencode"] is True
    assert data["cands_openai_no_opencode"] is True
    assert data["pkg_exports_clean"] is True


def test_domain6_w1_product_tree_has_no_import_opencode_package():
    """No `import opencode` / `from opencode` anywhere in W1 product code."""
    offenders = []
    app_root = SIBLINGS["w1"] / "app"
    pat = re.compile(r"^\s*(?:import opencode\b|from opencode[.\s])", re.M)
    for path in sorted(app_root.rglob("*.py")):
        if {"tests", "static", "templates"} & set(path.parts):
            continue
        if pat.search(path.read_text(encoding="utf-8", errors="replace")):
            offenders.append(str(path.relative_to(SIBLINGS["w1"])))
    assert not offenders, offenders
