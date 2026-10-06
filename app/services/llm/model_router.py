"""DEV-005 W5 — local-first model router + call telemetry.

Additive v1 surface. Does NOT modify ``app/services/llm/router.py`` (legacy
MANAGER_PROVIDER routing stays untouched). W3 local-runtime integration lands
in I1; until then the local leg runs through an injected provider object
(W3 wires the real one, tests inject FakeProvider).

Modes: AUTO (local default, escalation only for recorded observable reasons),
LOCAL (always local), OPENAI (explicit, requires OPENAI_API_KEY presence).

Every executed call persists one ``model_calls`` row via
``app.database.repos.ModelCalls``: identity (call/turn/project ids, provider,
model, adapter, quantization), route mode + reason, token counts exactly as
reported (unknown stays NULL), latency, estimated API cost, pricing version.
Local API cost is always 0.00 with a compute-not-metered marker.
"""
from __future__ import annotations

import time
import uuid
from functools import lru_cache
from dataclasses import dataclass, field
from pathlib import Path

from app.contracts.model_call import ModelCall
from app.contracts.routing import ESCALATION_REASONS, ModelRoute

LOCAL_COMPUTE_NOTE = "local API cost $0.00; local compute cost is not metered"
OPENAI_UNAVAILABLE_NOTE = "openai unavailable"
UNKNOWN_COST_NOTE = "usage unknown; cost not estimated"
PRICING_UNAVAILABLE_NOTE = "pricing unavailable; cost not estimated"

DEFAULT_PRICING_PATH = Path(__file__).resolve().parents[3] / "config" / "model_pricing.yaml"

LOCAL_DEFAULT_REASON = "benchmark winner default"


class ModelCallFailure(RuntimeError):
    """Provider call failure with truthful boundary identity.

    ``invocation_started`` is false when routing or provider resolution failed
    before the provider method was entered. It is true only around that method.
    """

    def __init__(self, message: str, *, provider: str, model: str,
                 route_mode: str, route_reason: str,
                 invocation_started: bool, call_id: str = "",
                 failure_code: str = "", http_status: int | None = None,
                 failure_reason: str = "") -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.route_mode = route_mode
        self.route_reason = route_reason
        self.invocation_started = invocation_started
        self.call_id = call_id
        self.failure_code = failure_code
        self.http_status = http_status
        self.failure_reason = failure_reason


def _provider_failure_details(exc: Exception) -> tuple[str, int | None, str]:
    """Project provider errors to a small safe vocabulary for turn telemetry."""
    allowed = {
        "authentication": "The provider rejected the credential.",
        "credit_limit": "The provider reported a credit or account limit.",
        "rate_limit": "The provider rate limit was reached.",
        "request_configuration": "The provider rejected the model or request configuration.",
        "http_error": "The provider returned an HTTP error.",
        "timeout": "The provider request timed out.",
        "parse_error": "The provider returned an invalid response.",
        "connection": "The provider could not be reached.",
        "request_failure": "The provider request failed.",
    }
    code = getattr(exc, "failure_kind", "")
    code = code if isinstance(code, str) and code in allowed else "request_failure"
    status = getattr(exc, "status", None)
    if not isinstance(status, int) or not 100 <= status <= 599:
        status = None
    return code, status, allowed[code]

# Escalation reasons that may move an AUTO call from local to OpenAI.
# "benchmark winner default" is the stay-local default, not an escalation.
OPENAI_ESCALATIONS = frozenset(
    r for r in ESCALATION_REASONS if r != LOCAL_DEFAULT_REASON
)


# ---------- pricing ----------

class PricingUnavailable(RuntimeError):
    """No valid pricing version for the requested date/version.

    Raised instead of silently using a stale version or borrowing a future
    one. Callers (ModelRouter.complete) catch this and persist an explicit
    pricing-unavailable note with cost None — never a fabricated cost.
    """


@dataclass
class PricingVersion:
    version: str
    effective_date: str  # ISO date; lexical compare is valid for YYYY-MM-DD
    # Per-model USD-per-million-token rates: (input, cached_input, output).
    rates: dict[str, tuple[float, float, float]] = field(default_factory=dict)

    def rate_for(self, model: str) -> tuple[float, float, float] | None:
        return self.rates.get(model)


@dataclass
class PricingDoc:
    versions: list[PricingVersion]
    local_compute_note: str = "local compute cost is not metered"
    path: str = ""

    def effective_version(self, as_of: str | None = None) -> PricingVersion:
        """Latest version when as_of is None; else newest version with
        effective_date <= as_of. Raises PricingUnavailable when no version
        is effective yet - never falls back to a future version silently.

        An empty document (the degraded result of a missing or unreadable
        pricing file) raises PricingUnavailable rather than IndexError, so
        "cost unknown" stays a first-class outcome instead of a crash.
        """
        ordered = sorted(self.versions, key=lambda v: (v.effective_date, v.version))
        if not ordered:
            raise PricingUnavailable(
                "no pricing versions are available"
                + (f" (as of {as_of})" if as_of else "")
            )
        if as_of is None:
            return ordered[-1]
        applicable = [v for v in ordered if v.effective_date <= as_of]
        if not applicable:
            raise PricingUnavailable(
                f"no pricing version effective on or before {as_of}"
            )
        return applicable[-1]


def _parse_pricing_text(text: str, path: str = "") -> PricingDoc:
    """Parse the constrained pricing YAML shape without requiring PyYAML.

    Uses PyYAML when installed (same parser as the test suite); otherwise a
    small stdlib reader handles exactly the shape this worker authors
    (versions/version/effective_date/models/{model}/
    input_per_million/cached_input_per_million/output_per_million
    + local_compute_note).
    """
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        return _pricing_from_mapping(data or {}, path)
    except ImportError:
        return _pricing_from_mapping(_minimal_mapping(text), path)


def _pricing_from_mapping(data: dict, path: str = "") -> PricingDoc:
    versions: list[PricingVersion] = []
    for entry in data.get("versions", []) or []:
        rates: dict[str, tuple[float, float, float]] = {}
        for model, rate in ((entry.get("models", {})) or {}).items():
            try:
                rates[str(model)] = (
                    float(rate["input_per_million"]),
                    float(rate["cached_input_per_million"]),
                    float(rate["output_per_million"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
        versions.append(PricingVersion(
            version=str(entry.get("version", "")),
            effective_date=str(entry.get("effective_date", "")),
            rates=rates,
        ))
    versions = [v for v in versions if v.version and v.effective_date]
    if not versions:
        raise ValueError(f"no versioned pricing entries in {path or 'pricing doc'}")
    note = str(data.get("local_compute_note", "local compute cost is not metered"))
    return PricingDoc(versions=versions, local_compute_note=note, path=path)


def _minimal_mapping(text: str) -> dict:
    """Indent-aware reader for the exact shape authored in model_pricing.yaml."""
    versions: list[dict] = []
    note = "local compute cost is not metered"
    current_version: dict | None = None
    current_model: str | None = None
    in_versions = False
    in_models = False
    models_indent = 0
    for raw in text.splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if indent == 0 and stripped.startswith("local_compute_note:"):
            note = stripped.split(":", 1)[1].strip().strip("'\"")
        elif indent == 0 and stripped.startswith("versions:"):
            in_versions = True
        elif in_versions and stripped.startswith("- version:"):
            current_version = {"version": stripped.split(":", 1)[1].strip().strip("'\""),
                               "effective_date": "", "models": {}}
            versions.append(current_version)
            in_models = False
            current_model = None
        elif current_version is not None and stripped.startswith("effective_date:"):
            current_version["effective_date"] = stripped.split(":", 1)[1].strip().strip("'\"")
        elif current_version is not None and stripped == "models:":
            in_models = True
            models_indent = indent
            current_model = None
        elif in_models and indent > models_indent and stripped.endswith(":") \
                and "per_million" not in stripped:
            current_model = stripped[:-1].strip().strip("'\"")
            current_version["models"][current_model] = {}
        elif in_models and current_model and "per_million:" in stripped:
            key, _, val = stripped.partition(":")
            try:
                current_version["models"][current_model][key.strip()] = float(val.strip())
            except ValueError:
                continue
    return {"versions": versions, "local_compute_note": note}


def load_pricing(path: str | Path | None = None) -> PricingDoc:
    """Load the pricing table, degrading to "unknown cost" rather than crashing.

    DEV-008. This used to be an unguarded ``read_text()``. ``ModelRouter`` is
    constructed on every graph turn, so a missing or unreadable pricing file
    made the frozen application raise ``FileNotFoundError`` on the *first chat
    message* — and the failure was invisible, because ``_get_graph()`` converts
    any exception into "Graph runtime is unavailable". The app looked healthy and
    simply never answered.

    Cost accounting is an observability feature, not a correctness one. If the
    table is missing or malformed the honest outcome is an empty document, which
    makes every cost estimate ``None`` — exactly the "unknown stays unknown"
    convention the rest of this module already follows — rather than taking the
    user's turn down.

    Callers that genuinely require the file should read it themselves and
    handle the error; this function is the forgiving path used on the serving
    hot path.
    """
    pricing_path = Path(path) if path else DEFAULT_PRICING_PATH
    try:
        text = pricing_path.read_text(encoding="utf-8")
    except OSError:
        _warn_missing_pricing(pricing_path, "could not be read")
        return PricingDoc(versions=(), local_compute_note="")
    try:
        return _parse_pricing_text(text, str(pricing_path))
    except Exception:
        _warn_missing_pricing(pricing_path, "could not be parsed")
        return PricingDoc(versions=(), local_compute_note="")


_WARNED_PRICING: set[str] = set()


def _warn_missing_pricing(path: Path, reason: str) -> None:
    """Log once per path. Never raise, and never include a secret."""
    key = str(path)
    if key in _WARNED_PRICING:
        return
    _WARNED_PRICING.add(key)
    try:
        import logging

        logging.getLogger(__name__).warning(
            "model_pricing.yaml %s at %s; cost estimates will report unknown. "
            "This does not affect chat, tools or retrieval.", reason, key,
        )
    except Exception:
        pass


def estimate_cost_usd(doc: PricingDoc, *, provider: str, model: str,
                      input_tokens: int | None, output_tokens: int | None,
                      cached_tokens: int | None = None,
                      pricing_version: str | None = None,
                      as_of: str | None = None) -> float | None:
    """Token arithmetic on reported counts only. Unknown stays None.

    Local legs always cost $0.00 API (compute not metered). OpenAI legs need
    known input+output counts and a priced model, else None — never estimated.
    Cached input prices at cached_input_per_million; uncached input
    (input - cached when cached is known, else full input) prices at
    input_per_million — cached tokens are never double-counted. Raises
    PricingUnavailable when the requested version/date has no valid pricing
    instead of silently using a stale or future version.
    """
    if provider == "local":
        return 0.0
    if input_tokens is None or output_tokens is None:
        return None
    if pricing_version is not None:
        version = next((v for v in doc.versions if v.version == pricing_version),
                       None)
        if version is None:
            raise PricingUnavailable(
                f"pricing version {pricing_version!r} not found; cost not estimated"
            )
    else:
        version = doc.effective_version(as_of=as_of)
    rate = version.rate_for(model)
    if rate is None:
        return None
    input_rate, cached_rate, output_rate = rate
    if cached_tokens is None:
        uncached = input_tokens
        cached = 0
    else:
        if cached_tokens < 0 or cached_tokens > input_tokens:
            return None
        uncached = input_tokens - cached_tokens
        cached = cached_tokens
    return (uncached / 1_000_000 * input_rate
            + cached / 1_000_000 * cached_rate
            + output_tokens / 1_000_000 * output_rate)


# ---------- routing policy ----------

def openai_configured() -> bool:
    try:
        from app.services.config_service import ConfigService
        return ConfigService.is_openai_configured()
    except Exception:
        return False


def _openai_env_configured() -> bool:
    return openai_configured()


def _normalize_mode(mode: str) -> str:
    normalized = (mode or "").strip().upper()
    if normalized not in ("AUTO", "LOCAL", "OPENAI", "OPENROUTER", "BASE", "MARKETING_LORA"):
        raise ValueError(f"unknown route mode: {mode!r}")
    return normalized


def openrouter_configured_default() -> bool:
    try:
        from app.services.config_service import ConfigService
        return ConfigService.is_openrouter_configured()
    except Exception:
        return False


@lru_cache(maxsize=1)
def get_managed_local_runtime_manager():
    """Return the process-shared manager used by API and provider wiring."""
    from app.services.llm.local_runtime_manager import get_local_runtime_manager
    return get_local_runtime_manager()


def _local_model_enabled() -> bool:
    try:
        from app.services.config_service import ConfigService
        value = str(ConfigService.get_setting("local_model_enabled", "on") or "on")
        return value.strip().lower() not in {"0", "off", "false", "no", "disabled"}
    except Exception:
        return True


def decide_route(mode: str, *, local_available: bool = True,
                 openai_configured: bool | None = None,
                 openrouter_configured: bool | None = None,
                 cloud_provider: str = "openai",
                 escalation_reason: str | None = None,
                 allow_cloud_escalation: bool = True,
                 local_model: str = "local-default",
                 openai_model: str = "gpt-4o-mini",
                 openrouter_model: str = "openrouter/auto") -> ModelRoute:
    """Pure local-first route decision. No I/O, no randomness.

    AUTO stays local unless an approved observable escalation reason moves it
    to OpenAI. Without an OpenAI key, AUTO never leaves the local path (the
    reason records that OpenAI was unavailable) — except explicit OPENAI mode,
    which fails loudly instead of silently substituting.
    """
    route_mode = _normalize_mode(mode)
    has_key = openai_configured if openai_configured is not None else _openai_env_configured()
    if route_mode == "LOCAL":
        return ModelRoute(mode="LOCAL", provider="local", model=local_model,
                          reason=LOCAL_DEFAULT_REASON)
    if route_mode == "BASE":
        return ModelRoute(mode="BASE", provider="local", model=local_model,
                          reason="explicit BASE mode")
    if route_mode == "MARKETING_LORA":
        return ModelRoute(mode="MARKETING_LORA", provider="local", model=local_model,
                          reason="explicit MARKETING_LORA mode")
    if route_mode == "OPENAI":
        if not has_key:
            raise RuntimeError("OpenAI is not configured. Connect OpenAI in Settings.")
        return ModelRoute(mode="OPENAI", provider="openai", model=openai_model,
                          reason="explicit user selection")
    if route_mode == "OPENROUTER":
        or_ok = openrouter_configured if openrouter_configured is not None \
            else openrouter_configured_default()
        if not or_ok:
            raise RuntimeError("OpenRouter user selection requested but openrouter_configured=False in decide_route call")
        return ModelRoute(mode="OPENROUTER", provider="openrouter",
                          model=openrouter_model,
                          reason="explicit user selection")
    if escalation_reason in OPENAI_ESCALATIONS and cloud_provider == "openrouter":
        or_ok = openrouter_configured if openrouter_configured is not None \
            else openrouter_configured_default()
        if not or_ok:
            return ModelRoute(mode="AUTO", provider="local", model=local_model,
                              reason=f"{escalation_reason}; {OPENAI_UNAVAILABLE_NOTE}")
        return ModelRoute(mode="AUTO", provider="openrouter",
                          model=openrouter_model, reason=escalation_reason)
    if escalation_reason is not None and escalation_reason not in ESCALATION_REASONS:
        raise ValueError(f"unapproved escalation reason: {escalation_reason!r}")
    if (
        not local_available
        and has_key
        and escalation_reason is None
        and allow_cloud_escalation
    ):
        escalation_reason = "local unavailable"
    if escalation_reason is None or escalation_reason == LOCAL_DEFAULT_REASON:
        if not local_available and not has_key:
            raise RuntimeError("Local AI is unavailable and OpenAI is not configured")
        return ModelRoute(mode="AUTO", provider="local", model=local_model,
                          reason=LOCAL_DEFAULT_REASON)
    if escalation_reason in OPENAI_ESCALATIONS:
        if not has_key:
            if escalation_reason == "local unavailable":
                raise RuntimeError("Local AI is unavailable and OpenAI is not configured")
            return ModelRoute(mode="AUTO", provider="local", model=local_model,
                              reason=f"{escalation_reason}; {OPENAI_UNAVAILABLE_NOTE}")

        return ModelRoute(mode="AUTO", provider="openai", model=openai_model,
                          reason=escalation_reason)
    raise ValueError(f"unhandled escalation reason: {escalation_reason!r}")


# ---------- usage extraction ----------

_USAGE_ALIASES = {
    "input_tokens": ("input_tokens", "input", "prompt_tokens"),
    "cached_tokens": ("cached_tokens", "cached", "cached_input_tokens"),
    "output_tokens": ("output_tokens", "output", "completion_tokens"),
    "reasoning_tokens": ("reasoning_tokens", "reasoning", "reasoning_output_tokens"),
    "total_tokens": ("total_tokens", "total"),
}


def extract_usage(usage: dict | None) -> dict[str, int | None]:
    """Map provider usage shapes onto canonical legs. Unknown stays None."""
    source = dict(usage or {})
    out: dict[str, int | None] = {}
    for leg, aliases in _USAGE_ALIASES.items():
        value: int | None = None
        for alias in aliases:
            if alias in source and source[alias] is not None:
                try:
                    candidate = int(source[alias])
                except (TypeError, ValueError):
                    continue
                if candidate >= 0:
                    value = candidate
                break
        out[leg] = value
    return out


# ---------- executing router ----------

class ModelRouter:
    """Executes one model call through injected providers and persists telemetry.

    Provider injection keeps W5 decoupled: W3/I1 wire the real local runtime;
    callers/tests inject fakes. No network, spend, or secrets leave this path
    (key presence is checked, never read into logs or rows).
    """

    def __init__(self, *, local_provider=None, openai_provider=None,
                 openrouter_provider=None,
                 pricing: PricingDoc | None = None,
                 pricing_path: str | Path | None = None,
                 default_local_model: str = "local-default",
                 default_openai_model: str = "gpt-4o-mini",
                 default_openrouter_model: str = "openrouter/auto") -> None:
        self.local_provider = local_provider
        self.openai_provider = openai_provider
        self.openrouter_provider = openrouter_provider
        self.pricing = pricing or load_pricing(pricing_path)
        self.default_local_model = default_local_model
        self.default_openai_model = default_openai_model
        self.default_openrouter_model = default_openrouter_model

    def _provider_for(self, provider_name: str):
        if provider_name == "local":
            provider = self.local_provider
        elif provider_name == "openrouter":
            provider = self.openrouter_provider
        else:
            provider = self.openai_provider
        if provider is None:
            if provider_name == "local":
                raise RuntimeError("local provider not wired yet (W3/I1 own it)")
            if provider_name == "openrouter":
                raise RuntimeError("openrouter provider not wired for this call")
            raise RuntimeError("openai provider not wired for this call")
        return provider

    @staticmethod
    def _provider_model(provider, fallback: str) -> str:
        model = getattr(provider, "model", "") or fallback
        return str(model) if str(model).strip() else fallback

    def _route_decision(self, route_mode: str, escalation_reason: str | None):
        has_key = openai_configured()
        or_cfg = openrouter_configured_default()
        manager_provider = "auto"
        cloud_escalation = False
        openrouter_model = self.default_openrouter_model
        saved_openrouter_model = ""
        try:
            from app.services.config_service import ConfigService
            manager_provider = str(
                ConfigService.get_setting("manager_provider", "auto") or "auto"
            ).strip().lower()
            cloud_escalation = str(
                ConfigService.get_setting("cloud_escalation", "off") or "off"
            ).strip().lower() in {"1", "on", "true", "yes"}
            saved_openrouter_model = str(ConfigService.get_setting(
                "openrouter_default_model", "") or "").strip()
            if saved_openrouter_model:
                openrouter_model = saved_openrouter_model
        except Exception:
            pass
        if manager_provider == "openrouter":
            cloud_provider = "openrouter"
        elif manager_provider == "openai":
            cloud_provider = "openai"
        elif or_cfg and (not has_key or saved_openrouter_model):
            cloud_provider = "openrouter"
        else:
            cloud_provider = "openai"
        local_available = self.local_provider is not None and _local_model_enabled()
        if (
            route_mode == "AUTO"
            and cloud_escalation
            and not local_available
            and escalation_reason is None
        ):
            escalation_reason = "local unavailable"
        return decide_route(
            route_mode,
            local_available=local_available if route_mode == "AUTO" else True,
            openai_configured=has_key,
            openrouter_configured=or_cfg,
            cloud_provider=cloud_provider,
            escalation_reason=escalation_reason,
            allow_cloud_escalation=cloud_escalation,
            local_model=self.default_local_model,
            openai_model=self.default_openai_model,
            openrouter_model=openrouter_model,
        )

    @staticmethod
    def _provider_reported(response) -> dict:
        """Provider-reported authoritative usage extras (OpenRouter et al)."""
        usage = getattr(response, "usage", None) or {}
        cost = usage.get("cost_usd")
        actual = usage.get("actual_model")
        requested = usage.get("requested_model")
        if cost is None and isinstance(usage.get("cost"), (int, float)):
            cost = float(usage["cost"])
        out: dict = {}
        if isinstance(cost, (int, float)) and cost >= 0:
            out["cost_usd"] = float(cost)
        if isinstance(requested, str) and requested.strip():
            out["requested_model"] = requested.strip()
        if isinstance(actual, str) and actual.strip():
            out["actual_model"] = actual.strip()
        return out

    def complete(self, conn, *, turn_id: str, project_id: str, system: str,
                 messages: list, tools: list, mode: str = "AUTO",
                 opts: dict | None = None, escalation_reason: str | None = None,
                 call_id: str | None = None, adapter: str | None = None,
                 quantization: str = "", as_of: str | None = None,
                 behavior_profile: str = "", on_transport_event=None):
        from app.database import repos

        if not (turn_id or "").strip():
            raise ValueError("turn_id is required")
        if not (project_id or "").strip():
            raise ValueError("project_id is required (fail closed)")
        cid = call_id or f"mc-{uuid.uuid4().hex[:12]}"
        route_mode = _normalize_mode(mode)
        local_available = self.local_provider is not None
        route = self._route_decision(route_mode, escalation_reason)
        try:
            provider = self._provider_for(route.provider)
        except Exception as exc:
            raise ModelCallFailure(
                "Selected provider is unavailable.", provider=route.provider,
                model=route.model or "", route_mode=route_mode,
                route_reason=route.reason, invocation_started=False,
                call_id=cid,
            ) from exc
        fallback_model = (self.default_local_model if route.provider == "local"
                          else self.default_openrouter_model
                          if route.provider == "openrouter"
                          else self.default_openai_model)
        model_name = self._provider_model(provider, route.model or fallback_model)
        if route.provider == "openrouter" and str(route.model or "").strip():
            model_name = str(route.model).strip()
        if route_mode == "BASE":
            target_adapter = ""
        elif route_mode == "MARKETING_LORA":
            target_adapter = adapter if (adapter is not None and adapter != "") else "OMOS-Qwen2.5-7B-Marketing-v1"
        else:
            target_adapter = adapter if adapter is not None else getattr(getattr(provider, "config", None), "adapter", "OMOS-Qwen2.5-7B-Marketing-v1")

        call_opts = dict(opts or {})
        if route.provider == "local":
            if target_adapter is not None:
                call_opts["adapter"] = target_adapter
        else:
            call_opts.pop("adapter", None)
            if route.provider == "openrouter":
                call_opts["model"] = model_name
                if callable(on_transport_event):
                    call_opts.update({"_transport_observer": on_transport_event,
                                      "call_id": cid, "turn_id": turn_id})

        started_wall = time.time()
        started_at = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(started_wall))
        try:
            response = provider.complete(system=system, messages=messages,
                                         tools=tools, opts=call_opts)
        except Exception as exc:
            failure_code, http_status, failure_reason = _provider_failure_details(exc)
            raise ModelCallFailure(
                "Provider request failed.", provider=route.provider,
                model=model_name, route_mode=route_mode,
                route_reason=route.reason, invocation_started=True,
                call_id=cid,
                failure_code=failure_code, http_status=http_status,
                failure_reason=failure_reason,
            ) from exc
        ended_wall = time.time()
        ended_at = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ended_wall))
        latency_ms = max(0, int((ended_wall - started_wall) * 1000))

        usage = extract_usage(getattr(response, "usage", None))
        try:
            pricing_version = self.pricing.effective_version(as_of=as_of).version
            cost = estimate_cost_usd(
                self.pricing, provider=route.provider, model=model_name,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cached_tokens=usage["cached_tokens"],
                as_of=as_of,
            )
        except PricingUnavailable:
            pricing_version = ""
            cost = None
        # OpenRouter (et al.) provider-reported usage is authoritative.
        reported = self._provider_reported(response)
        if "cost_usd" in reported:
            cost = reported["cost_usd"]
            pricing_version = "provider-reported"
        if "actual_model" in reported and route.provider not in ("local",):
            model_name = reported["actual_model"]
        if route.provider == "local":
            cost_note = LOCAL_COMPUTE_NOTE
        elif pricing_version == "":
            cost_note = PRICING_UNAVAILABLE_NOTE
        elif cost is None:
            cost_note = UNKNOWN_COST_NOTE
        else:
            cost_note = ""
        resp_usage = getattr(response, "usage", None) or {}
        server_reported = resp_usage.get("adapter")
        actual_adapter = str(server_reported) if server_reported is not None else (str(target_adapter or "") if route.provider == "local" else "")
        actual_quant = str(resp_usage.get("quantization")) if resp_usage.get("quantization") is not None else quantization
        if usage["total_tokens"] is None and usage["input_tokens"] is not None \
                and usage["output_tokens"] is not None:
            usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
        requested_model = str(route.model or "") if route.provider != "local" else ""
        call = ModelCall(
            call_id=cid,
            turn_id=turn_id,
            project_id=project_id,
            provider=route.provider,  # type: ignore[arg-type]
            model=model_name,
            adapter=actual_adapter,
            quantization=actual_quant,
            route_mode=route_mode,  # type: ignore[arg-type]
            route_reason=route.reason,
            input_tokens=usage["input_tokens"],
            cached_tokens=usage["cached_tokens"],
            output_tokens=usage["output_tokens"],
            reasoning_tokens=usage["reasoning_tokens"],
            total_tokens=usage["total_tokens"],
            latency_ms=latency_ms,
            estimated_cost_usd=cost,
            pricing_version=pricing_version,
            started_at=started_at,
            ended_at=ended_at,
            requested_model=requested_model,
            behavior_profile=behavior_profile if route.provider != "local" else "",
        )
        # cost_note lives in the telemetry row (W0 ModelCall contract frozen).
        record = call.model_dump()
        record["cost_note"] = cost_note
        repos.ModelCalls.insert(conn, record)
        return response, call
    def complete_streaming(self, conn, *, turn_id: str, project_id: str, system: str,
                           messages: list, tools: list, mode: str = "AUTO",
                           opts: dict | None = None, escalation_reason: str | None = None,
                           call_id: str | None = None, adapter: str | None = None,
                           quantization: str = "", as_of: str | None = None,
                           behavior_profile: str = "", on_token=None,
                           on_transport_event=None):
        from app.database import repos

        if not (turn_id or "").strip():
            raise ValueError("turn_id is required")
        if not (project_id or "").strip():
            raise ValueError("project_id is required (fail closed)")
        cid = call_id or f"mc-{uuid.uuid4().hex[:12]}"
        route_mode = _normalize_mode(mode)
        local_available = self.local_provider is not None
        route = self._route_decision(route_mode, escalation_reason)
        try:
            provider = self._provider_for(route.provider)
        except Exception as exc:
            raise ModelCallFailure(
                "Selected provider is unavailable.", provider=route.provider,
                model=route.model or "", route_mode=route_mode,
                route_reason=route.reason, invocation_started=False,
                call_id=cid,
            ) from exc
        fallback_model = (self.default_local_model if route.provider == "local"
                          else self.default_openrouter_model
                          if route.provider == "openrouter"
                          else self.default_openai_model)
        model_name = self._provider_model(provider, route.model or fallback_model)
        if route.provider == "openrouter" and str(route.model or "").strip():
            model_name = str(route.model).strip()
        if route_mode == "BASE":
            target_adapter = ""
        elif route_mode == "MARKETING_LORA":
            target_adapter = adapter if (adapter is not None and adapter != "") else "OMOS-Qwen2.5-7B-Marketing-v1"
        else:
            target_adapter = adapter if adapter is not None else getattr(getattr(provider, "config", None), "adapter", "OMOS-Qwen2.5-7B-Marketing-v1")

        call_opts = dict(opts or {})
        if route.provider == "local":
            if target_adapter is not None:
                call_opts["adapter"] = target_adapter
        else:
            call_opts.pop("adapter", None)
            if route.provider == "openrouter":
                call_opts["model"] = model_name
                if callable(on_transport_event):
                    call_opts.update({"_transport_observer": on_transport_event,
                                      "call_id": cid, "turn_id": turn_id})

        acc_tracker = [""]
        seq = 0

        def _on_event(kind, text):
            nonlocal seq
            if not text or not callable(on_token):
                return
            prev = acc_tracker[0]
            if text.startswith(prev) and len(text) >= len(prev):
                delta = text[len(prev):]
                acc_tracker[0] = text
            else:
                delta = text
                acc_tracker[0] += text
            if delta:
                seq += 1
                try:
                    on_token(delta=delta, sequence=seq, call_id=cid)
                except TypeError:
                    on_token(delta)

        started_wall = time.time()
        started_at = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(started_wall))
        try:
            if hasattr(provider, "complete_streaming") and callable(provider.complete_streaming):
                response = provider.complete_streaming(
                    system=system, messages=messages, tools=tools, opts=call_opts,
                    on_event=_on_event,
                )
            else:
                response = provider.complete(system=system, messages=messages,
                                             tools=tools, opts=call_opts)
                if callable(on_token) and getattr(response, "text", None):
                    try:
                        on_token(response.text)
                    except Exception:
                        pass
        except Exception as exc:
            failure_code, http_status, failure_reason = _provider_failure_details(exc)
            raise ModelCallFailure(
                "Provider request failed.", provider=route.provider,
                model=model_name, route_mode=route_mode,
                route_reason=route.reason, invocation_started=True,
                call_id=cid,
                failure_code=failure_code, http_status=http_status,
                failure_reason=failure_reason,
            ) from exc
        ended_wall = time.time()
        ended_at = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ended_wall))
        latency_ms = max(0, int((ended_wall - started_wall) * 1000))

        usage = extract_usage(getattr(response, "usage", None))
        try:
            pricing_version = self.pricing.effective_version(as_of=as_of).version
            cost = estimate_cost_usd(
                self.pricing, provider=route.provider, model=model_name,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cached_tokens=usage["cached_tokens"],
                as_of=as_of,
            )
        except PricingUnavailable:
            pricing_version = ""
            cost = None
        # OpenRouter (et al.) provider-reported usage is authoritative.
        reported = self._provider_reported(response)
        if "cost_usd" in reported:
            cost = reported["cost_usd"]
            pricing_version = "provider-reported"
        if "actual_model" in reported and route.provider not in ("local",):
            model_name = reported["actual_model"]
        if route.provider == "local":
            cost_note = LOCAL_COMPUTE_NOTE
        elif pricing_version == "":
            cost_note = PRICING_UNAVAILABLE_NOTE
        elif cost is None:
            cost_note = UNKNOWN_COST_NOTE
        else:
            cost_note = ""
        resp_usage = getattr(response, "usage", None) or {}
        server_reported = resp_usage.get("adapter")
        actual_adapter = str(server_reported) if server_reported is not None else (str(target_adapter or "") if route.provider == "local" else "")
        actual_quant = str(resp_usage.get("quantization")) if resp_usage.get("quantization") is not None else quantization
        if usage["total_tokens"] is None and usage["input_tokens"] is not None \
                and usage["output_tokens"] is not None:
            usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
        requested_model = str(route.model or "") if route.provider != "local" else ""
        call = ModelCall(
            call_id=cid,
            turn_id=turn_id,
            project_id=project_id,
            provider=route.provider,  # type: ignore[arg-type]
            model=model_name,
            adapter=actual_adapter,
            quantization=actual_quant,
            route_mode=route_mode,  # type: ignore[arg-type]
            route_reason=route.reason,
            input_tokens=usage["input_tokens"],
            cached_tokens=usage["cached_tokens"],
            output_tokens=usage["output_tokens"],
            reasoning_tokens=usage["reasoning_tokens"],
            total_tokens=usage["total_tokens"],
            latency_ms=latency_ms,
            estimated_cost_usd=cost,
            pricing_version=pricing_version,
            started_at=started_at,
            ended_at=ended_at,
            requested_model=requested_model,
            behavior_profile=behavior_profile if route.provider != "local" else "",
        )
        record = call.model_dump()
        record["cost_note"] = cost_note
        repos.ModelCalls.insert(conn, record)
        return response, call
