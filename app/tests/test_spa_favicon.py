from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import spa


def test_favicon_prefers_packaged_asset_then_uses_source_fallback(
    tmp_path, monkeypatch,
):
    source_icon = Path(__file__).resolve().parents[2] / "frontend" / "public" / "favicon.ico"
    source_bytes = source_icon.read_bytes()
    packaged_icon = tmp_path / "dist" / "favicon.ico"
    packaged_icon.parent.mkdir()
    packaged_bytes = b"\x00\x00\x01\x00packaged-icon"
    packaged_icon.write_bytes(packaged_bytes)
    monkeypatch.setattr(spa, "FAVICON_FILE", packaged_icon)
    monkeypatch.setattr(spa, "SOURCE_FAVICON_FILE", source_icon)

    app = FastAPI()
    app.include_router(spa.router)
    with TestClient(app) as client:
        packaged = client.get("/favicon.ico")
        assert packaged.status_code == 200
        assert packaged.headers["content-type"].startswith("image/x-icon")
        assert packaged.content == packaged_bytes

        monkeypatch.setattr(spa, "FAVICON_FILE", tmp_path / "missing" / "favicon.ico")
        source = client.get("/favicon.ico")
        assert source.status_code == 200
        assert source.headers["content-type"].startswith("image/x-icon")
        assert source.content == source_bytes
        assert source.content[:4] == b"\x00\x00\x01\x00"

        assert client.get("/favicon.ico/secret.js").status_code == 404
        assert client.get("/api/missing").status_code == 404
