"""DEV-005 W3 remediation — focused contract gaps (TDD RED spec).

Scope: W3 only. Fake loopback transport, no network/server/download.
- Incremental SSE: provider must consume chunks as received (sse_iter),
  reassemble split lines, emit delta callbacks in arrival order.
- Tools: supplied definitions forwarded OpenAI-style; returned tool_calls
  parsed into LLMResponse without inventing fields.
- Timeout: per-call timeout_s / health timeout honored (forwarded to transport).
- Preserved: loopback-only, null unknown usage, no reasoning_content exposure.
"""
import json

from app.services.llm.base import ToolCall, ToolSpec


def _provider(transport, **kw):
    from app.services.llm.local_llama import LocalLlamaProvider
    kw.setdefault("model", "qwen3-8b-Q4_K_M")
    kw.setdefault("quantization", "Q4_K_M")
    return LocalLlamaProvider(transport=transport, **kw)


class ChunkedLoopback:
    """Yields SSE bytes in split chunks (mid-line splits included)."""

    def __init__(self, chunks, *, model_id="qwen3-8b-Q4_K_M"):
        self._chunks = list(chunks)
        self.model_id = model_id
        self.calls = []

    def request(self, method, path, *, body=None, stream=False, timeout_s=None):
        self.calls.append((method, path, stream, timeout_s))
        if method == "GET" and path == "/v1/models":
            return {"status_code": 200,
                    "json": {"data": [{"id": self.model_id}]}, "sse": b""}
        if method == "POST" and path == "/v1/chat/completions" and stream:
            return {"status_code": 200, "json": {},
                    "sse_iter": iter(self._chunks)}
        if method == "POST" and path == "/v1/chat/completions":
            return {"status_code": 200,
                    "json": {"choices": [{"message": {"content": "FALLBACK-SHOULD-NOT-HAPPEN"}}],
                             "model": self.model_id}, "sse": b""}
        return {"status_code": 404, "json": {}, "sse": b""}


def test_incremental_streaming_processes_chunks_as_received_in_order():
    line1 = "data: " + json.dumps({"choices": [{"delta": {"content": "Hello "}}]}) + "\n"
    line2 = "data: " + json.dumps({"choices": [{"delta": {"content": "world"}}]}) + "\n"
    done = "data: [DONE]\n"
    blob = (line1 + line2 + done).encode("utf-8")
    # Split mid-line to prove reassembly (not whole-blob buffering).
    chunks = [blob[:15], blob[15:40], blob[40:55], blob[55:]]
    fake = ChunkedLoopback(chunks)
    prov = _provider(fake.request)
    events = []
    resp = prov.complete_streaming(system="s",
                                  messages=[{"role": "user", "content": "hi"}],
                                  tools=[], opts={},
                                  on_event=lambda k, v: events.append((k, v)))
    assert resp.text == "Hello world"
    deltas = [v for k, v in events if k == "delta"]
    assert deltas == ["Hello ", "Hello world"]
    # No fallback path taken: exactly one POST (the stream), plus discovery.
    posts = [c for c in fake.calls if c[0] == "POST"]
    assert len(posts) == 1


def test_incremental_streaming_ignores_reasoning_content_in_chunks():
    line1 = ("data: " + json.dumps({"choices": [{"delta": {
        "content": "Hi", "reasoning_content": "SECRET-THINK"}}]}) + "\n")
    done = "data: [DONE]\n"
    blob = (line1 + done).encode("utf-8")
    fake = ChunkedLoopback([blob[:10], blob[10:]])
    prov = _provider(fake.request)
    events = []
    resp = prov.complete_streaming(system="s",
                                  messages=[{"role": "user", "content": "hi"}],
                                  tools=[], opts={},
                                  on_event=lambda k, v: events.append((k, v)))
    assert resp.text == "Hi"
    assert "SECRET-THINK" not in resp.text
    assert all("SECRET-THINK" not in v for _, v in events)


class ToolLoopback:
    def __init__(self):
        self.calls = []
        self.bodies = []

    def request(self, method, path, *, body=None, stream=False, timeout_s=None):
        self.calls.append((method, path, stream, timeout_s))
        if method == "GET" and path == "/v1/models":
            return {"status_code": 200,
                    "json": {"data": [{"id": "qwen3-8b-Q4_K_M"}]}, "sse": b""}
        if method == "POST" and path == "/v1/chat/completions":
            self.bodies.append(dict(body or {}))
            return {"status_code": 200, "json": {
                "model": "qwen3-8b-Q4_K_M",
                "choices": [{"message": {
                    "content": None,
                    "tool_calls": [{"id": "call_1", "type": "function",
                                    "function": {"name": "get_weather",
                                                 "arguments": '{"city": "Cairo"}'}}]},
                    "finish_reason": "tool_calls"}]}, "sse": b""}
        return {"status_code": 404, "json": {}, "sse": b""}


def test_tool_definitions_forwarded_and_tool_calls_parsed_without_invention():
    fake = ToolLoopback()
    prov = _provider(fake.request)
    tools = [ToolSpec(name="get_weather", description="Get weather",
                      parameters={"type": "object",
                                  "properties": {"city": {"type": "string"}}})]
    resp = prov.complete(system="s",
                         messages=[{"role": "user", "content": "weather in Cairo?"}],
                         tools=tools, opts={})
    sent = fake.bodies[-1]
    assert "tools" in sent
    assert sent["tools"] == [{"type": "function", "function": {
        "name": "get_weather", "description": "Get weather",
        "parameters": {"type": "object",
                       "properties": {"city": {"type": "string"}}}}}]
    assert len(resp.tool_calls) == 1
    call = resp.tool_calls[0]
    assert isinstance(call, ToolCall)
    assert call.name == "get_weather"
    assert call.arguments == {"city": "Cairo"}
    assert call.call_id == "call_1"


def test_tool_response_missing_fields_not_invented():
    class BareLoopback(ToolLoopback):
        def request(self, method, path, *, body=None, stream=False, timeout_s=None):
            self.calls.append((method, path, stream, timeout_s))
            if method == "GET" and path == "/v1/models":
                return {"status_code": 200,
                        "json": {"data": [{"id": "qwen3-8b-Q4_K_M"}]}, "sse": b""}
            if method == "POST" and path == "/v1/chat/completions":
                self.bodies.append(dict(body or {}))
                return {"status_code": 200, "json": {
                    "model": "qwen3-8b-Q4_K_M",
                    "choices": [{"message": {
                        "content": "done",
                        "tool_calls": [{"type": "function",
                                        "function": {"name": "lookup"}}]},
                        "finish_reason": "tool_calls"}]}, "sse": b""}
            return {"status_code": 404, "json": {}, "sse": b""}

    fake = BareLoopback()
    prov = _provider(fake.request)
    resp = prov.complete(system="s", messages=[{"role": "user", "content": "hi"}],
                         tools=[{"name": "lookup"}], opts={})
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "lookup"
    assert resp.tool_calls[0].arguments == {}
    assert resp.tool_calls[0].call_id == ""


class TimeoutLoopback:
    def __init__(self):
        self.seen = {}

    def request(self, method, path, *, body=None, stream=False, timeout_s=None):
        self.seen[(method, path, stream)] = timeout_s
        if path == "/health":
            return {"status_code": 200, "json": {"status": "ok"}, "sse": b""}
        if path == "/v1/models":
            return {"status_code": 200,
                    "json": {"data": [{"id": "qwen3-8b-Q4_K_M"}]}, "sse": b""}
        return {"status_code": 200, "json": {
            "model": "qwen3-8b-Q4_K_M",
            "choices": [{"message": {"content": "ok"}}]}, "sse": b""}


def test_per_call_timeout_override_honored():
    fake = TimeoutLoopback()
    prov = _provider(fake.request, timeout_s=45, health_timeout_s=5)
    prov.complete(system="s", messages=[{"role": "user", "content": "hi"}],
                 tools=[], opts={"timeout_s": 99})
    assert fake.seen[("POST", "/v1/chat/completions", False)] == 99
    prov.health(timeout_s=2)
    assert fake.seen[("GET", "/health", False)] == 2
