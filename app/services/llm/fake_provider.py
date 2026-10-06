"""FakeProvider: scripted LLMResponse queue for deterministic tests. Zero network."""
from .base import LLMResponse


class FakeProvider:
    name = "fake"

    def __init__(self, script: list | None = None):
        self.script: list = list(script or [])
        self.chunks_script: list = []
        self.calls: list = []
        self._last_chunks = None

    def queue(self, text: str | None = None, tool_calls: list | None = None, usage: dict | None = None, chunks: list[str] | None = None):
        self.script.append(LLMResponse(text=text, tool_calls=list(tool_calls or []), usage=dict(usage or {})))
        self.chunks_script.append(list(chunks) if chunks is not None else None)

    def supports_server_websearch(self) -> bool:
        return False

    def complete(self, *, system: str, messages: list, tools: list, opts: dict | None = None) -> LLMResponse:
        self.calls.append({"system": system, "messages": list(messages),
                           "tools": [t["name"] if isinstance(t, dict) else t.name for t in tools],
                           "opts": dict(opts or {})})
        if not self.script:
            self._last_chunks = None
            return LLMResponse(text="Fake fallback answer.", tool_calls=[], usage={})
        if self.chunks_script:
            self._last_chunks = self.chunks_script.pop(0)
        else:
            self._last_chunks = None
        return self.script.pop(0)

    def complete_streaming(self, *, system: str, messages: list, tools: list,
                           opts: dict | None = None, on_event=None) -> LLMResponse:
        resp = self.complete(system=system, messages=messages, tools=tools, opts=opts)
        chunks = getattr(self, "_last_chunks", None)
        if chunks:
            for c in chunks:
                if callable(on_event) and c:
                    try:
                        on_event("delta", c)
                    except Exception:
                        pass
        elif callable(on_event) and resp.text:
            try:
                on_event("delta", resp.text)
            except Exception:
                pass
        return resp
