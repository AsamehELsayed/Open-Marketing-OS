"""DEV-007 W6 — MCP Gateway/Manager acceptance tests.

Covers: I-4 frozen interface conformance, default-deny discovery,
per-tool AND per-project allowlist matrix, frozen approval-mapping matrix
(READ/GREEN, WRITE/YELLOW, EXTERNAL_ACTION/YELLOW, DESTRUCTIVE/RED),
approval check BEFORE any transport call, vault-bound credentials,
timeouts/health, audit rows with zero secrets, and the w6.sql fragment.

Tool Registry / Integration Registry / vault are consumed ONLY through
local fakes built against the frozen record shapes (those modules are
built by W4/W5 in parallel).
"""

from __future__ import annotations
from app.tests.internal_fixtures import requires_internal_runs

import inspect
import json
import sqlite3
from pathlib import Path

import pytest

from app.services import mcp as mcp_pkg
from app.services.mcp import (
    APPROVAL_MATRIX,
    DEFAULT_TOOL_TIMEOUT_S,
    HEALTH_TIMEOUT_S,
    McpGateway,
)
from app.services.mcp.manager import (
    approval_for_category,
    classify_mcp_tool,
    scrub_mapping,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
W6_SQL = (
    REPO_ROOT / "development" / "runs" / "DEV-007" / "migrations" / "w6.sql"
)

PROJECT_A = "proj-alpha"
PROJECT_B = "proj-beta"
VAULT_REF = "vault://mcp/crm-token"
# Deliberately NOT matching sk-/gh_ redaction patterns: any leak of these
# strings into an audit row fails the test instead of being masked.
FAKE_VAULT_SECRET = "W6-vault-FakeToken-Value-Not-Real"
FAKE_ARG_SECRETS = {
    "api_key": "W6-FAKE-API-KEY-VALUE",
    "password": "W6-FAKE-PASSWORD-VALUE",
}

SERVER_TOOLS = [
    {"name": "list_contacts", "description": "List CRM contacts",
     "parameters": {"limit": {"type": "integer"}}},
    {"name": "create_campaign", "description": "Create a campaign draft",
     "parameters": {"name": {"type": "string"}}},
    {"name": "send_email", "description": "Send an email to a lead",
     "parameters": {"to": {"type": "string"}}},
    {"name": "delete_campaign", "description": "Permanently delete a campaign",
     "parameters": {"campaign_id": {"type": "string"}}},
]

EXPECTED_CATEGORIES = {
    "list_contacts": "READ",
    "create_campaign": "WRITE",
    "send_email": "EXTERNAL_ACTION",
    "delete_campaign": "DESTRUCTIVE",
}
EXPECTED_PERMISSION = {
    "READ": "green",
    "WRITE": "yellow",
    "EXTERNAL_ACTION": "yellow",
    "DESTRUCTIVE": "red",
}

TOOL_RECORD_KEYS = {
    "tool_id", "source_type", "description", "parameters", "side_effect",
    "project_scope_required", "auth_required", "credential_scope",
    "permission_level", "cost_type", "timeout_s", "retry_policy", "enabled",
}
INTEGRATION_RECORD_KEYS = {
    "integration_id", "capability", "provider", "scope", "status", "config",
    "secret_ref", "last_health",
}


# ---------------------------------------------------------------------------
# Local fakes (frozen shapes only; no real registry/vault imports)
# ---------------------------------------------------------------------------
class FakeTransport:
    def __init__(self, tools=None):
        self.tools = [dict(t) for t in (tools if tools is not None else SERVER_TOOLS)]
        self.calls: list[dict] = []
        self.discover_calls: list[str] = []
        self.health_calls: list[dict] = []
        self.fail_health = False
        self.fail_call_for: set[str] = set()

    def list_tools(self, endpoint: str) -> list[dict]:
        self.discover_calls.append(endpoint)
        return [dict(t) for t in self.tools]

    def call_tool(self, endpoint, tool, args, *, credential=None, timeout_s=None):
        self.calls.append({
            "endpoint": endpoint, "tool": tool, "args": dict(args),
            "credential": credential, "timeout_s": timeout_s,
        })
        if tool in self.fail_call_for:
            raise RuntimeError("transport boom")
        return {"result": f"{tool}-ok"}

    def health(self, endpoint, *, timeout_s=None):
        self.health_calls.append({"endpoint": endpoint, "timeout_s": timeout_s})
        if self.fail_health:
            raise TimeoutError("health probe timed out")
        return {"healthy": True, "status": "healthy", "version": "fake-1"}


class FakeVault:
    """vault store/resolve/revoke with vault:// refs."""

    def __init__(self, secrets=None):
        self.secrets = dict(secrets or {})
        self.resolve_calls: list[str] = []
        self.revoked: set[str] = set()

    def store(self, ref: str, value: str) -> None:
        if not ref.startswith("vault://"):
            raise ValueError("vault refs must start with vault://")
        self.secrets[ref] = value

    def resolve(self, ref: str) -> str:
        self.resolve_calls.append(ref)
        if ref in self.revoked or ref not in self.secrets:
            raise KeyError(f"unknown or revoked ref: {ref}")
        return self.secrets[ref]

    def revoke(self, ref: str) -> None:
        self.revoked.add(ref)
        self.secrets.pop(ref, None)


class FakeToolRegistry:
    def __init__(self):
        self.records: dict[str, dict] = {}

    def upsert(self, record: dict) -> None:
        missing = TOOL_RECORD_KEYS - set(record)
        if missing:
            raise AssertionError(f"ToolRecord missing keys: {sorted(missing)}")
        self.records[record["tool_id"]] = dict(record)


class FakeIntegrationRegistry:
    def __init__(self):
        self.records: dict[str, dict] = {}

    def upsert(self, record: dict) -> None:
        missing = INTEGRATION_RECORD_KEYS - set(record)
        if missing:
            raise AssertionError(f"IntegrationRecord missing keys: {sorted(missing)}")
        self.records[record["integration_id"]] = dict(record)


FIXED_TS = "2026-09-23T00:00:00+00:00"


class Harness:
    def __init__(self):
        self.transport = FakeTransport()
        self.vault = FakeVault({VAULT_REF: FAKE_VAULT_SECRET})
        self.tool_registry = FakeToolRegistry()
        self.integration_registry = FakeIntegrationRegistry()
        self.gw = McpGateway(
            tool_registry=self.tool_registry,
            integration_registry=self.integration_registry,
            vault=self.vault,
            transport=self.transport,
            clock=lambda: FIXED_TS,
        )
        self.gw.register_mcp_server(
            "crm", "https://mcp.example.local/crm", "project:demo",
            secret_ref=VAULT_REF,
        )
        self.tools = self.gw.discover_mcp_tools("crm")


def harness() -> Harness:
    return Harness()


# ---------------------------------------------------------------------------
# I-4 conformance
# ---------------------------------------------------------------------------
def test_i4_method_signatures_are_frozen():
    expected = {
        "register_mcp_server": ["self", "server_id", "endpoint", "scope",
                                "secret_ref"],
        "discover_mcp_tools": ["self", "server_id"],
        "set_mcp_tool_allowlist": ["self", "server_id", "project_id", "allowed"],
        "is_mcp_tool_allowed": ["self", "server_id", "project_id", "tool"],
        "execute_mcp_tool": ["self", "server_id", "project_id", "tool", "args"],
        "mcp_status": ["self", "project_id"],
    }
    for name, params in expected.items():
        sig = inspect.signature(getattr(McpGateway, name))
        assert list(sig.parameters) == params, name
    sig = inspect.signature(McpGateway.register_mcp_server)
    assert sig.parameters["secret_ref"].default is None


def test_i4_module_level_delegates_are_frozen():
    expected = {
        "register_mcp_server": ["server_id", "endpoint", "scope", "secret_ref"],
        "discover_mcp_tools": ["server_id"],
        "set_mcp_tool_allowlist": ["server_id", "project_id", "allowed"],
        "is_mcp_tool_allowed": ["server_id", "project_id", "tool"],
        "execute_mcp_tool": ["server_id", "project_id", "tool", "args"],
        "mcp_status": ["project_id"],
    }
    for name, params in expected.items():
        fn = getattr(mcp_pkg, name)
        assert callable(fn), name
        assert list(inspect.signature(fn).parameters) == params, name


def test_module_level_delegates_end_to_end():
    h = harness()
    mcp_pkg.configure_gateway(
        tool_registry=h.tool_registry,
        integration_registry=h.integration_registry,
        vault=h.vault,
        transport=h.transport,
        clock=lambda: FIXED_TS,
    )
    try:
        registered = mcp_pkg.register_mcp_server(
            "docs", "https://mcp.example.local/docs", "project:demo"
        )
        assert registered["connected"] is False
        before = [row for row in mcp_pkg.mcp_status(PROJECT_A)
                  if row["server_id"] == "docs"][0]
        assert before["last_health"] == "unknown"
        assert before["last_checked_at"] is None
        health = mcp_pkg.check_mcp_health("docs")
        assert health["connected"] is True
        assert health["status"] == "healthy"
        found = mcp_pkg.discover_mcp_tools("docs")
        assert {t["name"] for t in found} == {"list_contacts", "create_campaign",
                                              "send_email", "delete_campaign"}
        assert mcp_pkg.is_mcp_tool_allowed("docs", PROJECT_A, "list_contacts") is False
        mcp_pkg.set_mcp_tool_allowlist("docs", PROJECT_A, ["list_contacts"])
        assert mcp_pkg.is_mcp_tool_allowed("docs", PROJECT_A, "list_contacts") is True
        out = mcp_pkg.execute_mcp_tool(
            "docs", PROJECT_A, "list_contacts", {"limit": 5}
        )
        assert out["ok"] is True and out["status"] == "succeeded"
        rows = mcp_pkg.mcp_status(PROJECT_A)
        assert rows and any(r["server_id"] == "docs" for r in rows)
    finally:
        mcp_pkg.reset_gateway()


def test_discover_returns_exact_frozen_shape_and_classifies():
    h = harness()
    assert len(h.tools) == len(SERVER_TOOLS)
    for spec in h.tools:
        assert set(spec) == {"name", "description", "parameters"}
    got = {t["name"]: h.gw.tool_category("crm", t["name"]) for t in h.tools}
    assert got == EXPECTED_CATEGORIES


def test_register_validation_and_vault_only_secret_ref():
    h = harness()
    with pytest.raises(ValueError):
        h.gw.register_mcp_server("", "https://x", "scope")
    with pytest.raises(ValueError):
        h.gw.register_mcp_server("s", "https://x", "")
    # Raw secret as secret_ref must be rejected; vault:// only.
    with pytest.raises(ValueError, match="vault://"):
        h.gw.register_mcp_server("raw", "https://x", "scope",
                                 secret_ref="W6-raw-secret-not-vault")
    with pytest.raises(ValueError, match="vault://"):
        h.gw.register_mcp_server("raw2", "https://x", "scope",
                                 secret_ref=FAKE_ARG_SECRETS["api_key"])
    ok = h.gw.register_mcp_server("ok", "https://x", "scope", secret_ref=None)
    assert ok["connected"] is False
    status = [row for row in h.gw.mcp_status(PROJECT_A)
              if row["server_id"] == "ok"][0]
    assert status["last_health"] == "unknown"
    assert status["last_checked_at"] is None


# ---------------------------------------------------------------------------
# Default-deny (REQUIRED): discovery never enables anything
# ---------------------------------------------------------------------------
def test_default_deny_discovery_never_enables():
    h = harness()
    names = [t["name"] for t in h.tools]
    assert names, "discovery must return tools"
    for project in (PROJECT_A, PROJECT_B, "", "unknown-project"):
        for tool in names:
            assert h.gw.is_mcp_tool_allowed("crm", project, tool) is False
    # No transport call may happen for any project/tool without an allowlist.
    for tool in names:
        out = h.gw.execute_mcp_tool("crm", PROJECT_A, tool, {"x": 1})
        assert out["ok"] is False and out["status"] == "denied"
        assert h.gw.execute_mcp_tool("crm", PROJECT_A, tool, {"x": 1})["ok"] is False
    assert h.transport.calls == []
    # UI status shows empty allowlists.
    for row in h.gw.mcp_status(PROJECT_A):
        assert row["allowed_tools"] == []
    # Tool Registry mirror is default-deny: every bridged record disabled.
    assert h.tool_registry.records, "discover must bridge ToolRecords"
    for record in h.tool_registry.records.values():
        assert record["enabled"] is False
    # Re-discovery still never enables.
    h.gw.discover_mcp_tools("crm")
    assert all(
        h.gw.is_mcp_tool_allowed("crm", PROJECT_A, t) is False for t in names
    )
    assert h.transport.calls == []


# ---------------------------------------------------------------------------
# Classification + frozen approval matrix (pure manager)
# ---------------------------------------------------------------------------
def test_classification_covers_all_four_categories():
    cases = {
        "list_users": "READ",
        "read_document": "READ",
        "fetch_report": "READ",
        "create_post": "WRITE",
        "update_record": "WRITE",
        "send_email": "EXTERNAL_ACTION",
        "publish_page": "EXTERNAL_ACTION",
        "notify_team": "EXTERNAL_ACTION",
        "delete_file": "DESTRUCTIVE",
        "purge_cache": "DESTRUCTIVE",
        "revoke_token": "DESTRUCTIVE",
    }
    for name, expected in cases.items():
        assert classify_mcp_tool(name, "") == expected, name
    # Ambiguous name + neutral description fails closed to WRITE (not READ).
    assert classify_mcp_tool("frobnicate", "Does a thing") == "WRITE"
    # Description fallback: dangerous verbs in prose still classify.
    assert classify_mcp_tool("mystery_a", "Deletes everything permanently") == "DESTRUCTIVE"
    assert classify_mcp_tool("mystery_b", "Sends an email blast") == "EXTERNAL_ACTION"
    assert classify_mcp_tool("mystery_c", "Creates a new draft") == "WRITE"


def test_approval_mapping_matrix_is_frozen():
    assert APPROVAL_MATRIX == {
        "READ": "GREEN",
        "WRITE": "YELLOW",
        "EXTERNAL_ACTION": "YELLOW",
        "DESTRUCTIVE": "RED",
    }
    assert approval_for_category("READ") == "GREEN"
    assert approval_for_category("WRITE") == "YELLOW"
    assert approval_for_category("EXTERNAL_ACTION") == "YELLOW"
    assert approval_for_category("DESTRUCTIVE") == "RED"
    with pytest.raises(ValueError):
        approval_for_category("UNKNOWN")


# ---------------------------------------------------------------------------
# Allowlist matrix: per-tool AND per-project
# ---------------------------------------------------------------------------
def test_allowlist_matrix_per_tool_and_per_project():
    h = harness()
    # Empty until set; per-project isolation from the start.
    assert h.gw.is_mcp_tool_allowed("crm", PROJECT_A, "list_contacts") is False
    assert h.gw.is_mcp_tool_allowed("crm", PROJECT_B, "list_contacts") is False

    h.gw.set_mcp_tool_allowlist("crm", PROJECT_A, ["list_contacts"])
    assert h.gw.is_mcp_tool_allowed("crm", PROJECT_A, "list_contacts") is True
    # Per-tool: other tools stay denied on PROJECT_A.
    for other in ("create_campaign", "send_email", "delete_campaign"):
        assert h.gw.is_mcp_tool_allowed("crm", PROJECT_A, other) is False
    # Per-project: PROJECT_B untouched.
    for tool in EXPECTED_CATEGORIES:
        assert h.gw.is_mcp_tool_allowed("crm", PROJECT_B, tool) is False

    h.gw.set_mcp_tool_allowlist(
        "crm", PROJECT_B, ["list_contacts", "create_campaign"]
    )
    assert h.gw.is_mcp_tool_allowed("crm", PROJECT_B, "create_campaign") is True
    assert h.gw.is_mcp_tool_allowed("crm", PROJECT_A, "create_campaign") is False
    # Replacing an allowlist revokes previously allowed tools.
    h.gw.set_mcp_tool_allowlist("crm", PROJECT_A, [])
    assert h.gw.is_mcp_tool_allowed("crm", PROJECT_A, "list_contacts") is False

    # Unknown tools are rejected (must discover first).
    with pytest.raises(ValueError, match="unknown MCP tools"):
        h.gw.set_mcp_tool_allowlist("crm", PROJECT_A, ["not_discovered_tool"])
    # Unknown server / empty project fail closed.
    with pytest.raises(ValueError):
        h.gw.set_mcp_tool_allowlist("nope", PROJECT_A, [])
    with pytest.raises(ValueError):
        h.gw.set_mcp_tool_allowlist("crm", "", [])
    assert h.gw.is_mcp_tool_allowed("nope", PROJECT_A, "list_contacts") is False
    assert h.gw.is_mcp_tool_allowed("crm", PROJECT_A, "") is False


def test_execute_gated_by_allowlist_before_transport():
    h = harness()
    h.gw.set_mcp_tool_allowlist("crm", PROJECT_A, ["list_contacts"])
    # Not allowlisted on this project -> denied, no call.
    out = h.gw.execute_mcp_tool("crm", PROJECT_A, "create_campaign", {"name": "x"})
    assert out["ok"] is False and out["status"] == "denied"
    assert h.transport.calls == []
    # Allowlisted + GREEN -> executes exactly once.
    ok = h.gw.execute_mcp_tool("crm", PROJECT_A, "list_contacts", {"limit": 3})
    assert ok["ok"] is True and ok["status"] == "succeeded"
    assert len(h.transport.calls) == 1
    assert h.transport.calls[0]["tool"] == "list_contacts"
    # Wrong project still denied even though another project allows it.
    out_b = h.gw.execute_mcp_tool("crm", PROJECT_B, "list_contacts", {})
    assert out_b["ok"] is False and out_b["status"] == "denied"
    assert len(h.transport.calls) == 1
    # Fail-closed edge cases still return dicts (no raise).
    assert h.gw.execute_mcp_tool("ghost", PROJECT_A, "list_contacts", {})["ok"] is False
    assert h.gw.execute_mcp_tool("crm", "", "list_contacts", {})["ok"] is False
    assert h.gw.execute_mcp_tool("crm", PROJECT_A, "no_such_tool", {})["ok"] is False
    assert h.gw.execute_mcp_tool("crm", PROJECT_A, "list_contacts", "not-a-dict")["ok"] is False
    assert len(h.transport.calls) == 1


# ---------------------------------------------------------------------------
# Approval mapping on execute: GREEN autonomous, YELLOW/RED gated BEFORE call
# ---------------------------------------------------------------------------
def test_approval_matrix_on_execute_checks_before_any_call():
    h = harness()
    all_tools = list(EXPECTED_CATEGORIES)
    h.gw.set_mcp_tool_allowlist("crm", PROJECT_A, all_tools)

    # READ -> GREEN: autonomous, no approval needed.
    green = h.gw.execute_mcp_tool("crm", PROJECT_A, "list_contacts", {})
    assert green["ok"] is True and h.transport.calls[-1]["tool"] == "list_contacts"
    calls_after_green = len(h.transport.calls)

    # WRITE + EXTERNAL_ACTION -> YELLOW; DESTRUCTIVE -> RED: all gated.
    for tool, level in (("create_campaign", "YELLOW"),
                        ("send_email", "YELLOW"),
                        ("delete_campaign", "RED")):
        out = h.gw.execute_mcp_tool("crm", PROJECT_A, tool, {"id": "1"})
        assert out["ok"] is False, tool
        assert out["status"] == "approval_required", tool
        assert out["approval_required"] is True, tool
        assert out["approval_level"] == level, tool
        assert out["category"] == EXPECTED_CATEGORIES[tool], tool
    # NOTHING was transported for gated tools.
    assert len(h.transport.calls) == calls_after_green
    assert [c["tool"] for c in h.transport.calls] == ["list_contacts"]

    # Grant approvals one by one -> each now executes.
    for tool in ("create_campaign", "send_email", "delete_campaign"):
        h.gw.grant_approval("crm", PROJECT_A, tool)
        out = h.gw.execute_mcp_tool("crm", PROJECT_A, tool, {"id": "1"})
        assert out["ok"] is True, tool
    assert len(h.transport.calls) == calls_after_green + 3

    # Revoke -> gated again, no new transport call.
    h.gw.revoke_approval("crm", PROJECT_A, "delete_campaign")
    out = h.gw.execute_mcp_tool("crm", PROJECT_A, "delete_campaign", {"id": "1"})
    assert out["status"] == "approval_required"
    assert out["approval_level"] == "RED"
    assert len(h.transport.calls) == calls_after_green + 3

    # Approval is per-project: grant on B does not ungate A.
    h.gw.grant_approval("crm", PROJECT_B, "delete_campaign")
    h.gw.set_mcp_tool_allowlist("crm", PROJECT_B, ["delete_campaign"])
    h.gw.revoke_approval("crm", PROJECT_A, "delete_campaign")  # keep A clean
    h.gw.grant_approval("crm", PROJECT_A, "delete_campaign")
    assert h.gw.is_mcp_tool_allowed("crm", PROJECT_B, "delete_campaign") is True
    # (approval keys are (server, project, tool) — B's grant never mutates A)
    h.gw.revoke_approval("crm", PROJECT_B, "delete_campaign")
    out_a = h.gw.execute_mcp_tool("crm", PROJECT_A, "delete_campaign", {})
    assert out_a["ok"] is True  # A still holds its own grant
    count_before = len(h.transport.calls)
    h.gw.revoke_approval("crm", PROJECT_A, "delete_campaign")
    out_a2 = h.gw.execute_mcp_tool("crm", PROJECT_A, "delete_campaign", {})
    assert out_a2["approval_required"] is True
    assert len(h.transport.calls) == count_before


def test_grant_approval_requires_known_tool():
    h = harness()
    with pytest.raises(ValueError):
        h.gw.grant_approval("crm", PROJECT_A, "never_discovered")
    with pytest.raises(ValueError):
        h.gw.grant_approval("ghost", PROJECT_A, "list_contacts")


# ---------------------------------------------------------------------------
# Vault-bound credentials: resolved only at call time, never audited
# ---------------------------------------------------------------------------
def test_vault_credential_resolved_at_call_time_only():
    h = harness()
    h.gw.set_mcp_tool_allowlist("crm", PROJECT_A, ["list_contacts"])
    assert h.vault.resolve_calls == []
    out = h.gw.execute_mcp_tool("crm", PROJECT_A, "list_contacts", {"limit": 1})
    assert out["ok"] is True
    assert h.vault.resolve_calls == [VAULT_REF]
    assert h.transport.calls[0]["credential"] == FAKE_VAULT_SECRET
    # Secret never appears in status rows or audit rows.
    for row in h.gw.mcp_status(PROJECT_A):
        assert FAKE_VAULT_SECRET not in json.dumps(row, ensure_ascii=False)
        if row["server_id"] == "crm":
            assert row["has_credential"] is True
    _assert_audit_has_no_secrets(h.gw)

    # Revoked/missing ref fails closed as a dict; no transport call.
    h.vault.revoke(VAULT_REF)
    count = len(h.transport.calls)
    out2 = h.gw.execute_mcp_tool("crm", PROJECT_A, "list_contacts", {})
    assert out2["ok"] is False and out2["status"] == "failed"
    assert len(h.transport.calls) == count
    _assert_audit_has_no_secrets(h.gw)


def test_server_without_secret_ref_never_resolves_vault():
    h = harness()
    h.gw.register_mcp_server("anon", "https://mcp.example.local/anon", "project:demo")
    h.gw.discover_mcp_tools("anon")
    h.gw.set_mcp_tool_allowlist("anon", PROJECT_A, ["list_contacts"])
    out = h.gw.execute_mcp_tool("anon", PROJECT_A, "list_contacts", {})
    assert out["ok"] is True
    assert h.vault.resolve_calls == []  # no credential on this server
    assert h.transport.calls[-1]["credential"] is None


# ---------------------------------------------------------------------------
# Audit: connect/discover/allow/execute covered; ZERO secrets in rows
# ---------------------------------------------------------------------------
def _audit_secret_strings():
    return [
        FAKE_VAULT_SECRET,
        FAKE_ARG_SECRETS["api_key"],
        FAKE_ARG_SECRETS["password"],
    ]


def _assert_audit_has_no_secrets(gw: McpGateway) -> None:
    rows = gw.audit_rows()
    assert rows, "audit must record rows"
    blob = json.dumps(rows, ensure_ascii=False, default=str)
    for secret in _audit_secret_strings():
        assert secret not in blob, f"secret leaked into audit: {secret[:12]}..."
    for row in rows:
        # Values never stored: only argument KEY names.
        assert isinstance(row["arg_keys"], list)
        blob_row = json.dumps(row, ensure_ascii=False, default=str)
        for secret in _audit_secret_strings():
            assert secret not in blob_row


def test_audit_covers_connect_discover_allow_execute_without_secrets():
    h = harness()
    h.gw.set_mcp_tool_allowlist("crm", PROJECT_A, ["list_contacts", "create_campaign"])
    h.gw.grant_approval("crm", PROJECT_A, "create_campaign")
    h.gw.execute_mcp_tool(
        "crm", PROJECT_A, "list_contacts", dict(FAKE_ARG_SECRETS, contact_id="c-1")
    )
    h.gw.execute_mcp_tool("crm", PROJECT_A, "create_campaign", {"name": "n"})
    # Denials are audited too.
    h.gw.execute_mcp_tool("crm", PROJECT_B, "create_campaign", {"name": "n"})

    actions = [row["action"] for row in h.gw.audit_rows()]
    for required in ("connect", "discover", "allow", "execute"):
        assert required in actions, f"missing audit action: {required}"
    assert "approve" in actions

    # No secrets anywhere (args, vault value, raw secret attempts).
    _assert_audit_has_no_secrets(h.gw)
    # Argument VALUES are absent; only key names survive.
    rows_blob = json.dumps(h.gw.audit_rows(), ensure_ascii=False, default=str)
    assert "c-1" not in rows_blob, "argument values must not be persisted"
    assert "W6-FAKE" not in rows_blob

    # Every execute row carries ok/category context without secret detail.
    execute_rows = [r for r in h.gw.audit_rows() if r["action"] == "execute"]
    assert execute_rows
    for row in execute_rows:
        assert set(row) >= {"seq", "ts", "action", "server_id", "project_id",
                            "tool", "detail", "arg_keys", "ok"}
        assert row["detail"].get("approval_level") in (None, "GREEN", "YELLOW", "RED")

    # Scrubber redacts secret-looking keys even if a caller passes them.
    scrubbed = scrub_mapping({"token": "abc", "nested": {"api_key": "xyz"},
                              "note": "ok"})
    assert scrubbed["token"] == "[REDACTED]"
    assert scrubbed["nested"]["api_key"] == "[REDACTED]"
    assert scrubbed["note"] == "ok"


def test_failed_transport_is_audited_and_returns_dict():
    h = harness()
    h.gw.set_mcp_tool_allowlist("crm", PROJECT_A, ["list_contacts"])
    h.transport.fail_call_for.add("list_contacts")
    out = h.gw.execute_mcp_tool(
        "crm", PROJECT_A, "list_contacts", dict(FAKE_ARG_SECRETS)
    )
    assert out["ok"] is False and out["status"] == "failed"
    _assert_audit_has_no_secrets(h.gw)
    failed = [r for r in h.gw.audit_rows()
              if r["action"] == "execute" and r["ok"] is False]
    assert failed


# ---------------------------------------------------------------------------
# Timeouts, health, status
# ---------------------------------------------------------------------------
def test_execute_passes_default_tool_timeout():
    h = harness()
    h.gw.set_mcp_tool_allowlist("crm", PROJECT_A, ["list_contacts"])
    h.gw.execute_mcp_tool("crm", PROJECT_A, "list_contacts", {})
    assert h.transport.calls[0]["timeout_s"] == DEFAULT_TOOL_TIMEOUT_S
    assert DEFAULT_TOOL_TIMEOUT_S == 30


def test_health_probe_updates_status_with_short_timeout():
    h = harness()
    result = h.gw.check_mcp_health("crm")
    assert result["status"] == "healthy" and result["connected"] is True
    assert result["timeout_s"] == HEALTH_TIMEOUT_S == 5
    assert [c["timeout_s"] for c in h.transport.health_calls] == [HEALTH_TIMEOUT_S]

    h.transport.fail_health = True
    down = h.gw.check_mcp_health("crm")
    assert down["status"] == "unhealthy" and down["connected"] is False
    row = [r for r in h.gw.mcp_status(PROJECT_A) if r["server_id"] == "crm"][0]
    assert row["last_health"] == "unhealthy"
    assert row["last_checked_at"] == FIXED_TS
    actions = [r["action"] for r in h.gw.audit_rows()]
    assert actions.count("health") == 2
    _assert_audit_has_no_secrets(h.gw)

    # Unknown server health fails closed with ValueError.
    with pytest.raises(ValueError):
        h.gw.check_mcp_health("ghost")


def test_mcp_status_shape_for_ui():
    h = harness()
    h.gw.set_mcp_tool_allowlist("crm", PROJECT_A, ["list_contacts", "send_email"])
    rows = h.gw.mcp_status(PROJECT_A)
    assert isinstance(rows, list) and rows
    for row in rows:
        assert set(row) >= {"server_id", "endpoint", "scope", "connected",
                            "last_health", "last_checked_at", "tool_count",
                            "allowed_tools", "has_credential"}
        assert isinstance(row["allowed_tools"], list)
        assert isinstance(row["tool_count"], int)
    crm = [r for r in rows if r["server_id"] == "crm"][0]
    assert crm["allowed_tools"] == ["list_contacts", "send_email"]
    assert crm["tool_count"] == len(SERVER_TOOLS)
    assert crm["connected"] is False
    assert crm["last_health"] == "unknown"
    assert crm["last_checked_at"] is None
    assert crm["endpoint"] == "https://mcp.example.local/crm"
    # Another project sees only its own allowlist.
    rows_b = h.gw.mcp_status(PROJECT_B)
    crm_b = [r for r in rows_b if r["server_id"] == "crm"][0]
    assert crm_b["allowed_tools"] == []
    # status never exposes credential values
    assert FAKE_VAULT_SECRET not in json.dumps(rows, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Registry bridges (frozen shapes via local fakes)
# ---------------------------------------------------------------------------
def test_registry_bridges_use_frozen_shapes_and_default_deny():
    h = harness()
    # Integration mirror exists after register.
    assert "mcp:crm" in h.integration_registry.records
    irec = h.integration_registry.records["mcp:crm"]
    assert irec["integration_id"] == "mcp:crm"
    assert irec["capability"] == "mcp"
    assert irec["provider"] == "https://mcp.example.local/crm"
    assert irec["scope"] == "project:demo"
    assert irec["status"] == "not_configured"
    assert irec["secret_ref"] == VAULT_REF
    assert irec["last_health"] == "unknown"
    assert set(irec) == INTEGRATION_RECORD_KEYS

    # Tool mirror exists after discover, one per tool, ALL disabled.
    for name, category in EXPECTED_CATEGORIES.items():
        tid = f"mcp:crm:{name}"
        assert tid in h.tool_registry.records, tid
        rec = h.tool_registry.records[tid]
        assert set(rec) == TOOL_RECORD_KEYS
        assert rec["source_type"] == "mcp"
        assert rec["enabled"] is False  # default-deny
        assert rec["permission_level"] == EXPECTED_PERMISSION[category]
        assert rec["side_effect"] == category.lower()
        assert rec["project_scope_required"] is True
        assert rec["auth_required"] is True  # server has secret_ref
        assert rec["credential_scope"] == "project:demo"
        assert rec["timeout_s"] == DEFAULT_TOOL_TIMEOUT_S
        assert rec["retry_policy"] == {"max_retries": 0}
        assert rec["cost_type"] == "external_call"
        assert isinstance(rec["parameters"], dict)
        assert isinstance(rec["description"], str)


# ---------------------------------------------------------------------------
# w6.sql migration fragment
# ---------------------------------------------------------------------------
@requires_internal_runs
def test_w6_sql_fragment_applies_idempotently_and_fail_closes_project():
    assert W6_SQL.exists(), f"missing W6 migration fragment: {W6_SQL}"
    sql = W6_SQL.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS mcp_servers" in sql
    assert "CREATE TABLE IF NOT EXISTS mcp_tools_allowlist" in sql

    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(sql)
        conn.executescript(sql)  # idempotent: second apply must not fail
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"mcp_servers", "mcp_tools_allowlist"} <= tables
        indexes = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert any("mcp_allowlist" in i for i in indexes)

        conn.execute(
            "INSERT INTO mcp_servers (server_id, endpoint, scope) "
            "VALUES ('crm', 'https://mcp.example.local/crm', 'project:demo')"
        )
        # project_id is project-owned: NOT NULL, no silent default.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO mcp_tools_allowlist "
                "(server_id, project_id, tool_name) VALUES ('crm', NULL, 't')"
            )
        conn.execute(
            "INSERT INTO mcp_tools_allowlist "
            "(server_id, project_id, tool_name) VALUES ('crm', 'p1', 'list_contacts')"
        )
        # Allowlist is a set: duplicates rejected (replaces go through repos set()).
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO mcp_tools_allowlist "
                "(server_id, project_id, tool_name) VALUES ('crm', 'p1', 'list_contacts')"
            )
        # Default-deny at the row level: no rows == nothing allowed.
        allowed = conn.execute(
            "SELECT tool_name FROM mcp_tools_allowlist "
            "WHERE server_id=? AND project_id=?",
            ("crm", "p2"),
        ).fetchall()
        assert allowed == []
    finally:
        conn.close()


def test_migration_never_touches_secret_values_schema():
    """Fragment stores vault REFS only; no secret payload columns."""
    sql = W6_SQL.read_text(encoding="utf-8")
    assert "secret_ref" in sql
    lowered = sql.lower()
    assert "secret_value" not in lowered
    assert "password" not in lowered
    assert "api_key" not in lowered
