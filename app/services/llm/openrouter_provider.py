"""OpenRouter provider (DEV-007R-E2E Part B).

First-class optional cloud provider behind the OMOS provider contract.
Uses the OpenAI-compatible transport at https://openrouter.ai/api/v1 with
an Authorization Bearer key from the Credential Vault. Browser requests
never touch OpenRouter directly: React -> FastAPI -> ModelRouter (or
settings routes) -> this module.

Provider identity is preserved: name == "openrouter" (never rebranded as
openai because of the wire protocol). Telemetry keeps requested_model and
the actual returned model id, real token counts, and cost when OpenRouter
reports it (usage.include). Missing cost stays None — never fabricated.
"""
import logging
import os
import time

logger = logging.getLogger(__name__)

from .base import LLMResponse, ToolCall

BASE_URL = "https://openrouter.ai/api/v1"
TRANSIENT = (429, 500, 502, 503, 504)


def get_key() -> str:
    """Resolve the vault-stored OpenRouter key (server-side only)."""
    from app.services.config_service import ConfigService
    return ConfigService.get_openrouter_api_key()


def is_configured() -> bool:
    """Key presence only — never the secret itself."""
    try:
        from app.services.config_service import ConfigService
        return ConfigService.is_openrouter_configured()
    except Exception:
        return False


class OpenRouterError(RuntimeError):
    def __init__(self, message: str, status: int | None = None,
                 failure_kind: str = "request_failure", *, telemetry: dict | None = None):
        super().__init__(message)
        self.status = status
        self.failure_kind = failure_kind
        self.telemetry = telemetry or {}


def _status(exc: Exception) -> int | None:
    for attr in ("status_code", "status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    return None


def _safe_text(value, limit: int = 120) -> str | None:
    """Allow short identifier-like provider metadata only."""
    import re
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > limit or not re.fullmatch(r"[A-Za-z0-9._:/-]+", value):
        return None
    return value


def _safe_usage(value: dict) -> dict:
    allowed = ("input", "output", "cached", "reasoning", "total_tokens", "cost_usd")
    return {k: v for k, v in value.items() if k in allowed and
            isinstance(v, (int, float)) and not isinstance(v, bool)}


class _TransportObserver:
    """Best-effort adapter telemetry; callback failures never affect requests."""
    def __init__(self, opts: dict, model: str):
        self.callback = opts.get("_transport_observer")
        self.call_id = _safe_text(opts.get("call_id"), 128)
        self.turn_id = _safe_text(opts.get("turn_id"), 128)
        self.model = _safe_text(model, 200)
        self.started_at = time.monotonic()
        self.attempt_index = 0
        self.last_telemetry: dict = {}

    def emit(self, *, state: str, attempt_index: int, **fields):
        import time as _time
        elapsed = int((_time.monotonic() - self.started_at) * 1000) if self.started_at else 0
        record = {"state": state, "call_id": self.call_id, "turn_id": self.turn_id,
                  "provider": "openrouter", "requested_model": self.model,
                  "endpoint_host": _safe_text(fields.pop("endpoint_host", "openrouter.ai")),
                  "endpoint_path": _safe_text(fields.pop("endpoint_path", "/api/v1/chat/completions")),
                  "http_method": _safe_text(fields.pop("http_method", "POST")),
                  "attempt_index": attempt_index, "elapsed_ms": elapsed}
        allow = {"credential_reference_resolved", "credential_decrypted", "client_initialized",
                 "request_serialized", "http_status", "provider_request_id", "content_type",
                 "actual_model", "usage", "provider_error_code", "provider_error_type",
                 "exception_class", "reason", "network_phase"}
        for key, value in fields.items():
            if key not in allow:
                continue
            if key in ("provider_request_id", "actual_model", "provider_error_code", "provider_error_type"):
                value = _safe_text(value)
            elif key == "content_type":
                value = _safe_text(value, 80)
            elif key == "exception_class":
                value = _safe_text(value, 80)
            elif key == "usage" and isinstance(value, dict):
                value = _safe_usage(value)
            elif key == "http_status" and not isinstance(value, int):
                continue
            elif key in ("reason", "network_phase"):
                value = _safe_text(value, 64)
            if value is not None:
                record[key] = value
        self.last_telemetry = {k: v for k, v in record.items() if k not in ("state", "elapsed_ms")}
        if callable(self.callback):
            try:
                self.callback(record)
            except Exception:
                pass


class _ObservedHTTPTransport:
    """Counts each SDK dispatch at httpx's actual transport boundary."""
    def __init__(self, transport, observer: _TransportObserver):
        self._transport = transport
        self._observer = observer

    def handle_request(self, request):
        import time as _time
        from urllib.parse import urlsplit
        self._observer.attempt_index += 1
        index = self._observer.attempt_index
        if self._observer.started_at is None:
            self._observer.started_at = _time.monotonic()
        parsed = urlsplit(str(request.url))
        host = parsed.hostname or ""
        path = parsed.path
        # SDK has now serialized the request and is dispatching it through httpx.
        self._observer.emit(state="provider_request_started", attempt_index=index,
                             endpoint_host=host, endpoint_path=path,
                             http_method=request.method, request_serialized=True,
                             transport_started=True, network_phase="transport_dispatch")
        try:
            response = self._transport.handle_request(request)
        except Exception as exc:
            self._observer.emit(state="provider_request_failed", attempt_index=index,
                                endpoint_host=host, endpoint_path=path,
                                http_method=request.method, exception_class=type(exc).__name__,
                                reason=_network_reason(exc), network_phase=_network_phase(exc),
                                transport_started=True)
            raise
        headers = response.headers
        status = response.status_code
        request_id = headers.get("x-openrouter-request-id") or headers.get("x-request-id")
        content_type = headers.get("content-type")
        self._observer.emit(state="provider_response_headers", attempt_index=index,
                            endpoint_host=host, endpoint_path=path, http_method=request.method,
                            http_status=status, provider_request_id=request_id,
                            content_type=content_type, network_phase="http_response")
        return response

    def close(self):
        self._transport.close()


def _network_phase(exc: Exception) -> str:
    name = type(exc).__name__.lower()
    cause_names = []
    messages = []
    cause = exc
    for _ in range(6):
        if cause is None:
            break
        cause_names.append(type(cause).__name__.lower())
        try:
            messages.append(str(cause).lower())
        except Exception:
            pass
        cause = getattr(cause, "__cause__", None) or getattr(cause, "__context__", None)
    if any(token in name for token in ("json", "validation", "decode", "parse")):
        return "response_parse"
    if "timeout" in name: return "timeout"
    if "proxy" in name or any("proxy" in n for n in cause_names): return "proxy"
    if "ssl" in name or "tls" in name or any("ssl" in n or "tls" in n for n in cause_names): return "tls"
    if ("gaierror" in name or any("gaierror" in n for n in cause_names)
            or any("getaddrinfo" in message for message in messages)): return "dns"
    if "connectionrefused" in name or any("connectionrefused" in n for n in cause_names): return "connection_refused"
    if "connect" in name: return "connection"
    if name in {"typeerror", "valueerror"}: return "request_serialization"
    return "transport"


def _network_reason(exc: Exception) -> str:
    phase = _network_phase(exc)
    return {"timeout": "request_timeout", "proxy": "proxy_error", "tls": "tls_error",
            "dns": "dns_error", "connection_refused": "connection_refused",
            "connection": "connection_error", "response_parse": "response_parse_failure",
            "request_serialization": "serialization_error"}.get(phase, "transport_error")


def _provider_error_fields(exc: Exception) -> dict:
    """Extract only bounded provider error identifiers; never retain its body/text."""
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return {}
    value = body.get("error") if isinstance(body.get("error"), dict) else body
    fields = {}
    for source, target in (("code", "provider_error_code"), ("type", "provider_error_type")):
        item = value.get(source) if isinstance(value, dict) else None
        safe = _safe_text(item, 64)
        if safe:
            fields[target] = safe
    return fields


_CATALOG: list[dict] | None = None
_CATALOG_TS: float = 0.0


def _model_catalog_cache() -> list[dict]:
    """Module-level TTL cache for the OpenRouter model catalog."""
    global _CATALOG, _CATALOG_TS
    ttl = _catalog_ttl()
    if _CATALOG is not None and (time.time() - _CATALOG_TS) < ttl:
        return _CATALOG
    data = _fetch_models()
    _CATALOG = data
    _CATALOG_TS = time.time()
    return _CATALOG


def _catalog_ttl() -> float:
    try:
        return max(60.0, float(os.getenv("OPENROUTER_CATALOG_TTL_S", "3600")))
    except (TypeError, ValueError):
        return 3600.0


def clear_catalog_cache() -> None:
    global _CATALOG
    _CATALOG = None
    _CATALOG_TS = 0.0


def _fetch_models() -> list[dict]:
    """Fetch /models from OpenRouter through the authenticated client."""
    import json
    from openai import OpenAI
    key = get_key()
    client = OpenAI(api_key=key, base_url=BASE_URL, timeout=20)
    models = client.models.list()
    out: list[dict] = []
    for m in getattr(models, "data", []) or []:
        item = json.loads(m.model_dump_json()) if hasattr(m, "model_dump_json") else dict(m)
        out.append({
            "id": item.get("id", ""),
            "name": item.get("name", ""),
            "context_length": item.get("context_length"),
            "modalities": (item.get("architecture") or {}).get("input_modalities")
                          or (item.get("architecture") or {}).get("modality"),
            "supports_tools": not item.get("parameters_structured") is False
                              and bool((item.get("supported_parameters") or ["tools"]) or []),
            "pricing": item.get("pricing") or {},
        })
    return [m for m in out if m["id"]]


def model_catalog(refresh: bool = False) -> list[dict]:
    """TTL-cached model catalog. Never stored token; ids only."""
    global _CATALOG
    if refresh:
        clear_catalog_cache()
    if _CATALOG is not None and (time.time() - _CATALOG_TS) < _catalog_ttl():
        return _CATALOG
    try:
        return _model_catalog_cache()
    except Exception as exc:
        logger.warning("Failed to fetch OpenRouter model catalog: %s", exc)
        return []


def _tool_spec(t) -> dict:
    spec = t if isinstance(t, dict) else {"name": t.name, "description": t.description,
                                          "parameters": t.parameters}
    return {"type": "function", "function": {
        "name": spec["name"], "description": spec.get("description", ""),
        "parameters": spec.get("parameters", {"type": "object", "properties": {}})}}


def _client(timeout: int):
    from openai import OpenAI
    key = get_key()
    if not key:
        raise OpenRouterError("OpenRouter is not configured. Connect OpenRouter in Settings.")
    return OpenAI(api_key=key, base_url=BASE_URL, timeout=timeout, max_retries=0,
                  default_headers={"HTTP-Referer": "https://omos.local",
                                   "X-Title": "Open Marketing OS"})


class OpenRouterProvider:
    name = "openrouter"
    base_url = BASE_URL

    def __init__(self, model: str = "openrouter/auto", timeout_s: int = 45):
        self.model = model
        self.timeout_s = timeout_s

    def supports_server_websearch(self) -> bool:
        return False

    def _model(self, opts: dict | None) -> str:
        return (opts or {}).get("model", self.model) or self.model

    def complete(self, *, system: str, messages: list, tools: list,
                 opts: dict | None = None) -> LLMResponse:
        opts = dict(opts or {})
        model = self._model(opts)
        timeout = int(opts.get("timeout_s", self.timeout_s) or self.timeout_s)
        observer = _TransportObserver(opts, model)
        client = self._open_client(timeout, observer=observer)
        try:
            payload = self._payload(system, messages, tools, model, opts)
        except Exception as exc:
            observer.emit(state="provider_request_failed", attempt_index=0,
                          exception_class=type(exc).__name__, reason="serialization_error",
                          network_phase="request_serialization", request_serialized=False,
                          transport_started=False)
            raise _wrap(exc, telemetry=observer.last_telemetry) from None
        observer.emit(state="provider_request_prepared", attempt_index=0,
                      credential_reference_resolved=True, credential_decrypted=True,
                      client_initialized=True)
        last_err: Exception | None = None
        for attempt in (0, 1):
            try:
                resp = client.chat.completions.create(**payload)
                result = _to_llm_response(resp, requested_model=model)
                observer.emit(state="provider_response_completed",
                              attempt_index=observer.attempt_index,
                              actual_model=_safe_text(result.usage.get("actual_model")),
                              usage=_safe_usage(result.usage))
                return result
            except Exception as e:
                last_err = e
                status = _status(e)
                error_fields = _provider_error_fields(e)
                phase = _network_phase(e)
                reason = (_network_reason(e) if phase == "response_parse" else
                          "http_error" if status is not None else _network_reason(e))
                checkpoint_fields = ({"request_serialized": False, "transport_started": False}
                                     if phase == "request_serialization" else {})
                observer.emit(state="provider_request_failed",
                              attempt_index=observer.attempt_index,
                              http_status=status, exception_class=type(e).__name__,
                              reason=reason,
                              network_phase=(phase if phase == "response_parse" else
                                             "http_response" if status is not None else phase),
                              **checkpoint_fields, **error_fields)
                if status in TRANSIENT and attempt == 0:
                    time.sleep(1.0)
                    continue
                raise _wrap(e, telemetry=observer.last_telemetry) from None
        raise _wrap(last_err or Exception("openrouter call failed"))

    def complete_streaming(self, *, system: str, messages: list, tools: list,
                           opts: dict | None = None, on_event=None) -> LLMResponse:
        opts = dict(opts or {})
        model = self._model(opts)
        timeout = int(opts.get("timeout_s", self.timeout_s) or self.timeout_s)
        observer = _TransportObserver(opts, model)
        client = self._open_client(timeout, observer=observer)
        try:
            payload = self._payload(system, messages, tools, model, opts, stream=True)
        except Exception as exc:
            observer.emit(state="provider_request_failed", attempt_index=0,
                          exception_class=type(exc).__name__, reason="serialization_error",
                          network_phase="request_serialization", request_serialized=False,
                          transport_started=False)
            raise _wrap(exc, telemetry=observer.last_telemetry) from None
        observer.emit(state="provider_request_prepared", attempt_index=0,
                      credential_reference_resolved=True, credential_decrypted=True,
                      client_initialized=True)
        accumulated: list[str] = []
        usage_map: dict = {}
        actual_model = model
        try:
            stream = client.chat.completions.create(**payload)
            for chunk in stream:
                cm = getattr(chunk, "model", None)
                if cm:
                    actual_model = str(cm)
                u = getattr(chunk, "usage", None)
                if u is not None:
                    usage_map = _usage_of(u)
                for choice in getattr(chunk, "choices", []) or []:
                    piece = getattr(getattr(choice, "delta", None), "content", None)
                    if piece and isinstance(piece, str):
                        accumulated.append(piece)
                        if callable(on_event):
                            try:
                                on_event("delta", "".join(accumulated))
                            except Exception:
                                pass
        except Exception as e:
            status = _status(e)
            error_fields = _provider_error_fields(e)
            phase = _network_phase(e)
            reason = (_network_reason(e) if phase == "response_parse" else
                      "http_error" if status is not None else _network_reason(e))
            checkpoint_fields = ({"request_serialized": False, "transport_started": False}
                                 if phase == "request_serialization" else {})
            observer.emit(state="provider_request_failed",
                          attempt_index=observer.attempt_index,
                          http_status=status, exception_class=type(e).__name__,
                          reason=reason,
                          network_phase=(phase if phase == "response_parse" else
                                         "http_response" if status is not None else phase),
                          **checkpoint_fields, **error_fields)
            raise _wrap(e, telemetry=observer.last_telemetry) from None
        text = "".join(accumulated)
        usage_map.setdefault("requested_model", model)
        usage_map["actual_model"] = usage_map.get("actual_model") or actual_model
        observer.emit(state="provider_response_completed",
                      attempt_index=observer.attempt_index,
                      actual_model=_safe_text(usage_map.get("actual_model")),
                      usage=_safe_usage(usage_map))
        return LLMResponse(text=text if text else None, tool_calls=[], usage=usage_map)

    def _open_client(self, timeout: int, *, observer=None):
        try:
            from openai import OpenAI
        except Exception as exc:
            if observer:
                observer.emit(state="provider_request_failed", attempt_index=0,
                              exception_class=type(exc).__name__, reason="client_unavailable",
                              network_phase="client_initialization",
                              credential_reference_resolved="not_reached",
                              credential_decrypted="not_reached", client_initialized=False,
                              request_serialized="not_reached", transport_started=False)
            raise OpenRouterError("OpenRouter integration is unavailable",
                                  failure_kind="client_initialization") from None
        try:
            key = get_key()
        except Exception as exc:
            if observer:
                observer.emit(state="provider_request_failed", attempt_index=0,
                              exception_class=type(exc).__name__, reason="credential_unavailable",
                              network_phase="credential_vault",
                              credential_reference_resolved=False,
                              credential_decrypted="not_reached", client_initialized="not_reached",
                              request_serialized="not_reached", transport_started=False)
            raise OpenRouterError("OpenRouter credential is unavailable",
                                  failure_kind="credential_vault") from None
        if not key:
            if observer:
                observer.emit(state="provider_request_failed", attempt_index=0,
                              reason="credential_unavailable", network_phase="credential_vault",
                              credential_reference_resolved=False,
                              credential_decrypted="not_reached", client_initialized="not_reached",
                              request_serialized="not_reached", transport_started=False)
            raise OpenRouterError("OpenRouter is not configured. Connect OpenRouter in Settings.")
        if observer:
            observer.emit(state="provider_request_prepared", attempt_index=0,
                          credential_reference_resolved=True, credential_decrypted=True)
        try:
            import httpx
            transport = _ObservedHTTPTransport(httpx.HTTPTransport(), observer) if observer else None
            http_client = httpx.Client(transport=transport, timeout=timeout) if transport else None
            client = OpenAI(api_key=key, base_url=BASE_URL, timeout=timeout, max_retries=0,
                            http_client=http_client,
                            default_headers={"HTTP-Referer": "https://omos.local",
                                             "X-Title": "Open Marketing OS"})
            if observer:
                observer.emit(state="provider_request_prepared", attempt_index=0,
                              client_initialized=True)
            return client
        except Exception as exc:
            if observer:
                observer.emit(state="provider_request_failed", attempt_index=0,
                              exception_class=type(exc).__name__, reason="client_initialization_error",
                              network_phase="client_initialization",
                              credential_reference_resolved=True, credential_decrypted=True,
                              client_initialized=False, request_serialized="not_reached",
                              transport_started=False)
            raise OpenRouterError("OpenRouter client initialization failed",
                                  failure_kind="client_initialization") from None

    @staticmethod
    def _payload(system: str, messages: list, tools: list, model: str,
                 opts: dict, stream: bool = False) -> dict:
        msgs = [{"role": "system", "content": system}]
        for m in messages:
            msgs.append({"role": m.get("role", "user"),
                         "content": m.get("content", "")})
        payload: dict = {
            "model": model,
            "messages": msgs,
            "extra_body": {"usage": {"include": True}},
        }
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        if tools:
            payload["tools"] = [_tool_spec(t) for t in tools]
        if opts.get("_stream"):
            payload["stream"] = True
        return payload


def _usage_of(u) -> dict:
    import json as _json
    try:
        raw = u.model_dump()
    except Exception:
        raw = dict(u)
    usage: dict = {
        "input": raw.get("prompt_tokens"),
        "output": raw.get("completion_tokens"),
        "cached": ((raw.get("prompt_tokens_details") or {}).get("cached_tokens")
                   if isinstance(raw.get("prompt_tokens_details"), dict)
                   else raw.get("cached_tokens")),
        "reasoning": (raw.get("completion_tokens_details") or {}).get("reasoning_tokens")
                     or raw.get("reasoning_tokens"),
    }
    raw_total = raw.get("total_tokens")
    if isinstance(raw_total, (int, float)):
        usage["total_tokens"] = raw_total
    for cost_field in ("cost", "total_cost"):
        v = raw.get(cost_field)
        if isinstance(v, (int, float)):
            usage["cost_usd"] = float(v)
    try:
        text = _json.dumps(raw)
        if '"cost"' in text and "cost_usd" not in usage:
            import re as _re
            m = _re.search(r'"cost"\s*:\s*([0-9.]+)', text)
            if m:
                usage["cost_usd"] = float(m.group(1))
    except Exception:
        pass
    return usage


def _to_llm_response(resp, requested_model: str) -> LLMResponse:
    import json
    choice = peek = None
    choices = getattr(resp, "choices", []) or []
    if choices:
        peek = choices[0]
    message = getattr(peek, "message", None) if peek is not None else None
    text = getattr(message, "content", None)
    calls: list[ToolCall] = []
    for tc in getattr(message, "tool_calls", None) or []:
        fn = getattr(tc, "function", None)
        try:
            args = json.loads(getattr(fn, "arguments", "") or "{}")
        except Exception:
            args = {}
        calls.append(ToolCall(name=str(getattr(fn, "name", "")),
                              arguments=args, call_id=str(getattr(tc, "id", ""))))
    usage_map = (_usage_of(getattr(resp, "usage", None))
                 if getattr(resp, "usage", None) is not None else {})
    usage_map["requested_model"] = requested_model
    usage_map["actual_model"] = str(getattr(resp, "model", "") or requested_model)
    return LLMResponse(text=text if text else None,
                       tool_calls=calls if any(c.name for c in calls) else [],
                       usage=usage_map)


def _wrap(exc: Exception, *, telemetry: dict | None = None) -> Exception:
    status = _status(exc)
    exception_type = type(exc).__name__.lower()
    if "validation" in exception_type or "jsondecode" in exception_type or "parse" in exception_type:
        return OpenRouterError("OpenRouter returned an invalid response",
                               failure_kind="parse_error", telemetry=telemetry)
    if status == 401:
        return OpenRouterError("OpenRouter rejected the credential (auth error)", status=401,
                               failure_kind="authentication", telemetry=telemetry)
    if status in (402, 429):
        kind = "rate_limit" if status == 429 else "credit_limit"
        return OpenRouterError(f"OpenRouter reported limit/credit issue (status {status})",
                               status=status, failure_kind=kind, telemetry=telemetry)
    if status in (404, 400):
        return OpenRouterError(f"OpenRouter model/request configuration error (status {status})",
                               status=status, failure_kind="request_configuration", telemetry=telemetry)
    if status is not None:
        return OpenRouterError(f"OpenRouter request failed (status {status})", status=status,
                               failure_kind="http_error", telemetry=telemetry)
    if "timeout" in exception_type:
        return OpenRouterError("OpenRouter request timed out", failure_kind="timeout", telemetry=telemetry)
    if "connection" in exception_type:
        return OpenRouterError("OpenRouter could not be reached", failure_kind="connection", telemetry=telemetry)
    return OpenRouterError("OpenRouter request failed", telemetry=telemetry)


def test_connection(*, bounded_inference: bool = True) -> dict:
    """Real verification: auth (models list OK) + bounded inference when asked.
    Returns product-language tri-state; never echoes the key."""
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    try:
        models = _fetch_models()
        if not models:
            return {"credential_status": "connected", "provider_health": "error",
                    "capability_health": "configuration_error", "model_count": 0,
                    "detail": "OpenRouter reachable but returned no models",
                    "last_checked_at": now}
    except Exception as e:
        status = getattr(e, "status", None) or _status(e)
        if status == 401:
            return {"credential_status": "auth_error", "provider_health": "error",
                    "capability_health": "unknown",
                    "detail": "OpenRouter rejected the stored credential",
                    "last_checked_at": now}
        if status in (404, 400):
            return {"credential_status": "connected", "provider_health": "error",
                    "capability_health": "configuration_error",
                    "detail": "OpenRouter returned model API errors",
                    "last_checked_at": now}
        if status in TRANSIENT or status is None:
            return {"credential_status": "connected", "provider_health": "error",
                    "capability_health": "unknown",
                    "detail": "OpenRouter is unreachable right now",
                    "last_checked_at": now}
        # any other status (402/403/…) must still return a structured result
        return {"credential_status": "connected", "provider_health": "error",
                "capability_health": "unknown",
                "detail": f"OpenRouter reported status {status}",
                "last_checked_at": now}
    health = {"credential_status": "connected", "provider_health": "healthy",
              "capability_health": "not_verified", "model_count": len(models),
              "detail": "Authenticated; model catalog available",
              "last_checked_at": now}
    if health.get("provider_health") != "healthy":
        return health
    if not bounded_inference:
        health["capability_health"] = "not_verified"
        return health
    try:
        prov = OpenRouterProvider(model=_default_test_model(models), timeout_s=20)
        resp = prov.complete(system="Reply with the single word: ok.",
                             messages=[{"role": "user", "content": "ok?"}],
                             tools=[], opts={})
        if resp.text:
            health["capability_health"] = "verified"
            health["actual_model"] = resp.usage.get("actual_model", "")
            health["detail"] = "Authenticated; bounded inference verified"
        else:
            health["capability_health"] = "configuration_error"
            health["detail"] = "OpenRouter answered without usable text"
        return health
    except Exception:
        health["capability_health"] = "configuration_error"
        health["detail"] = "Model access check failed (bounded inference error)"
        return health


def _default_test_model(models: list[dict]) -> str:
    curated = os.getenv("OPENROUTER_TEST_MODEL", "")
    if curated:
        return curated
    for m in models:
        mid = m.get("id", "")
        if "free" in mid:
            return mid
    return models[0]["id"] if models else "openrouter/auto"
