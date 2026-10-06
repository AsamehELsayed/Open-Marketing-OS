"""DEV-018: exercise the real OpenRouter adapter through loopback only."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


SUCCESS = {
    "id": "chatcmpl-synthetic",
    "object": "chat.completion",
    "created": 1,
    "model": "stealth/space-bunny-alpha",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "SYNTHETIC PLUMBING ANSWER"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9},
}


class FakeOpenRouter:
    def __init__(self, *, status=200, body=None, delay=0.0, statuses=None):
        self.status = status
        self.body = SUCCESS if body is None else body
        self.delay = delay
        self.statuses = list(statuses or [])
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = None
                # Record only the auth-header boolean; never retain its value.
                owner.requests.append({
                    "method": self.command,
                    "path": self.path,
                    "authorization_present": bool(self.headers.get("Authorization")),
                    "content_type": self.headers.get("Content-Type"),
                    "payload": payload,
                })
                if owner.delay:
                    time.sleep(owner.delay)
                status = owner.statuses.pop(0) if owner.statuses else owner.status
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if status == 200:
                    output = owner.body
                else:
                    output = {"error": {"message": "synthetic fixture error", "type": "fixture_error", "code": "fixture"}}
                data = output if isinstance(output, bytes) else json.dumps(output).encode()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_args):
                # Avoid writing request headers or bodies to test logs.
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/api/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


@pytest.fixture
def fake_openrouter(monkeypatch):
    from app.services.llm import openrouter_provider as module

    fake = FakeOpenRouter()
    # The test's adapter can only reach the server bound to this loopback port.
    monkeypatch.setattr(module, "BASE_URL", fake.url)
    monkeypatch.setattr(module, "get_key", lambda: "synthetic-test-credential")
    try:
        yield fake, module
    finally:
        fake.close()


def _complete(module, *, timeout_s=2):
    provider = module.OpenRouterProvider(model="stealth/space-bunny-alpha", timeout_s=timeout_s)
    return provider.complete(
        system="Synthetic system instruction",
        messages=[{"role": "user", "content": "Synthetic user request"}],
        tools=[], opts={"model": "stealth/space-bunny-alpha"})


def test_success_uses_real_adapter_and_expected_sanitized_request(fake_openrouter):
    fake, module = fake_openrouter
    response = _complete(module)

    assert response.text == "SYNTHETIC PLUMBING ANSWER"
    assert response.usage["requested_model"] == "stealth/space-bunny-alpha"
    assert response.usage["actual_model"] == "stealth/space-bunny-alpha"
    assert response.usage["total_tokens"] == 9
    assert len(fake.requests) == 1
    request = fake.requests[0]
    assert request["method"] == "POST"
    assert request["path"] == "/api/v1/chat/completions"
    assert request["authorization_present"] is True
    assert request["content_type"].startswith("application/json")
    assert request["payload"]["model"] == "stealth/space-bunny-alpha"
    assert request["payload"]["messages"] == [
        {"role": "system", "content": "Synthetic system instruction"},
        {"role": "user", "content": "Synthetic user request"},
    ]
    assert request["payload"].get("stream", False) is False
    assert request["payload"]["usage"] == {"include": True}


@pytest.mark.parametrize(("status", "message"), [
    (401, "auth error"),
    (404, "model/request configuration error"),
    (429, "limit/credit issue"),
])
def test_provider_errors_are_safely_normalized(fake_openrouter, status, message):
    fake, module = fake_openrouter
    fake.status = status

    with pytest.raises(module.OpenRouterError) as caught:
        _complete(module)

    assert caught.value.status == status
    assert message in str(caught.value)
    assert caught.value.failure_kind == {
        401: "authentication", 404: "request_configuration", 429: "rate_limit"}[status]
    assert len(fake.requests) == (2 if status == 429 else 1)
    assert all(item["authorization_present"] for item in fake.requests)
    assert "synthetic-test-credential" not in str(caught.value)


def test_timeout_does_not_retry_or_fallback(fake_openrouter):
    fake, module = fake_openrouter
    fake.delay = 3.0
    events = []

    with pytest.raises(module.OpenRouterError) as caught:
        provider = module.OpenRouterProvider(model="stealth/space-bunny-alpha", timeout_s=2)
        provider.complete(
            system="Synthetic system instruction",
            messages=[{"role": "user", "content": "Synthetic user request"}],
            tools=[], opts={"model": "stealth/space-bunny-alpha",
                            "_transport_observer": events.append,
                            "call_id": "call-timeout", "turn_id": "turn-timeout"})

    assert str(caught.value) == "OpenRouter request timed out"
    assert caught.value.failure_kind == "timeout"
    assert len(fake.requests) == 1
    assert [e["attempt_index"] for e in events
            if e["state"] == "provider_request_started"] == [1]
    assert not [e for e in events if e["state"] == "provider_response_headers"]
    failed = [e for e in events if e["state"] == "provider_request_failed"]
    assert failed[-1]["network_phase"] == "timeout"


def test_malformed_success_body_is_parse_failure_without_retry(fake_openrouter):
    fake, module = fake_openrouter
    fake.body = b"{not-json"

    with pytest.raises(module.OpenRouterError) as caught:
        _complete(module)

    assert caught.value.status is None
    assert str(caught.value) == "OpenRouter returned an invalid response"
    assert caught.value.failure_kind == "parse_error"
    assert len(fake.requests) == 1


def test_client_uses_requested_timeout_and_disables_hidden_sdk_retries(fake_openrouter):
    _fake, module = fake_openrouter
    client = module.OpenRouterProvider()._open_client(7)

    assert client.max_retries == 0
    assert client.timeout == 7
