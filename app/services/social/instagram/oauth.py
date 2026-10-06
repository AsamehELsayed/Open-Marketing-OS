"""Workspace OAuth framework for instagram_owned_insights (provider: meta).

FRAMEWORK READY: authorize URL building, CSRF state, code exchange, vault
storage, and I-7 registration are implemented and covered by mock tests.

EXTERNAL AUTH BLOCKER: completing a REAL Meta OAuth exchange requires a Meta
app id/secret, an approved redirect URI, and live network access. None are
available in this environment (no paid services, no real secrets anywhere —
vault:// refs + mocks only). exchange_code() therefore fail-closes before any
network call when client credentials are missing, and tests always inject a
mock transport.

Secret rules: the access token goes straight into the vault; the returned
payload carries only the opaque secret_ref, never the token.
"""
from __future__ import annotations

import secrets as _secrets
from urllib.parse import urlencode

from . import capabilities as _caps
from . import registry_bridge

META_AUTHORIZE_URL = "https://www.facebook.com/v21.0/dialog/oauth"
META_TOKEN_URL = "https://graph.facebook.com/v21.0/oauth/access_token"
META_SCOPES = ("instagram_basic", "instagram_manage_insights",
               "instagram_manage_comments", "pages_show_list",
               "business_management")
OAUTH_PROVIDERS = {
    "meta": {
        "authorize_url": META_AUTHORIZE_URL,
        "token_url": META_TOKEN_URL,
        "scopes": META_SCOPES,
    },
}


def new_state() -> str:
    return _secrets.token_urlsafe(32)


def build_authorize_url(provider: str, *, client_id: str, redirect_uri: str,
                        state: str, scopes: tuple[str, ...] | None = None) -> str:
    spec = OAUTH_PROVIDERS.get(provider)
    if spec is None or not client_id or not redirect_uri or not state:
        raise ValueError(f"unsupported or incomplete OAuth request for {provider!r}")
    scope = " ".join(scopes or spec["scopes"])
    query = urlencode({"client_id": client_id, "redirect_uri": redirect_uri,
                       "state": state, "scope": scope,
                       "response_type": "code"})
    return f"{spec['authorize_url']}?{query}"


def _default_transport(url: str, params: dict) -> dict:
    import httpx
    r = httpx.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else {}


def _register_connection(project_id: str, secret_ref: str, *,
                         provider: str, config: dict | None) -> bool:
    merged = {"project_id": project_id}
    merged.update(config or {})
    return registry_bridge.register({
        "integration_id": f"instagram-owned-insights-{project_id}"[:64],
        "capability": _caps.CAP_INSTAGRAM_OWNED_INSIGHTS,
        "provider": provider,
        "scope": _caps.CREDENTIAL_SCOPE[_caps.CAP_INSTAGRAM_OWNED_INSIGHTS],
        "status": "connected",
        "config": merged,
        "secret_ref": secret_ref,
        "last_health": None,
    })


def exchange_code(provider: str, *, client_id: str, client_secret: str,
                  code: str, redirect_uri: str, project_id: str,
                  transport=None, config: dict | None = None) -> dict:
    """Exchange an authorization code, store the token in the vault
    (project/workspace scope), and register instagram_owned_insights.

    Returns only status labels + the opaque secret_ref. Never returns or logs
    the access token. Fail-closed on missing credentials, empty code, vault
    failure, or a token-less provider response.
    """
    spec = OAUTH_PROVIDERS.get(provider)
    if spec is None:
        return {"ok": False, "status": "error", "reason": "unsupported_provider",
                "framework_ready": True}
    if not client_id or not client_secret:
        return {"ok": False, "status": "error",
                "reason": "missing_client_credentials",
                "detail": ("EXTERNAL AUTH BLOCKER: Meta app credentials and an "
                           "approved redirect URI are not available in this "
                           "environment; framework is ready."),
                "framework_ready": True}
    if not code or not project_id:
        return {"ok": False, "status": "error", "reason": "missing_code_or_project"}
    send = transport or _default_transport
    try:
        data = send(spec["token_url"], {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        })
    except Exception:
        return {"ok": False, "status": "error",
                "reason": "exchange_failed",
                "detail": "OAuth exchange failed",
                "framework_ready": True}
    token = str((data or {}).get("access_token") or "")
    if not token:
        return {"ok": False, "status": "error", "reason": "no_access_token",
                "framework_ready": True}
    ref = registry_bridge.store_secret(
        token, scope=_caps.CREDENTIAL_SCOPE[_caps.CAP_INSTAGRAM_OWNED_INSIGHTS],
        name=f"{project_id}/{provider}_oauth")
    if not ref:
        return {"ok": False, "status": "error", "reason": "vault_unavailable"}
    registered = _register_connection(
        project_id, ref, provider=provider, config=config)
    if not registered:
        return {"ok": False, "status": "error", "reason": "register_failed",
                "secret_ref": ref}
    granted = data.get("scope") or data.get("granted_scopes")
    return {"ok": True, "status": "connected",
            "capability": _caps.CAP_INSTAGRAM_OWNED_INSIGHTS,
            "provider": provider, "secret_ref": ref,
            "scopes": list(granted) if isinstance(granted, (list, tuple))
                      else list(spec["scopes"])}


def connect_access_token(provider: str, token: str, *,
                         project_id: str, config: dict | None = None) -> dict:
    if provider not in OAUTH_PROVIDERS:
        return {"ok": False, "status": "error", "reason": "unsupported_provider"}
    if not token or not project_id:
        return {"ok": False, "status": "error", "reason": "missing_token_or_project"}
    ref = registry_bridge.store_secret(
        token,
        scope=_caps.CREDENTIAL_SCOPE[_caps.CAP_INSTAGRAM_OWNED_INSIGHTS],
        name=f"{project_id}/{provider}_oauth",
    )
    if not ref:
        return {"ok": False, "status": "error", "reason": "vault_unavailable"}
    if not _register_connection(
            project_id, ref, provider=provider, config=config):
        return {"ok": False, "status": "error", "reason": "register_failed"}
    return {"ok": True, "status": "connected", "provider": provider,
            "capability": _caps.CAP_INSTAGRAM_OWNED_INSIGHTS,
            "secret_ref": ref}


def disconnect_project(provider: str, project_id: str) -> bool:
    if provider not in OAUTH_PROVIDERS or not project_id:
        return False
    view = registry_bridge.resolve(
        _caps.CAP_INSTAGRAM_OWNED_INSIGHTS, project_id)
    if view is None or view.provider != provider or view.scope != "project":
        return False
    if view.secret_ref:
        try:
            from app.services.credentials import vault
            vault.revoke(view.secret_ref)
        except Exception:
            return False
    try:
        from app.services.integrations import registry
        registry.register_integration(
            registry.IntegrationRecord(
                integration_id=view.integration_id,
                capability=view.capability,
                provider=provider,
                scope="project",
                status="not_configured",
                config={"project_id": project_id},
                secret_ref=None,
                last_health=None,
            ),
            project_id=project_id,
        )
    except Exception:
        return False
    return True
