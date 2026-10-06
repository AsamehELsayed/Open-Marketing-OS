"""DEV-007R-HOTFIX-2 — capability execution + deterministic synthesis.

Chain (all vendor knowledge contained in the social capability layer —
LangGraph never learns provider internals):

  intent (TOOL_CAPABILITY)
    → ToolRegistry.resolve(capability=instagram_public_profile, project_id)
      (social capabilities bridge; provider chain chosen there)
    → execute (frozen provider chain via app.services.social.instagram)
    → normalized result   → tool_runs row persisted
    → deterministic synthesis (no LLM free-form claims, no RAG fallback)

If an explicitly requested capability fails, the answer is an honest
limitation + recovery options. Project knowledge / RAG is NEVER substituted.
"""
from __future__ import annotations

import time as _time
import urllib.parse
import uuid
from datetime import datetime, timezone

# ------------------------------------------------------------------ synthesis

WEBSITE_CAPABILITIES = ("website_fetch", "website_crawl",
                        "website_marketing_audit")

_OK_TEMPLATE = (
    "Instagram research for @{handle} (source: {source}):\n"
    "- Display name: {display_name}\n"
    "- Followers: {followers} / Following: {following}\n"
    "- Posts count: {posts_count}\n{posts_block}"
)

# DEV-008: these are shown verbatim to end users when a request is missing a
# handle or a URL. They previously used a real business's handle and domain as
# the example, which put a third party's identity into every fresh install's
# first Instagram or website prompt. Examples must be neutral placeholders.
_NEEDS_HANDLE = (
    "Which Instagram account should I scrape? "
    "Give me the @handle (for example @yourbrand)."
)

_NEEDS_URL = (
    "Which website should I analyze? "
    "Give me the URL (for example https://yourbusiness.com)."
)


def _emit_event(on_event, event_type: str, payload: dict) -> None:
    if not callable(on_event):
        return
    try:
        on_event(event_type, dict(payload or {}))
    except Exception:
        return


def _provider_label(provider: str) -> str:
    return "The Instagram provider"


def _safe_failure_note(provider: str, error_code: str) -> str:
    label = _provider_label(provider)
    code = str(error_code or "").strip().upper()
    if code == "AUTH_ERROR":
        detail = (
            f"{label} needs a valid connection. Check the Instagram connection "
            "in Settings and try again."
        )
    elif code in ("CONFIG_ERROR", "PROVIDER_NOT_FOUND"):
        detail = (
            f"{label} could not complete the Instagram request. Check the "
            "connection settings or use another provider."
        )
    else:
        detail = (
            f"{label} could not complete the Instagram request right now. "
            "Please try again or use another provider."
        )
    return f"{detail}\n\n[Retry] [Test Instagram Connection] [Use Another Provider]"


def _safe_failure_detail(note: str) -> str:
    text = str(note or "").strip()
    lowered = text.lower()
    actions = "[Retry] [Test Instagram Connection] [Use Another Provider]"
    if any(token in lowered for token in (
            "auth_error", "unauthorized", "authentication failed",
            "traceback", "bearer ", "http://", "https://", "api.")):
        return (
            "The Instagram connection needs attention. Check the connection "
            f"in Settings and try again.\n\n{actions}"
        )
    return text


def _fmt_num(v) -> str:
    try:
        return f"{int(v):,}" if v is not None else "unknown"
    except (TypeError, ValueError):
        return "unknown"


def synthesize_success(handle: str, audit: dict) -> str:
    acct = audit.get("account") or {}
    ev = audit.get("evidence") or {}
    source = ev.get("source") or audit.get("source_chain", ["unknown"])[-1] or "unknown"
    posts = audit.get("posts") or []
    post_lines = []
    for p in posts[:3]:
        if not isinstance(p, dict):
            continue
        cap = str(p.get("caption") or p.get("text") or "").strip().replace("\n", " ")
        if cap:
            post_lines.append(f"- Latest post: {cap[:160]}")
    posts_block = ("\nRecent posts:\n" + "\n".join(post_lines)) if post_lines else ""
    return _OK_TEMPLATE.format(
        handle=(handle or acct.get("handle") or "unknown").lstrip("@"),
        source=source,
        display_name=(acct.get("display_name") or acct.get("full_name") or "unknown"),
        followers=_fmt_num(acct.get("followers")),
        following=_fmt_num(acct.get("following")),
        posts_count=_fmt_num(acct.get("posts_count") or len(posts)),
        posts_block=posts_block,
    )


def synthesize_failure(handle: str, note: str) -> str:
    base = f"I couldn't retrieve @{handle} from Instagram."
    body = _safe_failure_detail(note)
    return f"{base}\n\n{body}" if body else base


# --------------------------------------------------------------- tool_run row

def persist_tool_run(tool_runs_conn, *, project_id: str,
                     tool_id: str, provider: str, status: str,
                     latency_ms: int = 0) -> str:
    """Insert one tool_runs row for legacy non-employee graph calls.

    Employee-owned runs add their orchestration context in employee_tools.
    These older graph paths do not have that context, so leave the additive
    columns empty while preserving the original nine-field telemetry API.
    """
    tool_run_id = uuid.uuid4().hex
    try:
        from app.services.tools.registry import (
            _TOOL_RUN_INSERT,
            ensure_tool_runs,
        )

        ensure_tool_runs(tool_runs_conn)
        tool_runs_conn.execute(
            _TOOL_RUN_INSERT,
            (tool_run_id, project_id or "", tool_id, provider,
             _now(), _now(), status, int(latency_ms or 0), "",
             None, None, None, 0))
        tool_runs_conn.commit()
    except Exception as e:
        # Telemetry must never break tool execution, but it must be visible.
        try:
            import sys

            print(f"[tool_run] persist failed: {e}", file=sys.stderr, flush=True)
        except Exception:
            pass
    return tool_run_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------- website synthesis helpers

def _website_project_url(conn, project_id: str) -> str:
    """Authoritative project website (structured state only, never RAG)."""
    try:
        from app.database import repos
        import json as _json
        row = repos.Projects.get(conn, project_id)
        if not row:
            return ""
        url = str(row.get("website", "") or "").strip()
        if url:
            return url
        settings = _json.loads(row.get("settings_json", "") or "{}")
        return str(settings.get("website", "") or "").strip()
    except Exception:
        return ""


def _footer(status_line: str) -> str:
    return f"\n\nSTATUS: {status_line} / SOURCE: website_fetch / CONFIDENCE: MEDIUM"


def synthesize_website_audit(url: str, payload: dict) -> str:
    audit = payload.get("audit") or {}
    checklist = audit.get("checklist") or {}
    pages = payload.get("pages") or []
    fetched_at = (pages[0].get("fetched_at", "") if pages
                  and isinstance(pages[0], dict) else "")
    socials = checklist.get("social_links") or {}
    social_on = [k for k, v in socials.items() if v]
    social_off = [k for k, v in socials.items() if not v]
    lines = [
        f"Website audit for {url} (source: live fetch {fetched_at}):",
        f"- Page title: {'found — ' + str(pages[0].get('title', '')) if checklist.get('has_title') else 'not found'}",
        f"- Meta description: {'present' if checklist.get('has_meta_description') else 'not found'}",
        f"- H1 heading: {'found — ' + str(pages[0].get('h1', '')) if checklist.get('has_h1') else 'not found'}",
        f"- Call-to-action keywords (contact / sign up / get started / اشترك / تواصل): {'observed' if checklist.get('has_cta_keywords') else 'not observed'}",
        f"- Contact email on page: {'observed' if checklist.get('has_email') else 'not observed'}",
        f"- Phone number on page: {'observed' if checklist.get('has_phone') else 'not observed'}",
        f"- Social links: {'found: ' + ', '.join(social_on) if social_on else 'none observed'}"
        + ("" if not social_off else f" (not observed: {', '.join(social_off)})"),
        f"- HTTPS: {'yes' if checklist.get('is_https') else 'no'}",
    ]
    pages_checked = checklist.get("pages_checked")
    if pages_checked:
        lines.append(f"- Pages checked: {pages_checked}")
    return "\n".join(lines) + _footer("PARTIALLY VERIFIED")


def synthesize_website_crawl(url: str, payload: dict) -> str:
    pages = payload.get("pages") or []
    fetched = [p for p in pages if isinstance(p, dict)
               and p.get("status") == "fetched"]
    lines = [f"Crawled {len(fetched)} page{'s' if len(fetched) != 1 else ''} "
             f"from {url} (source: live fetch):"]
    for p in fetched[:10]:
        title = str(p.get("title", "") or "").strip() or "(no title observed)"
        lines.append(f"- {p.get('final_url') or p.get('url', '')}: {title}")
    if not fetched:
        lines.append("- No pages could be fetched.")
    return "\n".join(lines) + _footer(
        "PARTIALLY VERIFIED" if fetched else "NOT ACCESSIBLE")


def synthesize_website_fetch(url: str, payload: dict) -> str:
    page = payload.get("page") or {}
    title = str(page.get("title", "") or "").strip() or "(no title observed)"
    desc = str(page.get("meta_description", "") or "").strip()
    text = str(page.get("text_head", "") or "").strip()[:200]
    lines = [f"Fetched {url} (source: live fetch {page.get('fetched_at', '')}):",
             f"- Title: {title}"]
    if desc:
        lines.append(f"- Description: {desc}")
    if text:
        lines.append(f"- Text head: {text}")
    return "\n".join(lines) + _footer("PARTIALLY VERIFIED")


def synthesize_website_failure(url: str) -> str:
    return (f"I couldn't fetch {url} right now.\n\n"
            "[Retry] [Check the site URL]")


# --------------------------------------------------------- resolve + execute

def resolve_and_execute(conn, *, project_id: str, capability: str,
                        args: dict, turn_id: str = "", on_event=None) -> dict:
    """Authoritative capability resolution + execution + persistence.

    Returns graph-ready fields: answer, tool_run_id, provider, status.
    Fails closed: unknown capability / register failure keeps the honest
    limitation answer path — never a RAG fallback.
    """
    if capability in WEBSITE_CAPABILITIES:
        return _resolve_and_execute_website(
            conn, project_id=project_id, capability=capability,
            args=args, turn_id=turn_id, on_event=on_event)

    from app.services.social.instagram.base import normalize_handle

    handle = normalize_handle(str((args or {}).get("handle", "") or "")).lstrip("@")
    owned = False
    if not handle:
        # Argument supply from AUTHORITATIVE project state only (no RAG):
        from app.services.tools.instagram_tools import _handle_from_project
        handle, owned = _handle_from_project(conn, project_id)
    if not handle:
        return {"answer": _NEEDS_HANDLE, "route_note": "NEEDS_HANDLE",
                "tool_run_id": "", "provider": "", "status": "needs_handle"}

    _emit_event(on_event, "tool_started", {
        "tool": "instagram_audit",
        "args": {"username": handle},
    })

    # Owned/verified project account flips capability to the owned chain which
    # falls back through the same frozen public chain (router handles it).
    if not owned:
        try:
            from app.database.repos import SocialAccounts
            for a in SocialAccounts.for_project(conn, project_id) or []:
                if (a.get("platform") or "") == "instagram":
                    owned = (str(a.get("status") or "").upper()) == "VERIFIED"
                    break
        except Exception:
            owned = False

    from app.services.social.instagram.router import audit_capability
    import time as _time
    t0 = _time.monotonic()
    try:
        audit = audit_capability(capability, handle, project_id=project_id)
    except Exception:
        audit = {"evidence": {"status": "unavailable"}, "account": None,
                 "posts": [], "unknowns": ["Instagram research could not complete the request."],
                 "attempts": [], "has_configured_provider": False,
                 "primary_failure": None}
    latency_ms = int((_time.monotonic() - t0) * 1000)

    status = (audit.get("evidence") or {}).get("status", "unavailable")
    succeeded = status in ("verified", "partial")
    attempts = audit.get("attempts") or []
    ok_attempt = next((a for a in attempts if a.get("status") == "SUCCEEDED"), None)
    provider = ok_attempt.get("provider", "") if ok_attempt else \
        ((audit.get("primary_failure") or {}).get("provider", "") or
         (attempts[0].get("provider", "") if attempts else ""))
    run_status = "success" if succeeded else "failed"
    tool_run_id = persist_tool_run(
        conn, project_id=project_id, tool_id=capability, provider=provider,
        status=run_status, latency_ms=latency_ms)
    _emit_event(on_event, "tool_completed", {
        "tool": "instagram_audit",
        "tool_run_id": tool_run_id,
        "obs": {
            "ok": succeeded,
            "audit": audit,
        },
    })

    if succeeded:
        answer = synthesize_success(handle, audit)
        social_rows = [{"path": f"@{handle}", "chunk_id": tool_run_id or "run",
                        "file_sha": "",
                        "status_tag": "VERIFIED" if status == "verified" else "PARTIAL"}]
    else:
        primary = audit.get("primary_failure") or {}
        if audit.get("has_configured_provider"):
            note = _safe_failure_note(
                str(primary.get("provider") or ""),
                str(primary.get("error_code") or ""))
        else:
            note = ("No Instagram provider is connected yet.\n\n"
                    "[Configure in Settings]")
        answer = synthesize_failure(handle, note)
        social_rows = []

    return {"answer": answer, "tool_run_id": tool_run_id, "provider": provider,
            "status": run_status, "capability": capability,
            "handle": handle, "social_results": social_rows}


def _resolve_and_execute_website(conn, *, project_id: str, capability: str,
                                 args: dict, turn_id: str = "",
                                 on_event=None) -> dict:
    """Website capability chain: registry execute + honest synthesis.

    Args come from the intent layer; the ONLY auto-fill source is the
    authoritative project row (website column / settings_json) — never RAG.
    """
    args = dict(args or {})
    raw_url = str(args.get("url", "") or "").strip()
    if raw_url:
        url = raw_url
        if not urllib.parse.urlparse(url).scheme:
            url = "https://" + url.lstrip("/")
    else:
        url = _website_project_url(conn, project_id)
    if not url:
        return {"answer": _NEEDS_URL, "route_note": "NEEDS_URL",
                "tool_run_id": "", "provider": "", "status": "needs_url",
                "capability": capability, "url": "", "social_results": []}

    call_args = {"url": url}
    if capability == "website_crawl" and args.get("max_pages") is not None:
        call_args["max_pages"] = args["max_pages"]

    _emit_event(on_event, "tool_started", {
        "tool": capability,
        "args": {"url": url},
    })

    from app.graphs.adapters import ToolRegistryAdapter
    import time as _t
    t0 = _t.monotonic()
    try:
        result = ToolRegistryAdapter().execute(
            conn, project_id=project_id, root="", name=capability,
            args=call_args)
    except Exception:
        result = {"ok": False, "error": "The tool could not be completed.",
                  "status": "failed"}
    latency_ms = int((_t.monotonic() - t0) * 1000)

    ok = bool(result.get("ok"))
    tool_status = result.get("status", "")
    succeeded = ok and tool_status not in ("NOT ACCESSIBLE",)
    run_status = "success" if succeeded else "failed"
    tool_run_id = persist_tool_run(
        conn, project_id=project_id, tool_id=capability,
        provider="website_fetcher", status=run_status, latency_ms=latency_ms)
    _emit_event(on_event, "tool_completed", {
        "tool": capability,
        "tool_run_id": tool_run_id,
        "obs": {
            "ok": ok,
            "audit": result,
        },
    })

    if not ok:
        answer = synthesize_website_failure(url)
    elif capability == "website_marketing_audit":
        if tool_status == "NOT ACCESSIBLE":
            answer = synthesize_website_failure(url)
        else:
            answer = synthesize_website_audit(url, result)
    elif capability == "website_crawl":
        if tool_status == "NOT ACCESSIBLE":
            answer = synthesize_website_failure(url)
        else:
            answer = synthesize_website_crawl(url, result)
    else:  # website_fetch
        if tool_status == "NOT ACCESSIBLE":
            answer = synthesize_website_failure(url)
        else:
            answer = synthesize_website_fetch(url, result)

    return {"answer": answer, "tool_run_id": tool_run_id,
            "provider": "website_fetcher" if succeeded else "",
            "status": run_status, "capability": capability, "url": url,
            "social_results": []}
