from __future__ import annotations

import importlib
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.database import repos
from app.database.sqlite import connect
from app.main import create_app
import app.services.config_service as config_module
from app.services import state as store
from app.services import turns as turnsvc
from app.services.config_service import ConfigService
from app.services.integrations import registry
from app.services.social.instagram import capabilities as instagram_capabilities

secret_store = importlib.import_module("app.services.credentials.store")

UNICODE_PATHS = (
    "/Users/用户/资料/私密.txt",
    "/Volumes/磁盘/私密/记录.txt",
)
UNICODE_VALUE = "用户私密值-7c21"
SAFE_COPY = "Use /campaign/launch as ordinary copy."


class MemoryBackend:
    name = "test-memory"

    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def put(self, key: str, plaintext: bytes) -> None:
        self.values[key] = bytes(plaintext)

    def get(self, key: str) -> bytes | None:
        return self.values.get(key)

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


@pytest.fixture()
def boundary_client(tmp_path, monkeypatch):
    db = tmp_path / "security-edges.db"
    root = tmp_path / "root"
    vault = tmp_path / "vault"
    root.mkdir()
    vault.mkdir()
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setattr(deps, "ROOT", root)
    monkeypatch.setenv("AI_RUNTIME", "langgraph")
    monkeypatch.setenv("OMOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OMOS_CREDENTIALS_DIR", str(vault))
    monkeypatch.setenv("CHROMA_ENABLED", "0")
    deps.init_db(db)
    previous_backend = secret_store._default
    secret_store.set_default_backend(MemoryBackend())
    config_module.clear_all_test_overrides()
    from app.services.llm import openrouter_provider
    openrouter_provider.clear_catalog_cache()
    from app.services.mcp import reset_gateway
    reset_gateway()
    monkeypatch.setattr(instagram_capabilities, "_i7_registered", False, raising=False)
    from app.routes import graph_runtime
    monkeypatch.setattr(graph_runtime, "_GRAPH", None, raising=False)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client, db, root
    openrouter_provider.clear_catalog_cache()
    reset_gateway()
    secret_store.set_default_backend(previous_backend)
    config_module.clear_all_test_overrides()


def _sse_payloads(body: str) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    event_type = ""
    data_lines: list[str] = []
    for line in body.splitlines() + [""]:
        if line.startswith("event: "):
            event_type = line[7:].strip()
        elif line.startswith("data: ") and event_type:
            data_lines.append(line[6:])
        elif not line and event_type:
            try:
                value = json.loads("\n".join(data_lines) or "{}")
            except json.JSONDecodeError:
                value = {"raw": "\n".join(data_lines)}
            payloads[event_type] = value if isinstance(value, dict) else {"value": value}
            event_type = ""
            data_lines = []
    return payloads


def _create_graph_thread(client: TestClient, conversation_id: str, key: str) -> str:
    response = client.post(
        "/api/graph/threads",
        json={
            "project_id": "starter",
            "conversation_id": conversation_id,
            "text": "run a safe answer",
            "client_message_id": key,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["thread_id"]


def _insert_security_event(db, thread_id: str) -> dict[str, Any]:
    path_one = "/usr/local/share/company/private.docx"
    path_two = "/builds/acme/project/private.docx"
    path_three = "/Users/alice/Documents/private-notes"
    path_four = "/Volumes/PrivateDrive/tenant/record"
    paths = (UNICODE_PATHS[0], UNICODE_PATHS[1], path_one, path_two, path_three, path_four)
    metadata = {
        "provider": "openrouter",
        "model": "vendor/model",
        "safe_note": "keep this metadata",
        "openai-api-key": "root-openai-value",
        "OPENROUTER-API-KEY": "root-openrouter-value",
        "ai-runtime": "root-runtime-value",
        "COMSPEC": "root-comspec-value",
        "telemetry": {
            "provider": "openrouter",
            "model": "vendor/model",
            "latency_ms": 12,
            "safe_note": "keep this telemetry",
            "path_note": (
                f"Artifacts {paths[0]}, {paths[1]}, {path_one}, {path_two}, "
                f"{path_three}, and {path_four}"
            ),
            "environment_note": f"openai-api-key={UNICODE_VALUE} COMSPEC",
            "nested": {
                "OpenRouter-Api-Key": "deep-openrouter-value",
                "AI-RUNTIME": "deep-runtime-value",
                "safe": "deep-safe",
            },
            "items": [
                {"openai-api-key": "list-openai-value", "safe": "list-safe"},
                {"COMSPEC": "list-comspec-value", "safe": "list-safe"},
            ],
        },
    }
    detail = (
        f"{SAFE_COPY} Campaign copy remains at https://example.com/campaign?id=7. "
        f"Runtime artifacts {paths[0]}, {paths[1]}, {path_one}, {path_two}, "
        f"{path_three}, and {path_four}."
    )
    with deps.get_db(db) as conn:
        repos.ExecutionEvents.insert(conn, {
            "project_id": "starter",
            "conversation_id": "",
            "turn_id": thread_id,
            "job_id": "",
            "event_type": "model_completed",
            "label": "Provider request failed",
            "detail": detail,
            "metadata_json": json.dumps(metadata),
            "created_at": "2026-09-25T00:00:00+00:00",
        })
        repos.Turns.set_status(conn, thread_id, "completed", {"provider": "graph"})
    return {
        "paths": paths,
        "values": (
            "root-openai-value",
            "root-openrouter-value",
            "root-runtime-value",
            "root-comspec-value",
            "deep-openrouter-value",
            "deep-runtime-value",
            "list-openai-value",
            "list-comspec-value",
            UNICODE_VALUE,
        ),
    }


def test_graph_and_chat_sse_filter_normalized_environment_keys_and_arbitrary_paths(boundary_client):
    from app.contracts.events import sanitize_user_text

    assert sanitize_user_text("Use /campaign/launch as ordinary copy") == "Use /campaign/launch as ordinary copy"
    assert sanitize_user_text(SAFE_COPY) == SAFE_COPY
    for path in UNICODE_PATHS:
        assert path not in sanitize_user_text(path)
    client, db, _root = boundary_client
    with deps.get_db(db) as conn:
        conversation = store.create_conversation(conn, project_id="starter")
    thread_id = _create_graph_thread(client, conversation["id"], "security-sse")
    fixture = _insert_security_event(db, thread_id)

    graph_response = client.get(f"/api/graph/threads/{thread_id}/events")
    chat_response = client.get(f"/chat/turns/{thread_id}/events")
    assert graph_response.status_code == 200
    assert chat_response.status_code == 200
    graph_payload = _sse_payloads(graph_response.text)["model_completed"]
    chat_payload = _sse_payloads(chat_response.text)["model_completed"]

    for payload in (graph_payload, chat_payload):
        body = json.dumps(payload, ensure_ascii=False)
        for key in ("openai-api-key", "OPENROUTER-API-KEY", "ai-runtime", "COMSPEC"):
            assert key not in body
        for value in fixture["values"]:
            assert value not in body
        for path in fixture["paths"]:
            assert path not in body
        assert SAFE_COPY in payload["detail"]
        assert "Campaign copy remains at https://example.com/campaign?id=7." in payload["detail"]
        assert payload["meta"]["provider"] == "openrouter"
        assert payload["meta"]["model"] == "vendor/model"
        assert payload["meta"]["telemetry"]["latency_ms"] == 12
        assert payload["meta"]["telemetry"]["safe_note"] == "keep this telemetry"
        assert payload["meta"]["telemetry"]["nested"]["safe"] == "deep-safe"
        assert payload["meta"]["telemetry"]["items"][0]["safe"] == "list-safe"
        assert payload["meta"]["telemetry"]["items"][1]["safe"] == "list-safe"


@pytest.mark.parametrize("endpoint", ["/files/upload", "/files/upload/image"])
def test_files_error_drops_arbitrary_paths_and_noncanonical_environment_keys(boundary_client, monkeypatch, endpoint):
    client, _db, _root = boundary_client
    from app.routes import files as files_route
    from app.services.files import IngestError

    def fail_ingest(*args, **kwargs):
        raise IngestError(
            "ingest_rejected",
            f"COMSPEC openai-api-key={UNICODE_VALUE} "
            f"{UNICODE_PATHS[0]} {UNICODE_PATHS[1]} "
            "/usr/local/share/company/private.docx "
            "/builds/acme/project/private.docx /Users/alice/Documents/private-notes "
            "/Volumes/PrivateDrive/tenant/record",
        )

    monkeypatch.setattr(files_route, "ingest", fail_ingest)
    response = client.post(
        endpoint,
        files={"file": ("notes.txt", b"hello", "text/plain")},
        data={"project_id": "starter", "attach_scope": "project"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "ingest_rejected"
    assert body["error"]["message"] == "The file could not be accepted."
    serialized = json.dumps(body, ensure_ascii=False)
    for marker in (
        "COMSPEC",
        "openai-api-key",
        UNICODE_VALUE,
        *UNICODE_PATHS,
        "/usr/local/share/company/private.docx",
        "/builds/acme/project/private.docx",
        "/Users/alice/Documents/private-notes",
        "/Volumes/PrivateDrive/tenant/record",
    ):
        assert marker not in serialized


@pytest.mark.parametrize("status_code", [401, 403])
def test_apify_auth_failure_projection_and_persistence_are_nonpositive(boundary_client, monkeypatch, status_code):
    client, db, _root = boundary_client
    from app.services.social.instagram import apify_client

    with connect(db) as conn:
        ref = ConfigService.store_apify_token("vault-apify-edge-token", conn=conn)
        registry.register_integration(
            registry.IntegrationRecord(
                integration_id="apify-security-edge",
                capability=instagram_capabilities.CAP_INSTAGRAM_PUBLIC_PROFILE,
                provider="apify",
                scope="installation",
                status="connected",
                config={"actor_id": "apify/instagram-profile-scraper"},
                secret_ref=ref,
                last_health=None,
            ),
            conn=conn,
        )

    class Response:
        def __init__(self, code: int):
            self.status_code = code

        def json(self):
            return {}

    monkeypatch.setattr(
        apify_client.httpx,
        "get",
        lambda *args, **kwargs: Response(status_code),
    )
    response = client.post(
        "/api/settings/credentials/test",
        json={"target": "apify", "project_id": "starter"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["credential_status"] == "auth_error"
    assert data["capability_health"] == "error"
    assert data["provider_health"] in {"error", "unavailable"}
    assert data["provider_health"] not in {"ok", "healthy", "active"}
    with connect(db) as conn:
        assert ConfigService.get_setting("health_apify_provider", conn=conn) == data["provider_health"]
        assert ConfigService.get_setting("health_apify_credential", conn=conn) == "auth_error"
