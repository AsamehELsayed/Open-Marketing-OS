"""DEV-020 adapter telemetry checks; all provider traffic is loopback-only."""
from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest


SUCCESS = {
    "id": "chatcmpl-fixture", "object": "chat.completion", "created": 1,
    "model": "stealth/space-bunny-alpha",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "fixture answer"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9},
}


class Loopback:
    def __init__(self, statuses=(200,), body=None, delay=0.0):
        self.statuses = list(statuses)
        self.body = SUCCESS if body is None else body
        self.delay = delay
        self.received = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                owner.received += 1
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                if owner.delay:
                    time.sleep(owner.delay)
                status = owner.statuses.pop(0) if owner.statuses else 200
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("X-OpenRouter-Request-Id", "req-fixture-1")
                self.end_headers()
                output = owner.body if status == 200 else {
                    "error": {"message": "synthetic error", "type": "fixture_error", "code": "fixture"}}
                data = output if isinstance(output, bytes) else json.dumps(output).encode()
                self.wfile.write(data)

            def log_message(self, *_args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/api/v1"
        assert urlsplit(self.url).hostname == "127.0.0.1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


@pytest.fixture
def adapter(monkeypatch):
    from app.services.llm import openrouter_provider as module
    fake = Loopback()
    monkeypatch.setattr(module, "BASE_URL", fake.url)
    monkeypatch.setattr(module, "get_key", lambda: "DEV020-SYNTHETIC-KEY")
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    events = []
    options = {"model": "stealth/space-bunny-alpha", "call_id": "call-fixture-01",
               "turn_id": "turn-fixture-01", "_transport_observer": events.append}
    try:
        yield fake, module, events, options
    finally:
        fake.close()


def test_counts_physical_success_at_httpx_boundary_and_sanitizes(adapter):
    fake, module, events, options = adapter
    result = module.OpenRouterProvider().complete(system="private prompt", messages=[], tools=[], opts=options)
    assert result.text == "fixture answer"
    assert fake.received == 1
    started = [e for e in events if e["state"] == "provider_request_started"]
    headers = [e for e in events if e["state"] == "provider_response_headers"]
    assert [e["attempt_index"] for e in started] == [1]
    assert all((e["call_id"], e["turn_id"]) == ("call-fixture-01", "turn-fixture-01")
               for e in events)
    assert [e["attempt_index"] for e in headers] == [1]
    assert headers[0]["http_status"] == 200
    assert headers[0]["provider_request_id"] == "req-fixture-1"
    assert events[-1]["state"] == "provider_response_completed"
    serialized = json.dumps(events)
    for forbidden in ("DEV020-SYNTHETIC-KEY", "private prompt", "Authorization", "fixture answer"):
        assert forbidden not in serialized


def test_retry_attempt_indices_match_server_received_posts(adapter):
    fake, module, events, options = adapter
    fake.statuses = [429, 200]
    result = module.OpenRouterProvider().complete(system="s", messages=[], tools=[], opts=options)
    assert result.text == "fixture answer"
    assert fake.received == 2
    started = [e for e in events if e["state"] == "provider_request_started"]
    headers = [e for e in events if e["state"] == "provider_response_headers"]
    assert [e["attempt_index"] for e in started] == [1, 2]
    assert [e["http_status"] for e in headers] == [429, 200]


@pytest.mark.parametrize("status", [401, 402, 403, 404])
def test_nonretryable_http_status_is_one_exact_transport_attempt(adapter, status):
    fake, module, events, options = adapter
    fake.statuses = [status]
    with pytest.raises(module.OpenRouterError) as caught:
        module.OpenRouterProvider().complete(system="s", messages=[], tools=[], opts=options)
    assert caught.value.status == status
    assert fake.received == 1
    assert [e["attempt_index"] for e in events
            if e["state"] == "provider_request_started"] == [1]
    assert [e["http_status"] for e in events
            if e["state"] == "provider_response_headers"] == [status]


def test_500_retry_is_bounded_and_each_transport_attempt_is_visible(adapter):
    fake, module, events, options = adapter
    fake.statuses = [500, 200]
    result = module.OpenRouterProvider().complete(system="s", messages=[], tools=[], opts=options)
    assert result.text == "fixture answer"
    assert fake.received == 2
    assert [e["attempt_index"] for e in events
            if e["state"] == "provider_request_started"] == [1, 2]
    assert [e["http_status"] for e in events
            if e["state"] == "provider_response_headers"] == [500, 200]


def test_malformed_response_keeps_http_response_and_parse_failure(adapter):
    fake, module, events, options = adapter
    fake.body = b"{not-json"
    with pytest.raises(module.OpenRouterError) as caught:
        module.OpenRouterProvider().complete(system="s", messages=[], tools=[], opts=options)
    assert caught.value.failure_kind == "parse_error"
    assert fake.received == 1
    headers = [e for e in events if e["state"] == "provider_response_headers"]
    failures = [e for e in events if e["state"] == "provider_request_failed"]
    assert [e["http_status"] for e in headers] == [200]
    assert failures[-1]["network_phase"] == "response_parse"
    assert failures[-1]["reason"] == "response_parse_failure"


@pytest.mark.parametrize(("cause", "phase"), [
    (socket.gaierror("synthetic DNS failure"), "dns"),
    (ConnectionRefusedError("synthetic refused"), "connection_refused"),
])
def test_dns_and_refused_connect_failures_are_counted_at_transport_entry(
        adapter, monkeypatch, cause, phase):
    fake, module, events, options = adapter
    import httpx

    def fail_transport(_self, request):
        raise httpx.ConnectError("synthetic transport failure", request=request) from cause

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", fail_transport)
    with pytest.raises(module.OpenRouterError) as caught:
        module.OpenRouterProvider().complete(system="s", messages=[], tools=[], opts=options)
    assert caught.value.status is None
    assert fake.received == 0
    started = [e for e in events if e["state"] == "provider_request_started"]
    failed = [e for e in events if e["state"] == "provider_request_failed"]
    assert len(started) == 1
    assert failed[-1]["network_phase"] == phase


def test_pretransport_credential_failure_has_zero_attempts(adapter, monkeypatch):
    fake, module, events, options = adapter
    monkeypatch.setattr(module, "get_key", lambda: "")
    with pytest.raises(module.OpenRouterError):
        module.OpenRouterProvider().complete(system="s", messages=[], tools=[], opts=options)
    assert fake.received == 0
    assert not [e for e in events if e["state"] == "provider_request_started"]
    failed = [e for e in events if e["state"] == "provider_request_failed"]
    assert failed[-1]["attempt_index"] == 0
    assert failed[-1]["network_phase"] == "credential_vault"


def test_vault_exception_is_safe_and_stops_before_transport(adapter, monkeypatch):
    fake, module, events, options = adapter
    monkeypatch.setattr(module, "get_key",
                        lambda: (_ for _ in ()).throw(RuntimeError("SECRET vault detail")))
    with pytest.raises(module.OpenRouterError) as caught:
        module.OpenRouterProvider().complete(system="s", messages=[], tools=[], opts=options)
    assert caught.value.failure_kind == "credential_vault"
    assert fake.received == 0
    failed = [e for e in events if e["state"] == "provider_request_failed"]
    assert failed[-1]["network_phase"] == "credential_vault"
    assert failed[-1]["credential_reference_resolved"] is False
    assert "SECRET" not in json.dumps(events)


def test_client_initialization_failure_is_pretransport(adapter, monkeypatch):
    fake, module, events, options = adapter
    import httpx

    def fail_client(**_kwargs):
        raise RuntimeError("SECRET client details")

    monkeypatch.setattr(httpx, "Client", fail_client)
    with pytest.raises(module.OpenRouterError) as caught:
        module.OpenRouterProvider().complete(system="s", messages=[], tools=[], opts=options)
    assert caught.value.failure_kind == "client_initialization"
    assert fake.received == 0
    failed = [e for e in events if e["state"] == "provider_request_failed"]
    assert failed[-1]["network_phase"] == "client_initialization"
    assert failed[-1]["client_initialized"] is False
    assert "SECRET" not in json.dumps(events)


def test_request_serialization_failure_has_zero_http_attempts(adapter, monkeypatch):
    fake, module, events, options = adapter

    def fail_payload(*_args, **_kwargs):
        raise TypeError("SECRET serialization details")

    monkeypatch.setattr(module.OpenRouterProvider, "_payload", staticmethod(fail_payload))
    with pytest.raises(module.OpenRouterError) as caught:
        module.OpenRouterProvider().complete(system="s", messages=[], tools=[], opts=options)
    assert caught.value.failure_kind == "request_failure"
    assert fake.received == 0
    assert not [e for e in events if e["state"] == "provider_request_started"]
    failed = [e for e in events if e["state"] == "provider_request_failed"]
    assert failed[-1]["network_phase"] == "request_serialization"
    assert failed[-1]["request_serialized"] is False
    assert "SECRET" not in json.dumps(events)


def test_streaming_uses_same_transport_boundary(adapter):
    fake, module, events, options = adapter
    fake.body = b'data: {"id":"x","model":"stealth/space-bunny-alpha","choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'
    result = module.OpenRouterProvider().complete_streaming(
        system="s", messages=[], tools=[], opts=options)
    assert result.text == "ok"
    assert fake.received == 1
    assert [e["attempt_index"] for e in events if e["state"] == "provider_request_started"] == [1]
