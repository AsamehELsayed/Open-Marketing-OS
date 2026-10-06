"""Instagram audit tool (GREEN, read-only). Runs the provider priority chain
and returns NORMALIZED InstagramAuditData — never raw vendor payloads, never
invented metrics. Unavailable fields stay absent; gaps land in `unknowns`."""
from app.services.social.instagram import router as ig_router


def _social_accounts_for_project(conn, project_id: str) -> list:
    """First-class social state (W1 contract), defensive: [] when unavailable."""
    try:
        from app.database import repos
        sa = getattr(repos, "SocialAccounts", None)
        if sa is None:
            return []
        rows = sa.for_project(conn, project_id) or []
        out = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            out.append({"platform": str(r.get("platform", "")),
                        "handle": str(r.get("handle", "")),
                        "url": str(r.get("url", "")),
                        "status": str(r.get("status", "LIKELY")),
                        "source": str(r.get("source", "manual"))})
        order = {"VERIFIED": 0, "LIKELY": 1, "UNVERIFIED": 2}
        out.sort(key=lambda a: order.get(str(a.get("status", "")).upper(), 3))
        return out[:12]
    except Exception:
        return []


def _handle_from_project(conn, project_id: str) -> tuple[str, bool]:
    """Project's own Instagram handle: SocialAccounts[instagram] table-first,
    legacy settings_json.instagram_handle as compat fallback.
    Returns (handle, owned) where owned=True iff table status=VERIFIED."""
    for a in _social_accounts_for_project(conn, project_id):
        if a.get("platform") == "instagram" and a.get("handle"):
            return str(a["handle"]).strip().lstrip("@"), str(a.get("status", "")).upper() == "VERIFIED"
    from app.database import repos
    import json
    handle, owned = "", False
    try:
        proj = repos.Projects.get(conn, project_id)
        settings = json.loads((proj or {}).get("settings_json", "") or "{}")
        handle = str(settings.get("instagram_handle", "") or "").strip().lstrip("@")
        owned = bool(settings.get("instagram_connected", False)) and bool(handle)
    except Exception:
        pass
    return handle, owned


import re as _re

_INTENT_WORDS = ("instagram", "انستجرام", "انستغرام", "انستا")


def detect_instagram_intent(text: str) -> dict | None:
    """Keyword intent + optional explicit @handle or extracted username. Returns None when unrelated."""
    lowered = (text or "").lower()
    if not any(w in lowered for w in _INTENT_WORDS):
        return None
    m = _re.search(r"@([a-zA-Z0-9_.]{1,40})", text or "")
    if m:
        return {"username": m.group(1)}
    # Match URL
    m_url = _re.search(r"instagram\.com/([a-zA-Z0-9_.]{1,40})", text or "")
    if m_url:
        return {"username": m_url.group(1)}
    # Match phrases like 'scrap them instagram', 'scrape the-brand instagram account', 'audit target instagram'
    m_scrape = _re.search(r"(?:scrap|scrape|audit|check|fetch|pull|profile)\s+([a-zA-Z0-9_.]{2,30})\s+(?:instagram|ig)", lowered)
    if m_scrape and m_scrape.group(1) not in ("the", "this", "our", "an", "my"):
        return {"username": m_scrape.group(1)}
    m_scrape2 = _re.search(r"(?:instagram|ig)\s+(?:account\s+)?(?:for\s+|of\s+)([a-zA-Z0-9_.]{2,30})", lowered)
    if m_scrape2 and m_scrape2.group(1) not in ("account", "profile", "data", "post", "posts", "page", "audit"):
        return {"username": m_scrape2.group(1)}
    m_scrape3 = _re.search(r"(?:instagram|ig)\s+account\s+([a-zA-Z0-9_.]{2,30})", lowered)
    if m_scrape3 and m_scrape3.group(1) not in ("account", "profile", "data", "post", "posts", "page", "audit"):
        return {"username": m_scrape3.group(1)}
    return {"username": ""}


def t_instagram_audit(conn, *, project_id, root, args):
    """Provider-first executor ordering: explicit username →
    project SocialAccounts[instagram] → legacy handle compat → NEEDS_HANDLE.
    Provider chain runs before any fallback."""
    from app.services.social.instagram.base import normalize_handle
    raw_user = str(args.get("username", "") or "").strip()
    username = normalize_handle(raw_user)
    owned_arg = args.get("owned", None)
    if not username:
        username, owned_default = _handle_from_project(conn, project_id)
        owned = owned_default if owned_arg is None else bool(owned_arg)
        if not username:
            return {"ok": True, "audit": None, "status": "NEEDS_HANDLE",
                    "note": "I don't have this project's Instagram yet — what's the @handle?"}
    else:
        owned = bool(owned_arg) if owned_arg is not None else False
    try:
        audit = ig_router.audit(username, owned=owned, project_id=project_id)
    except Exception as e:
        return {"ok": False, "error": f"Instagram research failed: {e}"[:300], "status": "failed"}
    status = (audit.get("evidence", {}) or {}).get("status", "unavailable")
    mapped = ("VERIFIED" if status == "verified" else
              "PARTIAL" if status == "partial" else "NOT ACCESSIBLE")
    if mapped == "NOT ACCESSIBLE":
        has_cfg = audit.get("has_configured_provider", False)
        prim = audit.get("primary_failure") or {}
        p_name = audit.get("provider") or prim.get("provider") or "configured provider"
        err_code = prim.get("error_code") or "integration error"

        if has_cfg:
            note = (f"Instagram research couldn't run.\n\n"
                    f"{p_name} connection is configured, but the scraper provider returned an {err_code.lower() if isinstance(err_code, str) else 'integration error'}.\n\n"
                    f"[Retry] [Test Instagram Connection] [Use another provider]")
        else:
            note = ("Instagram research couldn't run because no Instagram provider is connected.\n\n"
                    "Connect an Instagram provider in Settings to pull live profile and post data.\n\n"
                    "[Configure in Settings]")
        return {"ok": True, "audit": audit, "status": mapped,
                "provider": audit.get("provider") or "", "note": note}
    return {"ok": True, "audit": audit, "status": mapped,
            "provider": audit.get("provider") or ""}


def t_get_social_accounts(conn, *, project_id, root, args):
    """GREEN read-only: project-filtered social accounts, VERIFIED first."""
    if not project_id or not str(project_id).strip():
        return {"ok": False, "error": "project_id is required (fail closed)", "status": "failed"}
    return {"ok": True, "accounts": _social_accounts_for_project(conn, project_id),
            "status": "VERIFIED"}


def register_instagram_tools(registry):
    from .registry import ToolDef
    registry.register(ToolDef(
        name="instagram_audit",
        description=("Audit an Instagram account via configured providers (normalized profile+posts; "
                     "evidence only, never paste raw payloads). Use the project's own handle when the user "
                     "says 'our/our company's Instagram'; pass a username for competitors."),
        parameters={"type": "object",
                    "properties": {"username": {"type": "string"},
                                   "owned": {"type": "boolean"}}},
        side_effect="green", handler=t_instagram_audit))
    registry.register(ToolDef(
        name="get_social_accounts",
        description=("Read this project's recorded social accounts (platform/handle/url/status/source). "
                     "Use first for any 'what is our handle' question before web or screenshots."),
        parameters={"type": "object", "properties": {}},
        side_effect="green", handler=t_get_social_accounts))
