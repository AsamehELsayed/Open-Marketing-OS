"""Instagram provider router. Capability-first: callers (graph/nodes/tools)
request CAPABILITIES, never vendor implementations.

Capabilities (frozen I-7, see capabilities.py):
  instagram_public_profile  — public research path (default provider apify,
      installation-scoped credential resolved via the integration registry)
  instagram_owned_insights  — owned path (default provider meta, workspace
      OAuth credential resolved via the integration registry)

Provider priority differs for owned vs public accounts:
  Owned/connected: meta -> configured external (apify, brightdata) ->
    browser (explicit only) -> honest limitation.
  Public competitor: apify -> brightdata -> browser (explicit only) ->
    web search fallback marker -> honest limitation.

When project context exists, resolve_provider(capability, project_id) may
front a *connected* registry provider; brightdata/browser remain fallback
PROVIDERS on the chain, never capabilities. Vendor token/endpoint/actor/payload
details stay hidden inside providers.

The router never fabricates: first status in (verified, partial) wins,
otherwise the collected unknowns are returned. Model memory is never used
as current evidence — enforced by always attempting providers first.
"""
from . import capabilities as _caps
from .base import audit_result
from .apify_provider import ApifyProvider
from .brightdata_provider import BrightDataProvider
from .browser_provider import BrowserProvider
from .meta_provider import MetaProvider

OWNED_ORDER = ("meta", "apify", "brightdata", "browser")
PUBLIC_ORDER = ("apify", "brightdata", "browser")

_CAP_PUBLIC = _caps.CAP_INSTAGRAM_PUBLIC_PROFILE
_CAP_OWNED = _caps.CAP_INSTAGRAM_OWNED_INSIGHTS

_PROVIDERS = {
    "meta": MetaProvider,
    "apify": ApifyProvider,
    "brightdata": BrightDataProvider,
    "browser": BrowserProvider,
}

_PROVIDER_LABELS = {
    "apify": "Apify",
    "brightdata": "BrightData",
    "meta": "Meta",
    "browser": "Browser",
}


def _provider_label(provider: str) -> str:
    name = str(provider or "").strip()
    return _PROVIDER_LABELS.get(name.lower(), name)


def provider_status() -> dict:
    """Presence-only status for Settings UI. Never secrets."""
    _caps.ensure_i7_registered()
    out = {}
    for name, cls in _PROVIDERS.items():
        try:
            ok, why = cls().available()
        except Exception:
            ok, why = False, "check failed"
        out[name] = {"available": bool(ok), "detail": why}
    return out


def capability_status(project_id: str | None = None) -> dict:
    """Presence-only status per frozen capability. Never secrets — no token
    values, no vault refs, only provider labels and registry status enums."""
    _caps.ensure_i7_registered(project_id)
    out = {}
    for capability in _caps.FROZEN_CAPABILITIES:
        view = _caps.resolve_capability(capability, project_id)
        provider = (view.provider if view is not None and view.provider
                    else _caps.DEFAULT_PROVIDER[capability])
        cls = _PROVIDERS.get(provider)
        configured = False
        if cls is not None:
            try:
                instance = cls()
                checker = getattr(instance, "_available", None)
                ok, _ = (checker(project_id) if callable(checker)
                         else instance.available())
                configured = bool(ok)
            except Exception:
                configured = False
        out[capability] = {
            "provider": provider,
            "configured": configured,
            "registry_status": (view.status if view is not None
                                else "not_configured"),
            "credential_scope": _caps.CREDENTIAL_SCOPE[capability],
        }
    return out


def _order_for(capability: str, project_id: str | None,
               owned: bool) -> tuple[list[str], object, str]:
    """Frozen chain + registry reordering (connected known providers only).
    Unknown registry providers never run — chain stays fail-closed."""
    order = list(OWNED_ORDER if owned else PUBLIC_ORDER)
    view = _caps.resolve_capability(capability, project_id)
    note = ""
    if view is not None:
        if view.status == "connected" and view.provider in _PROVIDERS:
            if view.provider in order:
                order = [view.provider] + [p for p in order if p != view.provider]
            else:
                order.insert(0, view.provider)
        elif view.provider not in _PROVIDERS:
            note = (f"registry provider {view.provider} not available in this "
                    f"build; using frozen fallback chain")
    return order, view, note


def _annotate(res: dict, capability: str, view, note: str) -> dict:
    res["capability"] = capability
    if view is not None:
        res["integration"] = {
            "capability": capability,
            "provider": view.provider,
            "scope": view.scope,
            "status": view.status,
        }
    if note:
        unknowns = res.get("unknowns")
        if isinstance(unknowns, list):
            unknowns.append(note)
    return res


def audit(username: str, *, owned: bool = False,
          project_id: str | None = None) -> dict:
    """Run the priority chain for a capability path. Returns normalized
    InstagramAuditData dict with `capability`, optional non-secret
    `integration` annotation, and `attempts` listing each provider tried.
    Web search is a caller-side fallback (router marks it), never silently
    substituted here."""
    from .base import AUTH_ERROR, CONFIG_ERROR, PROVIDER_NOT_FOUND, normalize_handle
    username = normalize_handle(username)
    _caps.ensure_i7_registered(project_id)
    capability = _caps.capability_for(owned)
    order, view, note = _order_for(capability, project_id, owned)
    attempts, unknowns = [], []
    has_configured_provider = False
    primary_failure = None

    for name in order:
        provider = _PROVIDERS[name]()
        try:
            checker = getattr(provider, "_available", None)
            if callable(checker):
                ok, why = checker(project_id)
            else:
                ok, why = provider.available()
        except Exception:
            ok, why = False, "check failed"
        if not ok:
            attempts.append({"provider": name, "status": "skipped_unconfigured", "state": "NOT_CONFIGURED", "detail": why})
            continue

        has_configured_provider = True
        try:
            res = (provider.audit_owned(username, project_id=project_id) if owned
                   else provider.audit_public(username, project_id=project_id))
        except Exception:
            res = {"account": None, "posts": [], "insights": {},
                   "evidence": {"source": name, "status": "unavailable", "collected_at": ""},
                   "unknowns": [f"{name} could not complete the request"]}

        ev_status = res.get("evidence", {}).get("status", "unavailable")
        err_code = res.get("error_code")
        if ev_status in ("verified", "partial"):
            attempt_status = "SUCCEEDED"
        elif err_code in (CONFIG_ERROR, PROVIDER_NOT_FOUND):
            attempt_status = "FAILED_CONFIG"
        elif err_code == AUTH_ERROR:
            attempt_status = "AUTH_ERROR"
        else:
            attempt_status = "FAILED"

        attempts.append({"provider": name, "status": attempt_status, "error_code": err_code})

        if ev_status in ("verified", "partial"):
            res["attempts"] = attempts
            res["source_chain"] = [a.get("provider", "") for a in attempts]
            res["provider"] = _provider_label(name)
            return _annotate(res, capability, view, note)

        if not primary_failure and attempt_status != "NOT_CONFIGURED":
            primary_failure = {"provider": name, "status": attempt_status, "error_code": err_code}

        unknowns.extend(res.get("unknowns", []))

    if note:
        unknowns.append(note)
    resolved_provider = (
        (primary_failure or {}).get("provider")
        or next((attempt.get("provider") for attempt in attempts), "")
        or (view.provider if view is not None else "")
    )
    return _annotate({
        "account": None, "posts": [], "insights": {},
        "evidence": {"source": "none", "status": "unavailable", "collected_at": ""},
        "unknowns": unknowns or ["no Instagram provider configured"],
        "attempts": attempts,
        "source_chain": [a.get("provider", "") for a in attempts],
        "provider": _provider_label(resolved_provider),
        "has_configured_provider": has_configured_provider,
        "primary_failure": primary_failure,
    }, capability, view, "")


def audit_capability(capability: str, username: str, *,
                     project_id: str | None = None) -> dict:
    """Capability entry point for graph/nodes: request a capability string,
    never a vendor. FAIL-CLOSED on unknown capability — no provider runs."""
    if capability == _CAP_PUBLIC:
        return audit(username, owned=False, project_id=project_id)
    if capability == _CAP_OWNED:
        return audit(username, owned=True, project_id=project_id)
    res = audit_result("none", "unavailable",
                       unknowns=[f"unknown capability {capability!r} (fail-closed)"])
    res["attempts"] = []
    res["source_chain"] = []
    res["capability"] = str(capability or "")
    return res
