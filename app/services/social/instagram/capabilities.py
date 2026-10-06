"""Frozen I-7 Instagram capabilities — capability != provider.

Graph/nodes and callers request these capability strings only. Vendor token,
HTTP endpoint, actor ids, and payload details stay hidden inside providers
and are reached through the integration registry (consume-only bridge).

Frozen capabilities (I-7):
  instagram_public_profile  — default provider apify, credential scope
      INSTALLATION (configured once per installation via vault,
      e.g. APIFY token stored as vault://installation/...). Projects store
      ONLY handle/account metadata/connection reference — never an API key.
  instagram_owned_insights  — default provider meta, auth WORKSPACE OAUTH
      (workspace/project-scoped; token lives in the vault, framework in
      oauth.py — see EXTERNAL AUTH BLOCKER disclosure there).

Fallback chain labels (brightdata, browser) are PROVIDERS, not capabilities.
Fail-closed: unknown capability or absent registry => None, never a vendor.
"""
from __future__ import annotations

from app.database.identity import DEFAULT_PROJECT_ID

from . import registry_bridge

CAP_INSTAGRAM_PUBLIC_PROFILE = "instagram_public_profile"
CAP_INSTAGRAM_OWNED_INSIGHTS = "instagram_owned_insights"
FROZEN_CAPABILITIES = (CAP_INSTAGRAM_PUBLIC_PROFILE, CAP_INSTAGRAM_OWNED_INSIGHTS)

DEFAULT_PROVIDER = {
    CAP_INSTAGRAM_PUBLIC_PROFILE: "apify",
    CAP_INSTAGRAM_OWNED_INSIGHTS: "meta",
}
# Credential scope per capability (I-2 scope enum: installation|project|user).
# Owned auth is workspace OAuth and is recorded at project scope.
CREDENTIAL_SCOPE = {
    CAP_INSTAGRAM_PUBLIC_PROFILE: "installation",
    CAP_INSTAGRAM_OWNED_INSIGHTS: "project",
}

_i7_registered = False


def capability_for(owned: bool) -> str:
    return CAP_INSTAGRAM_OWNED_INSIGHTS if owned else CAP_INSTAGRAM_PUBLIC_PROFILE


def resolve_capability(capability: str, project_id: str | None = None):
    if capability not in FROZEN_CAPABILITIES:
        return None
    lookup_project = project_id
    if capability == CAP_INSTAGRAM_PUBLIC_PROFILE and not lookup_project:
        try:
            from app.services import state
            from app import deps
            with deps.get_db() as conn:
                lookup_project = state.active_project_id(conn)
        except Exception:
            lookup_project = DEFAULT_PROJECT_ID
    try:
        return registry_bridge.resolve(capability, lookup_project)
    except Exception:
        return None


def i7_records(project_id: str | None = None) -> list[dict]:
    records = [{
        "integration_id": "instagram-public-profile",
        "capability": CAP_INSTAGRAM_PUBLIC_PROFILE,
        "provider": DEFAULT_PROVIDER[CAP_INSTAGRAM_PUBLIC_PROFILE],
        "scope": CREDENTIAL_SCOPE[CAP_INSTAGRAM_PUBLIC_PROFILE],
        "status": "not_configured",
        "config": {"actor_id": "apify/instagram-profile-scraper"},
        "secret_ref": None,
        "last_health": None,
    }]
    if project_id:
        records.append({
            "integration_id": f"instagram-owned-insights-{project_id}"[:64],
            "capability": CAP_INSTAGRAM_OWNED_INSIGHTS,
            "provider": DEFAULT_PROVIDER[CAP_INSTAGRAM_OWNED_INSIGHTS],
            "scope": CREDENTIAL_SCOPE[CAP_INSTAGRAM_OWNED_INSIGHTS],
            "status": "not_configured",
            "config": {"project_id": project_id, "auth": "workspace_oauth"},
            "secret_ref": None,
            "last_health": None,
        })
    return records


def ensure_i7_registered(project_id: str | None = None) -> bool:
    """Idempotently register I-7 records that the registry does not know yet.
    Never overwrites an existing record (resolve-first). Never raises; returns
    False when the registry is absent or every call shape was rejected."""
    global _i7_registered
    if _i7_registered and project_id is None:
        return True
    try:
        ok = True
        for record in i7_records(project_id):
            if resolve_capability(record["capability"], project_id) is not None:
                continue
            if not registry_bridge.register(record):
                ok = False
        if ok and project_id is None:
            _i7_registered = True
        return ok
    except Exception:
        return False


def connect_public_profile(secret: str, *, provider: str = "apify") -> dict:
    """Installation setup: store the APIFY token in the vault (installation
    scope) and register the public-profile capability as connected. Returns
    the opaque vault ref — never the secret itself."""
    if not secret:
        return {"ok": False, "status": "error", "reason": "empty_secret"}
    ref = registry_bridge.store_secret(
        secret, scope=CREDENTIAL_SCOPE[CAP_INSTAGRAM_PUBLIC_PROFILE],
        name=f"{provider}_api_token")
    if not ref:
        return {"ok": False, "status": "error", "reason": "vault_unavailable"}
    record = {
        "integration_id": "instagram-public-profile",
        "capability": CAP_INSTAGRAM_PUBLIC_PROFILE,
        "provider": provider,
        "scope": CREDENTIAL_SCOPE[CAP_INSTAGRAM_PUBLIC_PROFILE],
        "status": "connected",
        "config": {"actor_id": "apify/instagram-profile-scraper"},
        "secret_ref": ref,
        "last_health": None,
    }
    if not registry_bridge.register(record):
        return {"ok": False, "status": "error", "reason": "register_failed",
                "secret_ref": ref}
    global _i7_registered
    _i7_registered = True
    return {"ok": True, "status": "connected",
            "capability": CAP_INSTAGRAM_PUBLIC_PROFILE, "secret_ref": ref}


def register_owned_connection(project_id: str, secret_ref: str, *,
                              provider: str = "meta",
                              config: dict | None = None) -> bool:
    """Record a workspace-OAuth connection for instagram_owned_insights at
    project scope. Secret material lives only in the vault ref."""
    if not project_id or not secret_ref:
        return False
    merged = {"project_id": project_id, "auth": "workspace_oauth"}
    merged.update(config or {})
    record = {
        "integration_id": f"instagram-owned-insights-{project_id}"[:64],
        "capability": CAP_INSTAGRAM_OWNED_INSIGHTS,
        "provider": provider,
        "scope": CREDENTIAL_SCOPE[CAP_INSTAGRAM_OWNED_INSIGHTS],
        "status": "connected",
        "config": merged,
        "secret_ref": secret_ref,
        "last_health": None,
    }
    return registry_bridge.register(record)
