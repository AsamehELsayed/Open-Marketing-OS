"""Self-contained mandatory SDK acceptance: no private fixtures, credentials or skips."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

import mcp.client.stdio as sdk_stdio
from mcp.types import CallToolResult, TextContent
import pytest

from app.database.sqlite import connect
from app.services import campaign_deliverables as deliverables
from prototypes.marketing_mcp.demo import CAMPAIGN, COPY, PROJECT, approve, public_evidence, seed
from prototypes.marketing_mcp.host import DraftHost, SERVER_ID, canonical_json
from prototypes.marketing_mcp.transport import (
    ENDPOINT, SERVER, TOOL, FixtureTransport, decode_result, normalize_tools, offline_network,
)


@pytest.fixture
def process_receipts(monkeypatch):
    """Observe real SDK-created processes, never replace their protocol behavior."""
    original = sdk_stdio._create_platform_compatible_process
    processes = []

    async def observe(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append((process, kwargs))
        return process

    monkeypatch.setattr(sdk_stdio, "_create_platform_compatible_process", observe)
    yield processes
    assert all(p.returncode is not None for p, _ in processes), "SDK child leaked"


@pytest.fixture
def workspace(tmp_path):
    conn = connect(tmp_path / "fixture.sqlite")
    row = seed(conn)
    yield conn, row
    conn.close()


def host_for(workspace):
    conn, row = workspace
    host = DraftHost(conn, PROJECT)
    return host, row


def test_real_protocol_default_deny_approval_determinism_and_audit(workspace, process_receipts, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "private-environment-sentinel")
    host, row = host_for(workspace)
    assert host.tools[0]["parameters"]["required"] == ["draft"]
    assert host.tools[0]["name"] == TOOL
    assert not host.registry.get_tool(TOOL, PROJECT).enabled
    assert not host.gateway.is_mcp_tool_allowed(SERVER_ID, PROJECT, TOOL)
    assert not host.prepare(CAMPAIGN, row["id"], row["current_version"])["ok"]
    host.allow_fixture()
    assert not host.prepare(CAMPAIGN, row["id"], 1)["ok"]
    in_review = deliverables.transition(host.conn, PROJECT, CAMPAIGN, row["id"], 1, "IN_REVIEW")
    assert not host.prepare(CAMPAIGN, row["id"], in_review["current_version"])["ok"]
    row = deliverables.transition(host.conn, PROJECT, CAMPAIGN, row["id"], 2, "APPROVED")
    first = host.prepare(CAMPAIGN, row["id"], row["current_version"])
    second = host.prepare(CAMPAIGN, row["id"], row["current_version"])
    assert first["ok"] and canonical_json(first) == canonical_json(second)
    assert first["payload"]["publish"] is False
    assert first["payload"]["status"] == "draft"
    assert first["payload"]["text"] == COPY
    assert len(first["payload"]["idempotency_key"]) == 64
    assert host.transport.active_sessions == 0
    assert len(process_receipts) == 3
    for process, params in process_receipts:
        assert process.returncode == 0
        assert params["command"] == sys.executable
        assert params["args"] == ["-I", str(SERVER)]
        assert "OPENROUTER_API_KEY" not in params["env"]
        assert "PYTHONPATH" not in params["env"]
    assert host.transport.exchanges[-1]["methods"] == ["initialize", "tools/list", "tools/call"]
    assert host.transport.exchanges[-1]["protocol"]
    rows = [dict(r) for r in host.conn.execute("SELECT * FROM tool_runs")]
    assert len(rows) == 5
    assert len({r["tool_run_id"] for r in rows}) == 5
    audit = json.dumps({"tool_runs": rows, "gateway": host.gateway.audit_rows()})
    assert COPY not in audit and "private-environment-sentinel" not in audit
    assert not host.gateway._approvals  # temporary tool grants are revoked


@pytest.mark.parametrize("mode", ["stale", "edited", "cross_project", "forged_snapshot", "wrong_type", "wrong_platform", "bad_copy", "boolean_version"])
def test_exact_version_scope_and_content_denied_before_formatter(workspace, process_receipts, mode):
    host, row = host_for(workspace)
    row = approve(host.conn, row)
    host.allow_fixture()
    expected = row["current_version"]
    if mode == "stale":
        expected -= 1
    elif mode == "edited":
        row = deliverables.revise(host.conn, PROJECT, CAMPAIGN, row["id"], expected, {"content_md": "Unapproved edit"})
        expected = row["current_version"]
    elif mode == "cross_project":
        host.project_id = "fixture-beta"
    elif mode == "boolean_version":
        expected = True
    elif mode in {"wrong_type", "wrong_platform", "bad_copy"}:
        changes = {"wrong_type": {"type": "strategy_brief"},
                   "wrong_platform": {"platform": "instagram"},
                   "bad_copy": {"content_md": "## Final copy\nText without the required audit sections"}}[mode]
        row = deliverables.revise(host.conn, PROJECT, CAMPAIGN, row["id"], expected, changes)
        row = approve(host.conn, row)
        expected = row["current_version"]
    else:
        host.conn.execute("UPDATE campaign_deliverables SET title=? WHERE id=?", ("Tampered title", row["id"]))
        host.conn.commit()
    assert not host.prepare(CAMPAIGN, row["id"], expected)["ok"]
    assert len(process_receipts) == 1  # discovery only; no tools/call
    assert all("tools/call" not in e["methods"] for e in host.transport.exchanges)


def test_direct_registry_forged_approval_cannot_bypass_handler(workspace, process_receipts):
    host, row = host_for(workspace)
    host.allow_fixture()
    result = host.registry.execute(host.conn, project_id=PROJECT, root=None, name=TOOL,
        approval_id="arbitrary-string", args={"campaign_id": CAMPAIGN, "deliverable_id": row["id"], "expected_version": 1})
    assert not result["ok"]
    assert len(process_receipts) == 1


def test_cross_project_registry_and_gateway_denied(workspace, process_receipts):
    host, row = host_for(workspace)
    row = approve(host.conn, row)
    host.allow_fixture()
    args = dict(campaign_id=CAMPAIGN, deliverable_id=row["id"], expected_version=row["current_version"])
    assert not host.registry.execute(host.conn, project_id="fixture-beta", root=None,
        name=TOOL, args=args, approval_id="forged")["ok"]
    assert not host.gateway.execute_mcp_tool(SERVER_ID, "fixture-beta", TOOL, {"draft": {}})["ok"]
    assert not host.gateway.execute_mcp_tool(SERVER_ID, PROJECT, "publish_post", {})["ok"]
    with pytest.raises(ValueError):
        host.gateway.register_mcp_server("bad", ENDPOINT, PROJECT, "inline-secret")
    assert len(process_receipts) == 1


@pytest.mark.parametrize("bad", [[], [SimpleNamespace(name="publish_post")],
    [SimpleNamespace(name=TOOL, inputSchema={"type": "string"})],
    [SimpleNamespace(name=TOOL, inputSchema={"type": "object", "required": ["draft"], "properties": {"draft": {"type": "string"}}})]])
def test_unexpected_discovery_fails_closed(bad):
    with pytest.raises(ValueError):
        normalize_tools(bad)


@pytest.mark.parametrize("result", [
    CallToolResult(isError=True, content=[TextContent(type="text", text="private error")]),
    CallToolResult(content=[]),
    CallToolResult(content=[TextContent(type="text", text="invalid-json")]),
    CallToolResult(content=[TextContent(type="text", text="[]")]),
    CallToolResult(content=[TextContent(type="text", text="{}")], structuredContent={"mismatch": True}),
])
def test_malformed_or_error_results_fail_closed(result):
    with pytest.raises(ValueError):
        decode_result(result)


def test_real_sdk_tool_error_cleans_up(process_receipts):
    transport = FixtureTransport()
    with pytest.raises(Exception):
        transport.call_tool(ENDPOINT, TOOL, {"draft": {}})
    assert transport.active_sessions == 0
    assert len(process_receipts) == 1


def test_real_sdk_timeout_cleans_up(process_receipts, monkeypatch):
    # Startup can be faster than any small deadline on CI. Stall a known
    # operation after real SDK process creation instead of racing startup speed.
    entered = []

    async def pending_initialize(self):
        entered.append(True)
        await asyncio.Event().wait()

    monkeypatch.setattr("prototypes.marketing_mcp.transport.ClientSession.initialize", pending_initialize)
    transport = FixtureTransport(timeout_s=5)
    with pytest.raises(TimeoutError):
        transport.list_tools(ENDPOINT)
    assert entered == [True]
    assert transport.active_sessions == 0
    assert len(process_receipts) == 1


def test_transport_denies_external_targets_credentials_and_commands():
    transport = FixtureTransport()
    with pytest.raises(ValueError):
        transport.list_tools("https://example.invalid/mcp")
    for name, credential in (("publish_post", None), (TOOL, "sentinel")):
        with pytest.raises(ValueError):
            transport.call_tool(ENDPOINT, name, {"draft": {}}, credential=credential)
    assert not transport.exchanges


def test_host_and_child_deny_network_before_any_connection():
    with offline_network(), pytest.raises(PermissionError):
        socket.create_connection(("127.0.0.1", 9))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        with offline_network(), pytest.raises(PermissionError):
            udp.sendto(b"offline-sentinel", ("127.0.0.1", 9))
    result = subprocess.run([sys.executable, "-I", str(SERVER), "--check-network-guard"],
        env={k: os.environ[k] for k in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if k in os.environ},
        capture_output=True, text=True, timeout=20, check=True)
    assert result.stdout.strip() == "network-denied-before-connect"


def test_audit_sections_never_become_provider_text(workspace, process_receipts):
    conn, row = workspace
    structured = ("## Final copy\nApproved public text\n\n## Persisted user facts\nPrivate fact\n\n"
        "## Retrieved evidence\nInternal evidence\n\n## AI suggestions\nInternal suggestion\n\n"
        "## Unknown or unsupported information\nNone\n")
    row = deliverables.revise(conn, PROJECT, CAMPAIGN, row["id"], 1, {"content_md": structured})
    row = approve(conn, row)
    host = DraftHost(conn, PROJECT)
    host.allow_fixture()
    result = host.prepare(CAMPAIGN, row["id"], row["current_version"])
    assert result["ok"]
    assert result["payload"]["text"] == "Approved public text"
    assert "Private fact" not in json.dumps(result)


def test_revision_changed_during_call_is_not_released(workspace, process_receipts, monkeypatch):
    host, row = host_for(workspace)
    row = approve(host.conn, row)
    host.allow_fixture()
    original = host.gateway.execute_mcp_tool

    def change_after_format(*args, **kwargs):
        result = original(*args, **kwargs)
        deliverables.revise(host.conn, PROJECT, CAMPAIGN, row["id"], row["current_version"], {"content_md": "Pending replacement"})
        return result

    monkeypatch.setattr(host.gateway, "execute_mcp_tool", change_after_format)
    assert not host.prepare(CAMPAIGN, row["id"], row["current_version"])["ok"]
    assert host._payload is None
    assert not host.gateway._approvals


def test_public_export_keeps_optional_proof_dependencies_and_excludes_scratch():
    from scripts.package import export_public_repo as exporter

    assert "prototypes/marketing_mcp" in exporter.INCLUDE_ROOTS
    assert "prototypes/__init__.py" in exporter.INCLUDE_FILES
    assert "docs/research/marketing-connectors.md" in exporter.DOCS_ALLOWLIST
    files = {p.as_posix() for p in exporter.plan(SERVER.parent)}
    assert {"server.py", "transport.py", "host.py", "requirements.txt", "demo.py", "README.md"} <= files
    assert exporter.is_excluded(Path(".scratch/session/fixture.sqlite"))


def test_public_evidence_omits_digest_without_changing_real_payload():
    draft = {"project_id": PROJECT, "campaign_id": CAMPAIGN, "deliverable_id": "fixture-post",
             "approved_version": 3, "platform": "linkedin", "text": COPY}
    digest = hashlib.sha256(canonical_json(draft).encode()).hexdigest()
    original = {"payload": {**draft, "status": "draft", "publish": False, "idempotency_key": digest},
                "tool_runs": [{"tool_run_id": "private-synthetic-run-id", "status": "success"}]}
    output = public_evidence(original)
    assert original["payload"]["idempotency_key"] == digest
    assert digest not in json.dumps(output)
    assert "idempotency_key" not in output["payload"]
    assert output["payload"]["idempotency_digest"] == {
        "algorithm": "SHA-256", "hex_length": 64, "value_omitted": True,
        "matches_canonical_payload": True}
    assert output["tool_runs"][0]["tool_run_id"] == "synthetic-run-1"
    invalid = json.loads(json.dumps(original))
    invalid["payload"]["idempotency_key"] = "0" * 64
    with pytest.raises(ValueError, match="does not match"):
        public_evidence(invalid)
