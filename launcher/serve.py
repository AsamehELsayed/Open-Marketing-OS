"""Backend entry point for a source checkout / dev run.

The frozen Windows build re-enters `omos_launcher.py --serve` instead of using
this file, so the shipped app is a single artifact. This module exists so the
same backend can be started directly during development and by the packaging
smoke test, and so `python launcher/serve.py` works with no arguments.

Binds loopback only. Takes the port from `OMOS_PORT` when the launcher supplied
one, otherwise asks the OS for a free port rather than assuming 8000 is free.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make `app` importable when run as a script from the repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    import uvicorn

    from app import paths
    from app.main import create_app

    paths.ensure_writable_dirs()
    port = int(os.environ.get("OMOS_PORT") or 0) or _free_port()
    print(f"{paths.APP_TITLE} backend on http://127.0.0.1:{port}/app")
    uvicorn.run(
        create_app(),
        host="127.0.0.1",   # loopback only
        port=port,
        log_level="warning",
    )
    return 0


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


if __name__ == "__main__":
    sys.exit(main())
