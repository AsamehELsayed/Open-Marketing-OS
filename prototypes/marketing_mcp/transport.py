"""One fixed SDK subprocess collaborator for the existing synchronous gateway."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).with_name("server.py").resolve()
ENDPOINT = "offline://omos-marketing-fixture"
TOOL = "format_campaign_draft"
_offline = ContextVar("marketing_mcp_offline", default=False)


def _network_guard(event, args):
    if _offline.get() and event in {"socket.__new__", "socket.connect", "socket.getaddrinfo", "socket.bind",
                                  "socket.sendto", "socket.sendmsg", "socket.gethostbyname", "socket.gethostbyaddr"}:
        raise PermissionError("Offline MCP host: networking denied")


sys.addaudithook(_network_guard)


@contextmanager
def offline_network():
    token = _offline.set(True)
    try:
        yield
    finally:
        _offline.reset(token)


def normalize_tools(tools):
    if len(tools) != 1 or tools[0].name != TOOL:
        raise ValueError("Unexpected fixture tools")
    schema = tools[0].inputSchema
    if (schema.get("type") != "object" or schema.get("required") != ["draft"]
            or set(schema.get("properties", {})) != {"draft"}
            or schema["properties"]["draft"].get("type") != "object"):
        raise ValueError("Unexpected formatter schema")
    return [{"name": TOOL, "description": tools[0].description or "Offline formatter",
             "parameters": schema}]


def decode_result(result):
    if result.isError:
        raise ValueError("Formatter returned an MCP error")
    if len(result.content) != 1 or result.content[0].type != "text":
        raise ValueError("Unexpected MCP content")
    payload = json.loads(result.content[0].text)
    if not isinstance(payload, dict):
        raise ValueError("Expected draft object")
    if result.structuredContent is not None and result.structuredContent != payload:
        raise ValueError("Conflicting MCP result representations")
    return payload


class FixtureTransport:
    """Not configurable for remote endpoints or third-party executables."""

    def __init__(self, timeout_s=15):
        if version("mcp") != "1.30.0":
            raise RuntimeError("Install prototypes/marketing_mcp/requirements.txt")
        if not 0 < timeout_s <= 30:
            raise ValueError("timeout_s must be in (0,30]")
        self.timeout_s = timeout_s
        self.exchanges = []
        self.active_sessions = 0

    async def _exchange(self, method, args, timeout_s):
        # SDK also merges a small documented safe environment allowlist; no
        # os.environ wholesale, PYTHONPATH, tokens, keys or user startup code.
        params = StdioServerParameters(command=sys.executable,
            args=["-I", str(SERVER)], env={k: os.environ[k] for k in
                ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if k in os.environ},
            cwd=str(SERVER.parent))
        self.active_sessions += 1
        try:
            async with asyncio.timeout(timeout_s):
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write,
                            read_timeout_seconds=timedelta(seconds=timeout_s)) as session:
                        initialized = await session.initialize()
                        tools = normalize_tools((await session.list_tools()).tools)
                        trace = {"protocol": initialized.protocolVersion,
                                 "methods": ["initialize", "tools/list"], "tools": tools}
                        self.exchanges.append(trace)
                        if method == "list":
                            return tools
                        result = await session.call_tool(TOOL, args)
                        trace["methods"].append("tools/call")
                        return decode_result(result)
        finally:
            self.active_sessions -= 1

    def _run(self, endpoint, method, args=None, timeout_s=30):
        if endpoint != ENDPOINT:
            raise ValueError("Only the fixed offline fixture is supported")
        # Initialize Windows asyncio's private loopback socketpair before the
        # guard; all MCP operations then run with outbound networking denied.
        with asyncio.Runner() as runner:
            with offline_network():
                return runner.run(self._exchange(method, args, min(self.timeout_s, timeout_s)))

    def list_tools(self, endpoint):
        return self._run(endpoint, "list")

    def call_tool(self, endpoint, tool, args, *, credential=None, timeout_s=30):
        if credential is not None or tool != TOOL or set(args) != {"draft"}:
            raise ValueError("Only credential-free draft formatting is supported")
        return {"payload": self._run(endpoint, "call", args, timeout_s)}
