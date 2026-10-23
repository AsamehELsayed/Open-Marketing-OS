"""Create config/searxng/.env with a private random SearXNG secret.

Optional: only needed for web research. Safe to re-run; an existing secret is
never overwritten unless --rotate is given. Uses the standard library only.
"""
import argparse
import os
import re
import secrets
import sys
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / "config" / "searxng" / ".env"
_VALID = re.compile(r"^SEARXNG_SECRET=([0-9a-f]{64})\s*$", re.M)


def main(argv=None, env_path: Path = ENV_PATH) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rotate", action="store_true",
                    help="replace the existing secret")
    args = ap.parse_args(argv)
    if env_path.exists() and _VALID.search(env_path.read_text("utf-8")) \
            and not args.rotate:
        print(f"Secret already set in {env_path}; nothing changed.")
    else:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"SEARXNG_SECRET={secrets.token_hex(32)}\n")
        print(f"Wrote a new private secret to {env_path} (never commit it).")
    print("Next: docker compose -f config/searxng/docker-compose.yml up -d\n"
          "Then start OMOS with WEB_SEARCH_URL=http://127.0.0.1:8888/search")
    return 0


if __name__ == "__main__":
    sys.exit(main())
