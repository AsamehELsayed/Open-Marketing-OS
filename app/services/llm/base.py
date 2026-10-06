"""LLMProvider protocol + value types. No SDK imports here."""
from typing import NamedTuple, Protocol


class ToolSpec(NamedTuple):
    name: str
    description: str
    parameters: dict
    side_effect: str = "green"


class ToolCall(NamedTuple):
    name: str
    arguments: dict
    call_id: str = ""


class LLMResponse(NamedTuple):
    text: str | None
    tool_calls: list
    usage: dict


class LLMProvider(Protocol):
    name: str

    def complete(self, *, system: str, messages: list, tools: list, opts: dict | None = None) -> LLMResponse:
        ...

    def supports_server_websearch(self) -> bool:
        ...


def stream_complete(provider, *, system: str, messages: list, tools: list,
                    opts: dict | None = None, on_event=None) -> LLMResponse:
    """Optional streaming interface. Providers may implement `complete_streaming`
    yielding ("delta", cumulative_text) / ("status", line) tuples for REAL
    incremental output. Default: single complete() call, one honest final event.
    Never synthesize fake token pacing here."""
    fn = getattr(provider, "complete_streaming", None)
    if callable(fn):
        return fn(system=system, messages=messages, tools=tools, opts=opts, on_event=on_event)
    resp = provider.complete(system=system, messages=messages, tools=tools, opts=opts)
    if callable(on_event) and resp.text:
        try:
            on_event("delta", resp.text)
        except Exception:
            pass
    return resp
