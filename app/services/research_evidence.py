"""Persisted web research evidence (DEV-043).

Sources returned by a real search are stored per project (and per campaign
when the turn created one) so they survive reload and restart and can be
shown in the campaign work package. Two honesty levels are never merged:

  search_lead - a search engine returned the URL; OMOS has not read the page
  inspected   - OMOS fetched the page itself

Evidence is its own table. Nothing here touches deliverable content, so
user-approved text can never be overwritten by research.
"""
from __future__ import annotations

import ipaddress
import re
import urllib.parse
import uuid
from datetime import datetime, timezone

_RESERVED_SUFFIXES = (".test", ".invalid", ".localhost", ".local", ".example",
                      ".internal", ".lan")
_RESERVED_HOSTS = {"localhost", "example.com", "example.org", "example.net"}
_PROVIDERS = {"web_search", "website_fetch"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def clean_url(raw) -> str:
    """Return a user-displayable http(s) URL, or '' for anything else.

    Placeholder, reserved-test and private hosts are rejected so fixtures and
    fabricated links cannot appear as evidence in a campaign.
    """
    url = str(raw or "").strip()[:500]
    if any(ord(c) < 33 or ord(c) == 127 for c in url):
        return ""
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https") or not host:
        return ""
    if "@" in parts.netloc:
        return ""
    if re.fullmatch(r"[0-9a-fx.]+", host) and not _is_ip(host):
        return ""  # shorthand/obfuscated numeric hosts such as 127.1 or 0x7f.0.0.1
    if host in _RESERVED_HOSTS or host.endswith(_RESERVED_SUFFIXES) \
            or host.endswith(tuple("." + h for h in _RESERVED_HOSTS)):
        return ""
    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global:
            return ""
    except ValueError:
        if "." not in host:
            return ""
    return url


def record_leads(conn, project_id: str, campaign_id: str, results, *,
                 query: str = "", turn_id: str = "",
                 provider: str = "web_search") -> int:
    """Store search results as unverified leads. Returns rows kept."""
    if not str(project_id or "").strip() or provider not in _PROVIDERS:
        return 0
    kept = 0
    stamp = _now()
    for r in results or []:
        if not isinstance(r, dict):
            continue
        url = clean_url(r.get("url") or r.get("path"))
        if not url:
            continue
        conn.execute(
            "INSERT INTO research_sources (id, project_id, campaign_id, url, "
            "title, snippet, query, provider, turn_id, retrieved_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(project_id, campaign_id, url) DO UPDATE SET "
            "retrieved_at=excluded.retrieved_at, "
            "title=CASE WHEN research_sources.verification='inspected' "
            "THEN research_sources.title ELSE excluded.title END, "
            "snippet=CASE WHEN research_sources.verification='inspected' "
            "THEN research_sources.snippet ELSE excluded.snippet END",
            ("rs_" + uuid.uuid4().hex[:24], project_id, campaign_id or "", url,
             str(r.get("title") or "")[:200], str(r.get("snippet")
                                                  or r.get("text") or "")[:600],
             str(query or "")[:300], provider, str(turn_id or "")[:100], stamp))
        kept += 1
    conn.commit()
    return kept


def mark_inspected(conn, project_id: str, url: str, *, title: str = "",
                   http_status: int | None = None, campaign_id: str = "") -> bool:
    """Record that OMOS fetched ``url`` itself (upgrades every matching lead)."""
    clean = clean_url(url)
    if not clean or not str(project_id or "").strip():
        return False
    stamp = _now()
    conn.execute(
        "UPDATE research_sources SET verification='inspected', inspected_at=?, "
        "http_status=?, title=CASE WHEN ?<>'' THEN ? ELSE title END "
        "WHERE project_id=? AND url=?",
        (stamp, http_status, title[:200], title[:200], project_id, clean))
    conn.execute(
        "INSERT OR IGNORE INTO research_sources (id, project_id, campaign_id, "
        "url, title, provider, retrieved_at, verification, inspected_at, "
        "http_status) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("rs_" + uuid.uuid4().hex[:24], project_id, campaign_id or "", clean,
         title[:200], "website_fetch", stamp, "inspected", stamp, http_status))
    conn.commit()
    return True


def list_sources(conn, project_id: str, campaign_id: str, limit: int = 20) -> list[dict]:
    """This campaign's sources plus the project's campaign-less research."""
    rows = conn.execute(
        "SELECT url, title, snippet, query, provider, retrieved_at, "
        "verification, inspected_at, campaign_id FROM research_sources "
        "WHERE project_id=? AND (campaign_id=? OR campaign_id='') "
        "ORDER BY (verification='inspected') DESC, retrieved_at DESC, id",
        (project_id, campaign_id)).fetchall()
    seen, out = set(), []
    for row in rows:
        d = dict(row)
        if d["url"] in seen or not clean_url(d["url"]):
            continue
        seen.add(d["url"])
        d["scope"] = "campaign" if d.pop("campaign_id") else "project"
        out.append(d)
        if len(out) >= limit:
            break
    return out
