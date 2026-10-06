"""OpenAI Responses-API provider (v0.2 §3). Only module that imports openai SDK.

Behavior:
- complete() maps to internal LLMResponse; transient 429/5xx retried once.
- Missing API key or missing SDK -> raises RuntimeError so the loop can
  fall back to deterministic mode (never crashes chat).
- supports_server_websearch() True only when OPENAI_SERVER_WEBSEARCH=1.
"""
import os
import time

from .base import LLMResponse, ToolCall

TRANSIENT = (429, 500, 502, 503, 504)


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str = "gpt-4o-mini", timeout_s: int = 45):
        self.model = model
        self.timeout_s = timeout_s

    def supports_server_websearch(self) -> bool:
        return os.getenv("OPENAI_SERVER_WEBSEARCH", "0") == "1"

    def complete(self, *, system: str, messages: list, tools: list, opts: dict | None = None) -> LLMResponse:
        try:
            from app.services.config_service import ConfigService
            api_key = ConfigService.get_openai_api_key()
        except Exception:
            api_key = ""
        if not api_key:
            raise RuntimeError("Cloud AI is not configured. Connect OpenAI in Settings.")
        try:
            from openai import OpenAI
        except Exception:
            raise RuntimeError("OpenAI integration is unavailable")
        model = (opts or {}).get("model", self.model)
        timeout = (opts or {}).get("timeout_s", self.timeout_s)
        fn_tools = []
        for t in tools:
            spec = t if isinstance(t, dict) else {"name": t.name, "description": t.description,
                                                 "parameters": t.parameters}
            fn_tools.append({"type": "function", "function": {
                "name": spec["name"], "description": spec.get("description", ""),
                "parameters": spec.get("parameters", {"type": "object", "properties": {}})}})
        input_blocks = [{"role": "system", "content": system}]
        for m in messages:
            input_blocks.append({"role": m.get("role", "user"), "content": m.get("content", "")})
        last_err: Exception | None = None
        for attempt in (0, 1):
            try:
                client = OpenAI(api_key=api_key, timeout=timeout)
                resp = client.responses.create(model=model, input=input_blocks, tools=fn_tools or None)
                return _to_llm_response(resp)
            except Exception as exc:
                last_err = exc
                if _status(exc) in TRANSIENT and attempt == 0:
                    time.sleep(1.0)
                    continue
                if _status(exc) in (401, 403):
                    raise RuntimeError("OpenAI rejected the stored credential") from None
                raise RuntimeError("OpenAI request failed") from None
        raise RuntimeError("OpenAI request failed")


def _status(exc: Exception) -> int | None:
    for attr in ("status_code", "status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    return None


def _to_llm_response(resp) -> LLMResponse:
    text_parts, calls, usage = [], [], {}
    try:
        usage = {"input": getattr(resp, "input_tokens", 0), "output": getattr(resp, "output_tokens", 0)}
    except Exception:
        pass
    for item in getattr(resp, "output", []) or []:
        kind = getattr(item, "type", "")
        if kind == "message":
            for c in getattr(item, "content", []) or []:
                if getattr(c, "type", "") in ("output_text", "text"):
                    text_parts.append(getattr(c, "text", ""))
        elif kind == "function_call":
            import json
            try:
                args = json.loads(getattr(item, "arguments", "") or "{}")
            except Exception:
                args = {}
            calls.append(ToolCall(name=getattr(item, "name", ""), arguments=args,
                                  call_id=getattr(item, "call_id", "")))
    return LLMResponse(text="\n".join(text_parts) if text_parts else None, tool_calls=calls, usage=usage)


def build_provider(model: str = "gpt-4o-mini", timeout_s: int = 45, script: list | None = None):
    """Factory: fake when LLM_PROVIDER=fake (tests), else OpenAI."""
    if os.getenv("LLM_PROVIDER", "openai") == "fake":
        from .fake_provider import FakeProvider
        return FakeProvider(script=script)
    return OpenAIProvider(model=model, timeout_s=timeout_s)
