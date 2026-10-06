"""DEV-024 W2 answer assembly checks; all requests terminate at an injected fake."""
import json
from types import SimpleNamespace

from app.services.llm.base import ToolCall
from app.services.llm.local_llama import LocalLlamaProvider


MODEL = "offline-qwen-fixture"


def _event(data):
    return ("data: " + json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8")


class FakeLoopback:
    """Small OpenAI-compatible transport fixture; it cannot open a socket."""

    def __init__(self, stream_chunks, final=None):
        self.stream_chunks = list(stream_chunks)
        self.final = final or {"model": MODEL, "choices": [{"message": {"content": "fallback"}}]}
        self.calls = []

    def request(self, method, path, *, body=None, stream=False, timeout_s=None):
        self.calls.append((method, path, stream))
        if method == "GET" and path == "/v1/models":
            return {"status_code": 200, "json": {"data": [{"id": MODEL}]}}
        if method == "POST" and path == "/v1/chat/completions" and stream:
            return {"status_code": 200, "json": {}, "sse_iter": iter(self.stream_chunks)}
        if method == "POST" and path == "/v1/chat/completions":
            return {"status_code": 200, "json": self.final}
        raise AssertionError(f"unexpected fake request: {method} {path}")


def _provider(fake):
    return LocalLlamaProvider(transport=fake.request, model=MODEL)


def _stream(fake):
    events = []
    response = _provider(fake).complete_streaming(
        system="", messages=[{"role": "user", "content": "offline"}],
        tools=[], opts={}, on_event=lambda kind, text: events.append((kind, text)))
    return response, events


def test_ordered_and_repeated_delta_fragments_are_preserved_and_reconciled():
    # Repeated fragments are legitimate content, not event duplicates to drop.
    fake = FakeLoopback([_event({"choices": [{"delta": {"content": "ha"}}]}),
                         _event({"choices": [{"delta": {"content": "ha"}}]}),
                         _event({"choices": [{"delta": {"content": "!"}}]}),
                         b"data: [DONE]\n"])
    response, events = _stream(fake)
    assert response.text == "haha!"
    assert [text for kind, text in events if kind == "delta"] == ["ha", "haha", "haha!"]
    assert [text for kind, text in events if kind == "delta"][-1] == response.text
    assert len([call for call in fake.calls if call[0] == "POST"]) == 1


def test_split_byte_and_string_sse_chunks_preserve_mixed_arabic_english():
    answer = "مرحباً — NJM: growth 42%"
    blob = _event({"choices": [{"delta": {"content": answer}}]}) + b"data: [DONE]\n"
    # Split within the Arabic UTF-8 bytes and also provide a string chunk.
    split = blob.index("ر".encode("utf-8")) + 1
    fake = FakeLoopback([blob[:split], blob[split:split + 5], blob[split + 5:].decode("utf-8")])
    response, events = _stream(fake)
    assert response.text == answer
    assert [text for kind, text in events if kind == "delta"][-1] == answer
    assert "�" not in response.text


def test_reasoning_and_tool_call_fragments_never_enter_answer_text():
    fake = FakeLoopback([
        _event({"choices": [{"delta": {"reasoning_content": "private reasoning",
                                         "content": "I will check.",
                                         "tool_calls": [{"index": 0, "id": "call-1",
                                                         "function": {"name": "lookup", "arguments": "{"}}]}}]}),
        _event({"choices": [{"delta": {"reasoning": "also private",
                                         "tool_calls": [{"index": 0,
                                                         "function": {"arguments": "\"id\": 7}"}}]}}]}),
        b"data: [DONE]\n",
    ])
    response, events = _stream(fake)
    assert response.text == "I will check."
    assert response.tool_calls == [ToolCall(name="lookup", arguments={"id": 7}, call_id="call-1")]
    assert all("private" not in text and "lookup" not in text for _, text in events)


def test_missing_content_deltas_use_only_injected_final_response():
    expected = "offline final answer"
    fake = FakeLoopback([_event({"choices": [{"delta": {"reasoning_content": "hidden"}}]}),
                         b"data: [DONE]\n"],
                        final={"model": MODEL, "choices": [{"message": {"content": expected}}]})
    response, events = _stream(fake)
    assert response.text == expected
    assert [text for kind, text in events if kind == "delta"] == [expected]
    assert [call for call in fake.calls if call[0] == "POST"] == [
        ("POST", "/v1/chat/completions", True),
        ("POST", "/v1/chat/completions", False),
    ]


def test_openrouter_adapter_preserves_sdk_content_fragment_order_offline():
    # Replace the SDK boundary before invoking the adapter; no client/network is created.
    from app.services.llm.openrouter_provider import OpenRouterProvider

    chunks = [
        SimpleNamespace(model="fixture/actual", usage=None, choices=[SimpleNamespace(
            delta=SimpleNamespace(content="أهلاً ", reasoning="hidden", tool_calls=["ignored"]))]),
        SimpleNamespace(model="fixture/actual", usage=None, choices=[SimpleNamespace(
            delta=SimpleNamespace(content="NJM 42%", reasoning_content="hidden too"))]),
    ]

    class FakeCompletions:
        def create(self, **kwargs):
            assert kwargs["stream"] is True
            return iter(chunks)

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    provider = OpenRouterProvider(model="fixture/requested")
    provider._open_client = lambda timeout, *, observer=None: fake_client
    events = []
    response = provider.complete_streaming(
        system="", messages=[], tools=[], opts={},
        on_event=lambda kind, text: events.append((kind, text)))

    assert response.text == "أهلاً NJM 42%"
    assert response.usage["actual_model"] == "fixture/actual"
    assert [text for kind, text in events if kind == "delta"] == [
        "أهلاً ", "أهلاً NJM 42%"]

