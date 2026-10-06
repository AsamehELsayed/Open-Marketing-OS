"""DEV-005 W3 — provider-compatible adapter for a local llama.cpp
OpenAI-compatible server on loopback.

Contract (docs/v1/model-benchmark-plan.md §5, architecture.md §7, W0
app/contracts/model_call.py):
- Loopback only (LocalLlamaConfig fail-closed); stdlib HTTP, no new deps.
- /health readiness, /v1/models discovery, representative streaming chat.
- Actual reported model identity wins over configured guess; never invented.
- Usage maps provider fields when reported; missing legs stay None (never
  0-filled) so W0 ModelCall.totals_consistent() reads False, never a guess.
- Quantization/adapter metadata carried from config into usage + ModelCall.
- Timeouts surface as RuntimeError; non-2xx surfaces as RuntimeError.
  Per-call opts["timeout_s"] / health(timeout_s=) override config defaults.
- Supplied tool definitions forwarded OpenAI-style (tools=[{type:function}]);
  returned message.tool_calls parsed into ToolCall without inventing fields.
- Streaming is genuine incremental: live readline sse_iter (never resp.read()
  buffering), chunks processed as received with split-line reassembly;
  forwards only delta.content cumulatively; reasoning_content is
  dropped, never forwarded. No fake token pacing.
- Local API cost is $0.00; compute is not metered (pricing_version recorded).

Does NOT download models, start servers, train LoRA, or choose a winner.
"""
import codecs
import json
import time
import urllib.request
from collections.abc import Callable

from .base import LLMResponse, ToolCall
from .local_config import LOCAL_PRICING_VERSION, LocalLlamaConfig


class LocalLlamaProvider:
    """LLMProvider-compatible (name/complete/supports_server_websearch)."""

    name = "local"

    def __init__(self, transport: Callable | None = None, *,
                 base_url: str = "http://127.0.0.1:8080",
                 model: str = "", quantization: str = "", adapter: str = "",
                 timeout_s: int = 45, health_timeout_s: int = 5,
                 config: LocalLlamaConfig | None = None) -> None:
        if config is not None:
            self.config = config
            # Explicit kwargs override a passed config; empty means "keep config".
            if base_url != "http://127.0.0.1:8080":
                self.config = LocalLlamaConfig(
                    base_url=base_url, model=config.model or model,
                    quantization=config.quantization or quantization,
                    adapter=config.adapter or adapter,
                    timeout_s=config.timeout_s, health_timeout_s=config.health_timeout_s,
                    n_ctx=config.n_ctx, n_gpu_layers=config.n_gpu_layers,
                    extra_server_flags=config.extra_server_flags)
            else:
                if model:
                    self.config.model = model
                if quantization:
                    self.config.quantization = quantization
                if adapter:
                    self.config.adapter = adapter
        else:
            self.config = LocalLlamaConfig(
                base_url=base_url, model=model, quantization=quantization,
                adapter=adapter, timeout_s=timeout_s,
                health_timeout_s=health_timeout_s)
        self._transport = transport or self._http_transport

    # ---- transport ----

    def _effective_timeout(self, path: str, timeout_s: int | None) -> int:
        if timeout_s is not None:
            return max(1, int(timeout_s))
        if path == "/health":
            return self.config.health_timeout_s
        return self.config.timeout_s

    def _http_transport(self, method: str, path: str, *,
                        body: dict | None = None, stream: bool = False,
                        timeout_s: int | None = None) -> dict:
        """Stdlib loopback HTTP.

        Non-streaming returns {status_code, json, sse: b""}.
        Streaming returns {status_code, json: {}, sse_iter} where sse_iter
        yields raw response bytes incrementally as received (readline loop,
        never resp.read() buffering) so complete_streaming processes chunks
        live. Fake transports may return {sse: bytes} instead; the streaming
        parser accepts both.
        """
        import urllib.error
        url = self.config.base_url.rstrip("/") + path
        data = json.dumps(body or {}).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        timeout = self._effective_timeout(path, timeout_s)
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except TimeoutError:
            raise
        except Exception as e:
            if "timed out" in str(e).lower() or "timeout" in type(e).__name__.lower():
                raise TimeoutError(str(e)) from e
            raise RuntimeError(f"local llama request failed: {e}") from e
        if not stream:
            try:
                with resp:
                    raw = resp.read()
                    code = getattr(resp, "status", 200)
            except TimeoutError:
                raise
            except Exception as e:
                if "timed out" in str(e).lower() or "timeout" in type(e).__name__.lower():
                    raise TimeoutError(str(e)) from e
                raise RuntimeError(f"local llama request failed: {e}") from e
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                payload = {}
            return {"status_code": code, "json": payload, "sse": b""}
        # Streaming: genuine incremental line delivery, no whole-body read.
        code = getattr(resp, "status", 200)

        def _gen():
            try:
                while True:
                    line = resp.readline()
                    if not line:
                        break
                    yield line
            finally:
                try:
                    resp.close()
                except Exception:
                    pass

        return {"status_code": code, "json": {}, "sse_iter": _gen()}

    def _call(self, method: str, path: str, *,
              body: dict | None = None, stream: bool = False,
              timeout_s: int | None = None) -> dict:
        try:
            try:
                return self._transport(method, path, body=body, stream=stream,
                                       timeout_s=timeout_s)
            except TypeError as e:
                if "timeout_s" not in str(e):
                    raise
                # Fake-transport compatibility: older fakes lack timeout_s.
                return self._transport(method, path, body=body, stream=stream)
        except TimeoutError as e:
            raise RuntimeError(f"local llama call timed out: {e}") from e

    # ---- discovery ----

    def health(self, timeout_s: int | None = None) -> bool:
        try:
            res = self._call("GET", "/health", timeout_s=timeout_s)
        except RuntimeError:
            return False
        if res.get("status_code") != 200:
            return False
        status = str((res.get("json") or {}).get("status", "ok")).lower()
        return status in ("ok", "ready", "loaded", "success")

    def list_models(self, timeout_s: int | None = None) -> list:
        res = self._call("GET", "/v1/models", timeout_s=timeout_s)
        if res.get("status_code") != 200:
            raise RuntimeError(
                f"local llama /v1/models failed: HTTP {res.get('status_code')}")
        data = (res.get("json") or {}).get("data") or []
        return [str(m.get("id", "")) for m in data if isinstance(m, dict) and m.get("id")]

    def _resolve_model(self, timeout_s: int | None = None) -> str:
        """Actual reported identity wins; configured value is only a hint."""
        try:
            discovered = self.list_models(timeout_s=timeout_s)
        except RuntimeError:
            discovered = []
        if self.config.model and self.config.model in discovered:
            return self.config.model
        if discovered:
            return discovered[0]
        if self.config.model:
            return self.config.model
        raise RuntimeError("local llama server reports no models")

    # ---- chat ----

    def _messages(self, system: str, messages: list) -> list:
        blocks = []
        if system:
            blocks.append({"role": "system", "content": system})
        for m in messages or []:
            blocks.append({"role": (m.get("role") or "user"),
                           "content": str(m.get("content", ""))})
        return blocks

    @staticmethod
    def _map_usage(payload: dict, model: str, latency_ms: int,
                   quantization: str, adapter: str) -> dict:
        """Map OpenAI-compatible usage; absent legs stay None (never 0)."""
        raw = (payload or {}).get("usage") or {}
        details_prompt = raw.get("prompt_tokens_details") or {}
        details_comp = raw.get("completion_tokens_details") or {}
        # Server reported adapter status takes precedence over configured guess
        server_reported_adapter = (payload or {}).get("adapter")
        effective_adapter = adapter if server_reported_adapter is None else str(server_reported_adapter)
        return {
            "provider": "local",
            "model": model or payload.get("model", ""),
            "quantization": quantization,
            "adapter": effective_adapter,
            "input_tokens": raw.get("prompt_tokens"),
            "cached_tokens": details_prompt.get("cached_tokens"),
            "output_tokens": raw.get("completion_tokens"),
            "reasoning_tokens": details_comp.get("reasoning_tokens"),
            "total_tokens": raw.get("total_tokens"),
            "latency_ms": latency_ms,
            "estimated_cost_usd": 0.0,  # local API cost; compute not metered
            "pricing_version": LOCAL_PRICING_VERSION,
        }

    @staticmethod
    def _format_tools(tools: list | None) -> list | None:
        """Forward supplied definitions OpenAI-style; None when empty.

        Accepts ToolSpec NamedTuples or {"name","description","parameters"} dicts.
        Never invents entries: nameless definitions are skipped.
        """
        out = []
        for t in tools or []:
            if isinstance(t, dict):
                name = str(t.get("name") or "")
                if not name:
                    continue
                out.append({"type": "function", "function": {
                    "name": name,
                    "description": str(t.get("description") or ""),
                    "parameters": t.get("parameters")
                    if isinstance(t.get("parameters"), dict)
                    else {"type": "object", "properties": {}}}})
            else:
                try:
                    name = str(getattr(t, "name", "") or "")
                except Exception:
                    continue
                if not name:
                    continue
                try:
                    desc = str(getattr(t, "description", "") or "")
                except Exception:
                    desc = ""
                try:
                    params = getattr(t, "parameters", {"type": "object",
                                                       "properties": {}})
                except Exception:
                    params = {"type": "object", "properties": {}}
                if not isinstance(params, dict):
                    params = {"type": "object", "properties": {}}
                out.append({"type": "function", "function": {
                    "name": name, "description": desc, "parameters": params}})
        return out or None

    @staticmethod
    def _parse_tool_calls(payload: dict) -> list:
        """Parse message.tool_calls into ToolCall without inventing fields.

        Missing id -> ""; missing/non-dict arguments -> {}. Entries without a
        function name are skipped (never invented).
        """
        try:
            choice = ((payload or {}).get("choices") or [{}])[0]
        except Exception:
            return []
        if not isinstance(choice, dict):
            return []
        msg = choice.get("message") or {}
        if not isinstance(msg, dict):
            return []
        raw_calls = msg.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            return []
        out: list = []
        for rc in raw_calls:
            if not isinstance(rc, dict):
                continue
            fn = rc.get("function") or {}
            if not isinstance(fn, dict):
                fn = {}
            name = str(fn.get("name") or rc.get("name") or "")
            if not name:
                continue
            args_raw = fn.get("arguments", {})
            if isinstance(args_raw, dict):
                args = args_raw
            elif isinstance(args_raw, str):
                if not args_raw.strip():
                    args = {}
                else:
                    try:
                        parsed = json.loads(args_raw)
                    except Exception:
                        args = {}
                    else:
                        args = parsed if isinstance(parsed, dict) else {}
            else:
                args = {}
            call_id = str(rc.get("id") or rc.get("call_id") or "")
            out.append(ToolCall(name=name, arguments=args, call_id=call_id))
        return out

    @staticmethod
    def _sse_chunk_iter(res: dict):
        """Yield raw SSE byte chunks as received (fake-transport compatible)."""
        if res.get("sse_iter") is not None:
            for chunk in res["sse_iter"]:
                if chunk is None:
                    continue
                if isinstance(chunk, str):
                    yield chunk.encode("utf-8")
                else:
                    yield bytes(chunk)
            return
        blob = res.get("sse") or b""
        if blob:
            yield bytes(blob)

    @classmethod
    def _iter_sse_lines(cls, res: dict):
        """Reassemble complete 'data:' lines from arbitrary chunk splits."""
        buf = ""
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        for chunk in cls._sse_chunk_iter(res):
            # SSE transport chunks may end in the middle of a multibyte UTF-8
            # character. Keep decoder state across chunks so Arabic and other
            # non-ASCII content is not replaced at byte boundaries.
            buf += decoder.decode(chunk)
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                yield line
        buf += decoder.decode(b"", final=True)
        if buf.strip():
            yield buf

    def complete(self, *, system: str, messages: list, tools: list,
                 opts: dict | None = None) -> LLMResponse:
        timeout = (opts or {}).get("timeout_s", self.config.timeout_s)
        model = (opts or {}).get("model") or self._resolve_model(timeout_s=timeout)
        adapter_override = (opts or {}).get("adapter")
        effective_adapter = adapter_override if adapter_override is not None else self.config.adapter
        started = time.monotonic()
        body = {"model": model, "messages": self._messages(system, messages),
                "stream": False, "adapter": effective_adapter}
        formatted = self._format_tools(tools)
        if formatted is not None:
            body["tools"] = formatted
        res = self._call("POST", "/v1/chat/completions", body=body, stream=False,
                         timeout_s=timeout)
        latency_ms = int((time.monotonic() - started) * 1000)
        if res.get("status_code") != 200:
            raise RuntimeError(
                f"local llama chat failed: HTTP {res.get('status_code')}: "
                f"{str(res.get('json'))[:300]}")
        payload = res.get("json") or {}
        actual = str(payload.get("model") or model)
        text = None
        try:
            choice = (payload.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            text = msg.get("content")
        except Exception:
            text = None
        tool_calls = self._parse_tool_calls(payload)
        usage = self._map_usage(payload, actual, latency_ms,
                                self.config.quantization, effective_adapter)
        return LLMResponse(text=text, tool_calls=tool_calls, usage=usage)

    def complete_streaming(self, *, system: str, messages: list, tools: list,
                           opts: dict | None = None, on_event=None) -> LLMResponse:
        """REAL incremental streaming: process chunks as received.

        Consumes sse_iter chunk-by-chunk (live readline generator), reassembles
        split lines, forwards cumulative delta.content in arrival order, drops
        reasoning_content, and accumulates delta.tool_calls by index. Accepts
        legacy {sse: bytes} fakes. Falls back to complete() only when neither
        content nor tool_calls arrived — no fake pacing.
        """
        timeout = (opts or {}).get("timeout_s", self.config.timeout_s)
        model = (opts or {}).get("model") or self._resolve_model(timeout_s=timeout)
        adapter_override = (opts or {}).get("adapter")
        effective_adapter = adapter_override if adapter_override is not None else self.config.adapter
        started = time.monotonic()
        body = {"model": model, "messages": self._messages(system, messages),
                "stream": True, "adapter": effective_adapter}
        formatted = self._format_tools(tools)
        if formatted is not None:
            body["tools"] = formatted
        res = self._call("POST", "/v1/chat/completions", body=body, stream=True,
                         timeout_s=timeout)
        latency_ms = int((time.monotonic() - started) * 1000)
        if res.get("status_code") != 200:
            raise RuntimeError(
                f"local llama stream failed: HTTP {res.get('status_code')}")
        acc = ""
        streamed_tools: dict = {}
        final_payload: dict = {}
        for raw_line in self._iter_sse_lines(res):
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
                if isinstance(obj, dict):
                    if "adapter" in obj:
                        final_payload["adapter"] = obj["adapter"]
                    if obj.get("model"):
                        final_payload["model"] = obj["model"]
                    if obj.get("usage"):
                        final_payload["usage"] = obj["usage"]
            except Exception:
                continue
            try:
                choice = (obj.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                piece = delta.get("content")  # reasoning_content ignored
                for tc in delta.get("tool_calls") or []:
                    if not isinstance(tc, dict):
                        continue
                    try:
                        idx = int(tc.get("index", 0))
                    except Exception:
                        idx = 0
                    slot = streamed_tools.setdefault(
                        idx, {"id": "", "name": "", "args": ""})
                    if tc.get("id") and not slot["id"]:
                        slot["id"] = str(tc["id"])
                    fn = tc.get("function") or {}
                    if isinstance(fn, dict):
                        if fn.get("name") and not slot["name"]:
                            slot["name"] = str(fn["name"])
                        frag = fn.get("arguments")
                        if isinstance(frag, str):
                            slot["args"] += frag
            except Exception:
                piece = None
            if not isinstance(piece, str) or not piece:
                continue
            # OpenAI-compatible delta.content is an incremental fragment.
            # Repeated text is valid output (for example "ha", "ha" ->
            # "haha"); substring-based deduplication silently loses it.
            acc += piece
            if callable(on_event) and acc:
                try:
                    on_event("delta", acc)
                except Exception:
                    pass
        tool_calls: list = []
        for idx in sorted(streamed_tools):
            slot = streamed_tools[idx]
            if not slot["name"]:
                continue
            try:
                args = json.loads(slot["args"]) if slot["args"].strip() else {}
            except Exception:
                args = {}
            if not isinstance(args, dict):
                args = {}
            tool_calls.append(ToolCall(name=slot["name"], arguments=args,
                                       call_id=slot["id"]))
        if not acc and not tool_calls:
            # No incremental content: single honest fallback, no fake pacing.
            response = self.complete(system=system, messages=messages,
                                     tools=tools, opts=opts)
            # Keep the event stream reconciled with the returned answer when
            # the server supplied no content deltas and complete() recovered it.
            if callable(on_event) and response.text:
                try:
                    on_event("delta", response.text)
                except Exception:
                    pass
            return response
        usage = self._map_usage(final_payload, final_payload.get("model", model), latency_ms,
                                self.config.quantization, effective_adapter)
        return LLMResponse(text=acc or None, tool_calls=tool_calls, usage=usage)

    def supports_server_websearch(self) -> bool:
        return False

    # ---- observability bridge (W0 ModelCall; unknown stays unknown) ----

    def to_model_call(self, resp: LLMResponse, *, turn_id: str, project_id: str,
                      route_mode: str = "LOCAL",
                      route_reason: str = "local llama.cpp loopback") :
        from app.contracts.model_call import ModelCall
        usage = resp.usage or {}
        return ModelCall(
            turn_id=turn_id, project_id=project_id, provider="local",
            model=str(usage.get("model") or self.config.model or "unknown"),
            adapter=str(usage.get("adapter") if usage.get("adapter") is not None
                        else self.config.adapter),
            quantization=str(usage.get("quantization")
                             if usage.get("quantization") is not None
                             else self.config.quantization),
            route_mode=route_mode, route_reason=route_reason,
            input_tokens=usage.get("input_tokens"),
            cached_tokens=usage.get("cached_tokens"),
            output_tokens=usage.get("output_tokens"),
            reasoning_tokens=usage.get("reasoning_tokens"),
            total_tokens=usage.get("total_tokens"),
            latency_ms=int(usage.get("latency_ms") or 0),
            estimated_cost_usd=0.0,
            pricing_version=str(usage.get("pricing_version") or LOCAL_PRICING_VERSION),
        )
