"""DEV-007 W6: MCP Gateway/Manager package.

MCP is an EXTENSION mechanism: it never replaces internal tools. This
package owns the frozen I-4 interface:

    register_mcp_server(server_id, endpoint, scope, secret_ref | None)
    discover_mcp_tools(server_id) -> list[dict{name, description, parameters}]
    set_mcp_tool_allowlist(server_id, project_id, allowed: list[str])
    is_mcp_tool_allowed(server_id, project_id, tool) -> bool
    execute_mcp_tool(server_id, project_id, tool, args) -> dict
    mcp_status(project_id) -> list[dict]

Each function exists BOTH as a method on :class:`McpGateway` (preferred for
injected collaborators: tool registry / integration registry / vault /
transport) AND as a module-level delegate bound to a lazily-created default
gateway, so integrators can satisfy I-4 either way.

Rules enforced before any remote call:
* Default-deny: discovery never enables a tool; per-tool AND per-project
  allowlists gate execution.
* Frozen approval mapping: READ -> GREEN autonomous; WRITE /
  EXTERNAL_ACTION -> YELLOW approval-gated; DESTRUCTIVE -> RED
  approval-gated.
* Vault-bound credentials: ``secret_ref`` is None or a ``vault://`` ref;
  the value is resolved only at call time and never stored or audited.
"""

from __future__ import annotations

from typing import Any, Optional

from .gateway import (
    DEFAULT_TOOL_TIMEOUT_S,
    HEALTH_TIMEOUT_S,
    McpGateway,
    McpHttpTransport,
)
from .manager import (
    APPROVAL_MATRIX,
    CATEGORIES,
    REDACTED,
    approval_for_category,
    approval_for_tool,
    audit_arg_keys,
    classify_mcp_tool,
    is_autonomous,
    scrub_mapping,
    scrub_value,
)

__all__ = [
    "APPROVAL_MATRIX",
    "CATEGORIES",
    "DEFAULT_TOOL_TIMEOUT_S",
    "HEALTH_TIMEOUT_S",
    "McpGateway",
    "McpHttpTransport",
    "REDACTED",
    "approval_for_category",
    "approval_for_tool",
    "audit_arg_keys",
    "check_mcp_health",
    "classify_mcp_tool",
    "configure_gateway",
    "discover_mcp_tools",
    "execute_mcp_tool",
    "get_gateway",
    "grant_approval",
    "is_autonomous",
    "is_mcp_tool_allowed",
    "mcp_status",
    "register_mcp_server",
    "reset_gateway",
    "revoke_approval",
    "scrub_mapping",
    "scrub_value",
    "set_mcp_tool_allowlist",
]

_default_gateway: Optional[McpGateway] = None


def get_gateway() -> McpGateway:
    """Return the module-level default gateway, creating it on first use."""
    global _default_gateway
    if _default_gateway is None:
        from app.services.credentials import vault
        from app.services.integrations import registry
        from app.services.tools import registry as tool_registry
        _default_gateway = McpGateway(
            tool_registry=tool_registry,
            vault=vault,
            integration_registry=registry,
            transport=McpHttpTransport(),
        )
    return _default_gateway


def configure_gateway(**kwargs: Any) -> McpGateway:
    """(Re)build the default gateway with injected collaborators (I/O wiring)."""
    global _default_gateway
    _default_gateway = McpGateway(**kwargs)
    return _default_gateway


def reset_gateway() -> None:
    """Drop the default gateway (tests / hot re-wiring)."""
    global _default_gateway
    _default_gateway = None


# ---------------------------------------------------------------------------
# Frozen I-4 interface — module-level delegates.
# ---------------------------------------------------------------------------
def register_mcp_server(
    server_id: str,
    endpoint: str,
    scope: str,
    secret_ref: Optional[str] = None,
) -> dict:
    return get_gateway().register_mcp_server(server_id, endpoint, scope, secret_ref)


def discover_mcp_tools(server_id: str) -> list[dict]:
    return get_gateway().discover_mcp_tools(server_id)


def set_mcp_tool_allowlist(
    server_id: str, project_id: str, allowed: list[str]
) -> dict:
    return get_gateway().set_mcp_tool_allowlist(server_id, project_id, allowed)


def is_mcp_tool_allowed(server_id: str, project_id: str, tool: str) -> bool:
    return get_gateway().is_mcp_tool_allowed(server_id, project_id, tool)


def execute_mcp_tool(
    server_id: str, project_id: str, tool: str, args: dict
) -> dict:
    return get_gateway().execute_mcp_tool(server_id, project_id, tool, args)


def mcp_status(project_id: str) -> list[dict]:
    return get_gateway().mcp_status(project_id)


# Approval helpers (used by UI / integrator around the I-4 execute gate).
def grant_approval(server_id: str, project_id: str, tool: str) -> dict:
    return get_gateway().grant_approval(server_id, project_id, tool)


def revoke_approval(server_id: str, project_id: str, tool: str) -> dict:
    return get_gateway().revoke_approval(server_id, project_id, tool)


def check_mcp_health(server_id: str) -> dict:
    return get_gateway().check_mcp_health(server_id)
