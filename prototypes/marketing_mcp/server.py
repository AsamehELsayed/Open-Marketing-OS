"""Fixed local formatter served by the official MCP SDK over stdio only."""
import hashlib
import asyncio
import json
import sys
from importlib.metadata import version


def deny_network(event, args):
    if event in {"socket.__new__", "socket.connect", "socket.getaddrinfo", "socket.bind",
                 "socket.sendto", "socket.sendmsg", "socket.gethostbyname", "socket.gethostbyaddr"}:
        raise PermissionError("Offline MCP fixture: networking denied")


if version("mcp") != "1.30.0":
    raise RuntimeError("Install the prototype's pinned mcp==1.30.0")

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("OMOS offline draft formatter")
FIELDS = {"project_id", "campaign_id", "deliverable_id", "approved_version", "platform", "text"}


@mcp.tool()
def format_campaign_draft(draft: dict) -> dict:
    """Format supplied text as an offline draft. Host enforces content approval."""
    if set(draft) != FIELDS:
        raise ValueError("Unexpected draft fields")
    if type(draft["approved_version"]) is not int or draft["approved_version"] < 1:
        raise ValueError("Invalid approved version")
    if any(not isinstance(draft[k], str) or not draft[k].strip() for k in FIELDS - {"approved_version"}):
        raise ValueError("Draft fields must be nonempty text")
    if draft["platform"] != "linkedin":
        raise ValueError("This fixture formats LinkedIn text only")
    canonical = json.dumps(draft, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {**draft, "status": "draft", "publish": False,
            "idempotency_key": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


if __name__ == "__main__":
    if sys.argv[1:] == ["--check-network-guard"]:
        sys.addaudithook(deny_network)
        import socket
        try:
            socket.create_connection(("127.0.0.1", 9))
        except PermissionError:
            print("network-denied-before-connect")
        else:
            raise RuntimeError("Network guard failed")
    elif sys.argv[1:]:
        raise ValueError("No server options are supported")
    else:
        async def serve():
            # Windows creates a private loopback socketpair when constructing
            # its asyncio loop. Install the deny guard before MCP work, after
            # that stdlib event-loop IPC has been created.
            sys.addaudithook(deny_network)
            await mcp.run_stdio_async()

        asyncio.run(serve())
