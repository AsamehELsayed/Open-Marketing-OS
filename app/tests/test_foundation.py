"""Foundation hygiene (F3/F4) + shell smoke (F1/F2): routes stay presentation-only."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

ROUTES_DIR = Path(__file__).resolve().parents[1] / "routes"

FORBIDDEN = (
    "SELECT", "INSERT", "UPDATE", "DELETE",
    "subprocess", "Popen", "os.system", "os.popen",
    "chromadb", "sqlite3",
)

PAGES = ["/", "/chat", "/campaigns", "/tasks", "/approvals", "/companies",
         "/brain", "/results", "/settings", "/jobs"]


def test_runtime_ignored():
    text = (Path(__file__).resolve().parents[2] / ".gitignore").read_text(encoding="utf-8")
    assert "data/" in text and ".env" in text


def test_routes_contain_no_sql_or_subprocess():
    violations = []
    for path in ROUTES_DIR.glob("*.py"):
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            if token in text:
                violations.append(f"{path.name}: {token}")
    assert violations == []


def test_health_and_pages_200():
    client = TestClient(create_app())
    assert client.get("/health").status_code == 200
    for page in PAGES:
        assert client.get(page).status_code == 200, page
