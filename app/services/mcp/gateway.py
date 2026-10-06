"""DEV-007 W6: MCP Gateway/Manager.

MCP is an EXTENSION mechanism: it never replaces internal tools. The gateway
provides register/connect/discover, allowlist-first gating, vault-bound
credentials, health/timeouts, audit, and approval-mapped execution.

Frozen interface (I-4) implemented EXACTLY by :class:`McpGateway`:

    register_mcp_server(server_id, endpoint, scope, secret_ref | None)
    discover_mcp_tools(server_id) -> list[dict{name, description, parameters}]
    set_mcp_tool_allowlist(server_id, project_id, allowed: list[str])
    is_mcp_tool_allowed(server_id, project_id, tool) -> bool
    execute_mcp_tool(server_id, project_id, tool, args) -> dict
    mcp_status(project_id) -> list[dict]

Rules enforced here:
* Default-deny: discovering tools NEVER enables them. Every tool starts
  disabled; per-tool AND per-project allowlists gate execution.
* Approval mapping (frozen): READ -> GREEN autonomous; WRITE /
  EXTERNAL_ACTION -> YELLOW approval-gated; DESTRUCTIVE -> RED
  approval-gated. Both the allowlist AND the approval mapping are checked
  BEFORE any transport call.
* Vault-bound credentials: ``secret_ref`` must be None or a ``vault://``
  reference. Raw secrets are rejected at registration; the gateway resolves
  the ref via the vault only at call time and never stores or audits it.
* Audit: every connect/discover/allow/execute is recorded with zero secrets
  (argument values are never persisted, only key names).
* Timeouts: tool calls use the ToolRecord ``timeout_s`` default (30s);
  health checks use a short timeout (5s).

Integration with the Tool Registry / Integration Registry / vault (built by
W4/W5 in parallel) is done through duck-typed, injectable collaborators, so
production wiring can be swapped in later while tests use local fakes built
against the frozen record shapes:

    ToolRecord{tool_id, source_type, description, parameters, side_effect,
        project_scope_required, auth_required, credential_scope,
        permission_level green|yellow|red, cost_type, timeout_s,
        retry_policy, enabled}
    IntegrationRecord{integration_id, capability, provider, scope, status,
        config, secret_ref, last_health}
    vault store/resolve/revoke with vault:// refs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path  # noqa: F401  (documents Windows-path-safe stdlib use)
from typing import Any, Callable, Optional

from .manager import (
    approval_for_category,
    audit_arg_keys,
    classify_mcp_tool,
    scrub_mapping,
)

DEFAULT_TOOL_TIMEOUT_S = 30
HEALTH_TIMEOUT_S = 5

_CATEGORY_TO_PERMISSION = {
    "READ": "green",
    "WRITE": "yellow",
    "EXTERNAL_ACTION": "yellow",
    "DESTRUCTIVE": "red",
}


class McpHttpTransport:
    def __init__(self, *, timeout_s: int = DEFAULT_TOOL_TIMEOUT_S,
                 allow_network: bool = False):
        self.timeout_s = max(1, int(timeout_s))
        self.allow_network = bool(allow_network)

    def _request(self, endpoint: str, payload: dict, *,
                 credential: Any = None, timeout_s: int | None = None) -> Any:
        if not self.allow_network:
            raise RuntimeError("MCP network transport is disabled")
        import httpx
        headers = {"Content-Type": "application/json"}
        if credential is not None:
            headers["Authorization"] = f"Bearer {credential}"
        response = httpx.post(
            endpoint, json=payload, headers=headers,
            timeout=max(1, int(timeout_s or self.timeout_s)),
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError("MCP transport returned an error")
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return data

    def health(self, endpoint: str, *, timeout_s: int = HEALTH_TIMEOUT_S) -> dict:
        self._request(
            endpoint,
            {"jsonrpc": "2.0", "id": "health", "method": "initialize",
             "params": {"protocolVersion": "2024-11-05",
                        "capabilities": {}, "clientInfo": {
                            "name": "open-marketing-os", "version": "1.0.0"}}},
            timeout_s=timeout_s,
        )
        return {"healthy": True, "status": "healthy"}

    def list_tools(self, endpoint: str) -> list[dict]:
        result = self._request(
            endpoint,
            {"jsonrpc": "2.0", "id": "tools", "method": "tools/list",
             "params": {}},
        )
        if isinstance(result, dict):
            return [item for item in (result.get("tools") or [])
                    if isinstance(item, dict)]
        return []

    def call_tool(self, endpoint: str, tool: str, args: dict, *,
                  credential: Any = None, timeout_s: int = DEFAULT_TOOL_TIMEOUT_S) -> dict:
        result = self._request(
            endpoint,
            {"jsonrpc": "2.0", "id": "call", "method": "tools/call",
             "params": {"name": tool, "arguments": dict(args)}},
            credential=credential,
            timeout_s=timeout_s,
        )
        return result if isinstance(result, dict) else {"result": result}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class _Server:
    server_id: str
    endpoint: str
    scope: str
    secret_ref: Optional[str] = None
    connected: bool = False
    last_health: str = "unknown"
    last_checked_at: Optional[str] = None
    tools: dict[str, dict] = field(default_factory=dict)  # name -> spec
    categories: dict[str, str] = field(default_factory=dict)  # name -> category


class McpGateway:
    """Allowlist-first MCP gateway. See module docstring for the contract."""

    def __init__(
        self,
        *,
        tool_registry: Any = None,
        integration_registry: Any = None,
        vault: Any = None,
        transport: Any = None,
        clock: Callable[[], str] = _now_iso,
    ) -> None:
        self._tool_registry = tool_registry
        self._integration_registry = integration_registry
        self._vault = vault
        self._transport = transport
        self._clock = clock
        self._servers: dict[str, _Server] = {}
        # (server_id, project_id) -> set(tool names). Default-deny: missing
        # entry means NOTHING is allowed.
        self._allowlists: dict[tuple[str, str], set[str]] = {}
        # (server_id, project_id, tool) approvals granted for YELLOW/RED tools.
        self._approvals: set[tuple[str, str, str]] = set()
        self._audit: list[dict] = []
        self._seq = 0
        self._persist = bool(
            integration_registry is not None
            and hasattr(integration_registry, "IntegrationRecord")
            and vault is not None
        )
        if self._persist:
            self._load_persisted()

    def _load_persisted(self) -> None:
        try:
            from app import deps
            from app.database import repos
            with deps.get_db() as conn:
                rows = repos.McpServers.list(conn)
        except Exception:
            return
        for row in rows:
            server_id = str(row.get("server_id") or "").strip()
            if not server_id:
                continue
            checked = str(row.get("last_checked_at") or "").strip()
            self._servers[server_id] = _Server(
                server_id=server_id,
                endpoint=str(row.get("endpoint") or ""),
                scope=str(row.get("scope") or ""),
                secret_ref=str(row.get("secret_ref") or "") or None,
                connected=bool(row.get("connected")),
                last_health=str(row.get("last_health") or "unknown"),
                last_checked_at=checked or None,
            )

    def _persist_server(self, server: _Server) -> None:
        if not self._persist:
            return
        try:
            from app import deps
            from app.database import repos
            now = self._clock()
            with deps.get_db() as conn:
                repos.McpServers.upsert(conn, {
                    "server_id": server.server_id,
                    "endpoint": server.endpoint,
                    "scope": server.scope,
                    "secret_ref": server.secret_ref or "",
                    "connected": int(server.connected),
                    "last_health": server.last_health,
                    "last_checked_at": server.last_checked_at or "",
                    "created_at": now,
                    "updated_at": now,
                })
        except Exception:
            return

    def _persist_health(self, server: _Server) -> None:
        if not self._persist:
            return
        try:
            from app import deps
            from app.database import repos
            with deps.get_db() as conn:
                repos.McpServers.set_health(
                    conn, server.server_id, server.last_health,
                    server.last_checked_at or "", int(server.connected),
                )
        except Exception:
            return

    def _persist_allowlist(self, server: _Server, project: str,
                           allowed: set[str]) -> None:
        if not self._persist:
            return
        try:
            from app import deps
            from app.database import repos
            with deps.get_db() as conn:
                repos.McpToolsAllowlist.set(
                    conn, server.server_id, project, sorted(allowed))
        except Exception:
            return

    def _validate_secret_ref(self, secret_ref: str) -> None:
        if not self._persist or self._vault is None:
            return
        try:
            if hasattr(self._vault, "get_ref_row"):
                row = self._vault.get_ref_row(secret_ref)
                if not row or row.get("status") != "active":
                    raise ValueError("secret_ref is not active")
            elif hasattr(self._vault, "resolve"):
                self._vault.resolve(secret_ref)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("secret_ref could not be resolved") from exc

    # ------------------------------------------------------------------
    # I-4: registration / connect
    # ------------------------------------------------------------------
    def register_mcp_server(
        self,
        server_id: str,
        endpoint: str,
        scope: str,
        secret_ref: Optional[str] = None,
    ) -> dict:
        if not server_id or not str(server_id).strip():
            raise ValueError("server_id is required")
        if not endpoint or not str(endpoint).strip():
            raise ValueError("endpoint is required")
        if not scope or not str(scope).strip():
            raise ValueError("scope is required")
        if secret_ref is not None:
            if not isinstance(secret_ref, str) or not secret_ref.startswith("vault://"):
                raise ValueError("secret_ref must be None or a vault:// reference")
            self._validate_secret_ref(secret_ref)
        server = _Server(
            server_id=str(server_id),
            endpoint=str(endpoint),
            scope=str(scope),
            secret_ref=secret_ref,
        )
        server.connected = False
        server.last_health = "unknown"
        server.last_checked_at = None
        self._servers[server.server_id] = server
        self._bridge_integration_record(server)
        self._audit_row(
            "connect", server.server_id, None, None,
            ok=True, detail={"endpoint": server.endpoint, "scope": server.scope},
        )
        self._persist_server(server)
        return {
            "server_id": server.server_id,
            "endpoint": server.endpoint,
            "scope": server.scope,
            "connected": False,
        }

    # ------------------------------------------------------------------
    # I-4: discovery (never auto-enables)
    # ------------------------------------------------------------------
    def discover_mcp_tools(self, server_id: str) -> list[dict]:
        server = self._require_server(server_id)
        raw_tools = self._list_remote_tools(server)
        specs: list[dict] = []
        for raw in raw_tools:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name", "")).strip()
            if not name:
                continue
            description = str(raw.get("description", "") or "")
            parameters = raw.get("parameters", {}) or {}
            if not isinstance(parameters, dict):
                parameters = {}
            category = classify_mcp_tool(name, description)
            server.tools[name] = {
                "name": name,
                "description": description,
                "parameters": parameters,
            }
            server.categories[name] = category
            self._bridge_tool_record(server, name, description, parameters, category)
            # Exact frozen shape: only name/description/parameters.
            specs.append(
                {"name": name, "description": description, "parameters": parameters}
            )
        # Default-deny invariant: discovery touches NO allowlist entry.
        self._audit_row(
            "discover", server.server_id, None, None, ok=True,
            detail={"tool_count": len(specs)},
        )
        return specs

    # ------------------------------------------------------------------
    # I-4: per-tool AND per-project allowlists (default-deny)
    # ------------------------------------------------------------------
    def set_mcp_tool_allowlist(
        self, server_id: str, project_id: str, allowed: list[str]
    ) -> dict:
        server = self._require_server(server_id)
        project = self._require_project(project_id)
        names = list(allowed or [])
        unknown = [n for n in names if n not in server.tools]
        if unknown:
            raise ValueError(
                f"unknown MCP tools for server {server.server_id!r}: {unknown}"
            )
        self._allowlists[(server.server_id, project)] = set(names)
        self._persist_allowlist(server, project, self._allowlists[(server.server_id, project)])
        self._audit_row(
            "allow", server.server_id, project, None, ok=True,
            detail={"allowed": sorted(names)},
        )
        return {
            "server_id": server.server_id,
            "project_id": project,
            "allowed": sorted(names),
        }

    def is_mcp_tool_allowed(
        self, server_id: str, project_id: str, tool: str
    ) -> bool:
        if not server_id or not project_id or not tool:
            return False
        allowed = self._allowlists.get((str(server_id), str(project_id)))
        if not allowed:
            return False
        return str(tool) in allowed

    # ------------------------------------------------------------------
    # I-4: approval-mapped execution (allowlist AND approval BEFORE call)
    # ------------------------------------------------------------------
    def execute_mcp_tool(
        self, server_id: str, project_id: str, tool: str, args: dict
    ) -> dict:
        server = self._servers.get(str(server_id)) if server_id else None
        if server is None:
            self._audit_row(
                "execute", str(server_id), str(project_id), str(tool),
                ok=False, detail={"error": "unknown server"},
                arg_keys=args,
            )
            return {"ok": False, "error": f"unknown MCP server: {server_id!r}",
                    "status": "failed"}
        project = str(project_id or "").strip()
        if not project:
            self._audit_row(
                "execute", server.server_id, str(project_id), str(tool),
                ok=False, detail={"error": "project_id is required (fail closed)"},
                arg_keys=args,
            )
            return {"ok": False, "error": "project_id is required (fail closed)",
                    "status": "failed"}
        if not isinstance(args, dict):
            self._audit_row(
                "execute", server.server_id, project, str(tool),
                ok=False, detail={"error": "args must be an object"}, arg_keys=args,
            )
            return {"ok": False, "error": f"tool {tool}: args must be an object",
                    "status": "failed"}
        if str(tool) not in server.tools:
            self._audit_row(
                "execute", server.server_id, project, str(tool),
                ok=False, detail={"error": "unknown tool"}, arg_keys=args,
            )
            return {"ok": False, "error": f"unknown MCP tool: {tool!r}",
                    "status": "failed"}

        # Gate 1 (BEFORE any call): per-tool AND per-project allowlist.
        if not self.is_mcp_tool_allowed(server.server_id, project, str(tool)):
            self._audit_row(
                "execute", server.server_id, project, str(tool),
                ok=False, detail={"error": "tool not allowlisted for project"},
                arg_keys=args,
            )
            return {
                "ok": False,
                "error": f"tool {tool!r} is not allowlisted for project "
                         f"{project!r} (default-deny)",
                "status": "denied",
                "approval_required": False,
            }

        # Gate 2 (BEFORE any call): frozen approval mapping.
        category = server.categories.get(str(tool), "WRITE")
        approval_level = approval_for_category(category)
        if approval_level in ("YELLOW", "RED") and (
            server.server_id, project, str(tool)
        ) not in self._approvals:
            self._audit_row(
                "execute", server.server_id, project, str(tool),
                ok=False,
                detail={"error": "approval required", "approval_level": approval_level,
                        "category": category},
                arg_keys=args,
            )
            return {
                "ok": False,
                "error": f"tool {tool!r} requires {approval_level} approval "
                         f"(category {category}) before execution",
                "status": "approval_required",
                "approval_required": True,
                "approval_level": approval_level,
                "category": category,
            }

        # Both gates passed: resolve vault-bound credential (value never stored
        # or audited) and call the transport with a timeout. A vault failure
        # must not raise out of the frozen -> dict contract: fail closed.
        credential = None
        if server.secret_ref is not None:
            try:
                credential = self._resolve_credential(server.secret_ref)
            except Exception:
                self._audit_row(
                    "execute", server.server_id, project, str(tool),
                    ok=False,
                    detail={"error": "credential resolve failed",
                            "approval_level": approval_level,
                            "category": category},
                    arg_keys=args,
                )
                return {"ok": False,
                        "error": f"tool {tool}: credential resolve failed",
                        "status": "failed"}
        try:
            result = self._call_remote(
                server, str(tool), args,
                credential=credential, timeout_s=DEFAULT_TOOL_TIMEOUT_S,
            )
        except Exception:
            self._audit_row(
                "execute", server.server_id, project, str(tool),
                ok=False, detail={"error": "transport call failed",
                        "approval_level": approval_level,
                        "category": category},
                arg_keys=args,
            )
            return {"ok": False, "error": f"tool {tool} failed",
                    "status": "failed"}

        if not isinstance(result, dict):
            self._audit_row(
                "execute", server.server_id, project, str(tool),
                ok=False, detail={"error": "transport must return dict"},
                arg_keys=args,
            )
            return {"ok": False, "error": f"tool {tool}: transport must return dict",
                    "status": "failed"}
        self._audit_row(
            "execute", server.server_id, project, str(tool), ok=True,
            detail={"approval_level": approval_level, "category": category},
            arg_keys=args,
        )
        out = dict(result)
        out.setdefault("ok", True)
        out.setdefault("status", "succeeded")
        return out

    # ------------------------------------------------------------------
    # I-4: UI status
    # ------------------------------------------------------------------
    def mcp_status(self, project_id: str) -> list[dict]:
        project = str(project_id or "")
        rows: list[dict] = []
        for server in self._servers.values():
            allowed_set = set(
                self._allowlists.get((server.server_id, project), set()))
            if self._persist and project:
                try:
                    from app import deps
                    from app.database import repos
                    with deps.get_db() as conn:
                        allowed_set = set(
                            repos.McpToolsAllowlist.list_for(
                                conn, server.server_id, project))
                except Exception:
                    pass
            allowed = sorted(allowed_set)
            status = (
                "connected" if server.connected
                else "unknown" if server.last_health == "unknown"
                else server.last_health
            )
            capabilities = [
                {"name": name, "approval": server.categories.get(name, "WRITE")}
                for name in sorted(server.tools)
            ]
            rows.append({
                "server_id": server.server_id,
                "name": server.server_id,
                "endpoint": server.endpoint,
                "scope": server.scope,
                "connected": server.connected,
                "status": status,
                "last_health": server.last_health,
                "last_checked_at": server.last_checked_at,
                "tool_count": len(server.tools),
                "allowed_tools": allowed,
                "has_credential": server.secret_ref is not None,
                "capabilities": capabilities,
            })
        return rows

    # ------------------------------------------------------------------
    # Health (outside I-4; supports the status/health goal)
    # ------------------------------------------------------------------
    def check_mcp_health(self, server_id: str) -> dict:
        """Probe a registered server with HEALTH_TIMEOUT_S; update status.

        Failure marks the server disconnected (fail closed). The probe never
        includes credentials in the audit row.
        """
        server = self._require_server(server_id)
        status = "unhealthy"
        extra: dict = {}
        try:
            if self._transport is None:
                raise ValueError("no MCP transport configured")
            if hasattr(self._transport, "health"):
                info = self._transport.health(
                    server.endpoint, timeout_s=HEALTH_TIMEOUT_S
                )
            elif hasattr(self._transport, "ping"):
                info = self._transport.ping(
                    server.endpoint, timeout_s=HEALTH_TIMEOUT_S
                )
            else:
                info = {"healthy": True}
            if isinstance(info, dict):
                if "status" in info:
                    status = str(info["status"])
                else:
                    status = "healthy" if info.get("healthy", True) else "unhealthy"
                extra = {
                    k: v for k, v in info.items()
                    if k in ("latency_ms", "version", "tool_count")
                }
            else:
                status = "healthy"
        except Exception:
            status = "unhealthy"
            extra = {"error": "health probe failed"}
        server.last_health = status
        server.connected = str(status).strip().lower() in {"healthy", "ok", "ready"}
        server.last_checked_at = self._clock()
        self._persist_health(server)
        self._audit_row(
            "health", server.server_id, None, None,
            ok=server.connected, detail={"status": status, **extra},
        )
        return {
            "server_id": server.server_id,
            "status": status,
            "connected": server.connected,
            "checked_at": server.last_checked_at,
            "timeout_s": HEALTH_TIMEOUT_S,
        }

    # ------------------------------------------------------------------
    # Approvals + audit accessors (outside I-4; used by UI/tests)
    # ------------------------------------------------------------------
    def grant_approval(self, server_id: str, project_id: str, tool: str) -> dict:
        server = self._require_server(server_id)
        project = self._require_project(project_id)
        if str(tool) not in server.tools:
            raise ValueError(f"unknown MCP tool: {tool!r}")
        self._approvals.add((server.server_id, project, str(tool)))
        category = server.categories.get(str(tool), "WRITE")
        self._audit_row(
            "approve", server.server_id, project, str(tool), ok=True,
            detail={"approval_level": approval_for_category(category),
                    "category": category},
        )
        return {"server_id": server.server_id, "project_id": project,
                "tool": str(tool), "approved": True}

    def revoke_approval(self, server_id: str, project_id: str, tool: str) -> dict:
        key = (str(server_id), str(project_id), str(tool))
        self._approvals.discard(key)
        return {"server_id": str(server_id), "project_id": str(project_id),
                "tool": str(tool), "approved": False}

    def tool_category(self, server_id: str, tool: str) -> str:
        server = self._require_server(server_id)
        if str(tool) not in server.tools:
            raise ValueError(f"unknown MCP tool: {tool!r}")
        return server.categories.get(str(tool), "WRITE")

    def audit_rows(self) -> list[dict]:
        return [dict(row) for row in self._audit]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _require_server(self, server_id: str) -> _Server:
        server = self._servers.get(str(server_id)) if server_id else None
        if server is None:
            raise ValueError(f"unknown MCP server: {server_id!r}")
        return server

    @staticmethod
    def _require_project(project_id: str) -> str:
        project = str(project_id or "").strip()
        if not project:
            raise ValueError("project_id is required (fail closed)")
        return project

    def _list_remote_tools(self, server: _Server) -> list[dict]:
        if self._transport is None:
            return []
        if hasattr(self._transport, "list_tools"):
            tools = self._transport.list_tools(server.endpoint)
        elif hasattr(self._transport, "discover"):
            tools = self._transport.discover(server.endpoint)
        elif callable(self._transport):
            tools = self._transport(server.endpoint)
        else:
            raise ValueError("MCP transport cannot list tools")
        return list(tools or [])

    def _call_remote(
        self, server: _Server, tool: str, args: dict,
        *, credential: Any = None, timeout_s: int = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict:
        if self._transport is None:
            raise ValueError(f"no MCP transport connected for {server.server_id!r}")
        if hasattr(self._transport, "call_tool"):
            return self._transport.call_tool(
                server.endpoint, tool, dict(args),
                credential=credential, timeout_s=timeout_s,
            )
        if hasattr(self._transport, "call"):
            return self._transport.call(
                server.endpoint, tool, dict(args), timeout_s=timeout_s
            )
        raise ValueError("MCP transport cannot execute tools")

    def _resolve_credential(self, secret_ref: str) -> Any:
        if self._vault is None:
            raise ValueError("credential vault is not configured")
        if hasattr(self._vault, "resolve"):
            return self._vault.resolve(secret_ref)
        if hasattr(self._vault, "get"):
            return self._vault.get(secret_ref)
        if isinstance(self._vault, dict):
            return self._vault.get(secret_ref)
        return None

    def _bridge_tool_record(
        self, server: _Server, name: str, description: str,
        parameters: dict, category: str,
    ) -> None:
        """Mirror a discovered tool into the Tool Registry (disabled = deny)."""
        if self._tool_registry is None:
            return
        record = {
            "tool_id": f"mcp:{server.server_id}:{name}",
            "source_type": "mcp",
            "description": description,
            "parameters": parameters,
            "side_effect": category.lower(),
            "project_scope_required": True,
            "auth_required": server.secret_ref is not None,
            "credential_scope": server.scope,
            "permission_level": _CATEGORY_TO_PERMISSION[category],
            "cost_type": "external_call",
            "timeout_s": DEFAULT_TOOL_TIMEOUT_S,
            "retry_policy": {"max_retries": 0},
            "enabled": False,  # default-deny: never auto-enable
        }
        register_tool = getattr(self._tool_registry, "register_tool", None)
        if callable(register_tool):
            try:
                from app.services.tools.registry import ToolRecord
                register_tool(ToolRecord(
                    tool_id=record["tool_id"],
                    source_type=record["source_type"],
                    description=record["description"],
                    parameters=record["parameters"],
                    side_effect=record["side_effect"],
                    project_scope_required=record["project_scope_required"],
                    auth_required=record["auth_required"],
                    credential_scope=record["credential_scope"],
                    permission_level=record["permission_level"],
                    cost_type=record["cost_type"],
                    timeout_s=record["timeout_s"],
                    retry_policy="bounded",
                    enabled=False,
                ))
                return
            except Exception:
                pass
        for method in ("register", "upsert", "add"):
            fn = getattr(self._tool_registry, method, None)
            if callable(fn):
                try:
                    fn(record)
                except TypeError:
                    try:
                        fn(dict(record))
                    except Exception:
                        pass
                return
        if hasattr(self._tool_registry, "__setitem__"):
            try:
                self._tool_registry[record["tool_id"]] = record
            except Exception:
                pass

    def _bridge_integration_record(self, server: _Server) -> None:
        """Mirror a registered server into the Integration Registry."""
        if self._integration_registry is None:
            return
        record = {
            "integration_id": f"mcp:{server.server_id}",
            "capability": "mcp",
            "provider": server.endpoint,
            "scope": server.scope,
            "status": "not_configured" if not server.connected else "connected",
            "config": {"endpoint": server.endpoint},
            "secret_ref": server.secret_ref,
            "last_health": server.last_health,
        }
        for method in ("upsert", "register", "add"):
            fn = getattr(self._integration_registry, method, None)
            if callable(fn):
                try:
                    fn(record)
                except TypeError:
                    try:
                        fn(dict(record))
                    except Exception:
                        pass
                return
        if hasattr(self._integration_registry, "__setitem__"):
            try:
                self._integration_registry[record["integration_id"]] = record
            except Exception:
                pass

    def _audit_row(
        self, action: str, server_id: Any, project_id: Any, tool: Any,
        *, ok: bool, detail: Optional[dict] = None,
        arg_keys: Any = None,
    ) -> dict:
        self._seq += 1
        row = {
            "seq": self._seq,
            "ts": self._clock(),
            "action": str(action),
            "server_id": str(server_id) if server_id is not None else None,
            "project_id": str(project_id) if project_id is not None else None,
            "tool": str(tool) if tool is not None else None,
            # Values are scrubbed; arg VALUES never stored, only key names.
            "detail": scrub_mapping(detail or {}),
            "arg_keys": audit_arg_keys(arg_keys) if arg_keys is not None else [],
            "ok": bool(ok),
        }
        self._audit.append(row)
        return row
