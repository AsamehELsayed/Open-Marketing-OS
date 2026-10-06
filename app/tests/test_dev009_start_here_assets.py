"""Ensure every Start Here image is a shipped, compact product screenshot."""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
MANUAL = ROOT / "frontend" / "src" / "routes" / "StartHere.tsx"
PUBLIC = ROOT / "frontend" / "public"


def test_start_here_screenshot_references_exist_and_are_compact():
    source = MANUAL.read_text(encoding="utf-8")
    paths = re.findall(r'<img\s+src="(/app/start-here/[^\"]+)"', source)
    assert paths == [
        "/app/start-here/settings-ai.png",
        "/app/start-here/settings-integrations.png",
        "/app/start-here/completed-analysis-synthetic.png",
        "/app/start-here/main-chat-after-intro.png",
        "/app/start-here/execution-tree-synthetic.png",
    ]
    for url in paths:
        asset = PUBLIC / url.removeprefix("/app/")
        assert asset.is_file(), f"missing manual screenshot: {url}"
        assert asset.stat().st_size < 400_000, f"manual screenshot is too large: {url}"
    assert 'navigate("/app/business/new")' in source
    assert 'title: "Create your business"' in source
    assert 'to: "/app/business/new"' in source
