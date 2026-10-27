"""Explicit developer action: fetch only the OS-specific pinned runtime, no weights."""
import argparse
import hashlib
from pathlib import Path
import sys
import urllib.request
from urllib.parse import urlparse
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.services.llm.local_model_catalog import default_catalog

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    runtime = default_catalog().runtime
    args.directory.mkdir(parents=True, exist_ok=True)
    target = args.directory / runtime.asset_name
    if not target.exists():
        temporary = target.with_suffix(target.suffix + ".part")
        try:
            request = urllib.request.Request(runtime.url, headers={"User-Agent": "OMOS-NativeCandidate/1"})
            with urllib.request.urlopen(request, timeout=120) as response, temporary.open("xb") as output:
                parsed = urlparse(response.geturl())
                if parsed.scheme != "https" or parsed.hostname not in {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}:
                    raise ValueError("Unapproved runtime redirect")
                received = 0
                while block := response.read(1024 * 1024):
                    received += len(block)
                    if received > runtime.size_bytes:
                        raise ValueError("Runtime size exceeded pin")
                    output.write(block)
            with temporary.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            if received != runtime.size_bytes or digest != runtime.sha256:
                raise ValueError("Runtime archive failed immutable pin")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    with target.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if target.stat().st_size != runtime.size_bytes or digest != runtime.sha256:
        raise ValueError("Cached runtime failed immutable pin")
    print(target.resolve())
if __name__ == "__main__":
    main()
