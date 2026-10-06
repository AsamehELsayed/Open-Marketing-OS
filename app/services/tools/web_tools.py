"""Web research + website audit tools (v0.2 §6.3, Tier A). Evidence only,
STATUS-tagged, mockable.

Default transport is injectable: tests pass transport= callable; production
uses urllib with a short timeout. No API key required for the stub path —
results are honest about accessibility (no invented metrics).
"""
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

STATUS = "PARTIALLY VERIFIED"

_WEBSITE_UA = "Mozilla/5.0 (compatible; OMOS/1.0; website-audit)"
_WEBSITE_FETCH_TIMEOUT_S = 10
_TEXT_HEAD_LIMIT = 2000
_MAX_LINKS = 50
_MAX_AUDIT_PAGES = 3
_WEBSITE_BUDGET_S = 15.0
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _default_transport(query: str, count: int) -> list[dict]:
    # Honest stub: no external network in default suite. Deployments may set
    # WEB_SEARCH_URL to a real search endpoint returning JSON.
    return []


def t_web_search(conn, *, project_id, root, args, transport=None):
    query = (args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "query is required", "status": "failed"}
    try:
        count = max(1, min(10, int(args.get("count", 5))))
    except (ValueError, TypeError):
        count = 5
    transport = transport or _default_transport
    try:
        results = transport(query, count) or []
    except Exception as e:
        return {"ok": True, "results": [], "status": "NOT ACCESSIBLE",
                "note": f"search transport failed: {e}", "query": query}
    trimmed = [{"url": r.get("url", ""), "title": r.get("title", "")[:200],
                "snippet": r.get("snippet", "")[:600]} for r in results[:count]]
    status = STATUS if trimmed else "NOT ACCESSIBLE"
    return {"ok": True, "results": trimmed, "status": status,
            "confidence": "MEDIUM" if trimmed else "LOW", "query": query}


def make_web_handler(transport=None):
    def _h(conn, *, project_id, root, args):
        return t_web_search(conn, project_id=project_id, root=root, args=args, transport=transport)
    return _h


def register_web_tools(registry, transport=None):
    from .registry import ToolDef
    registry.register(ToolDef(name="web_search",
                              description="Live web research for external/current facts (evidence only, cite URLs).",
                              parameters={"type": "object", "required": ["query"],
                                          "properties": {"query": {"type": "string"},
                                                         "count": {"type": "integer"}}},
                              side_effect="green", handler=make_web_handler(transport)))


# ------------------------------------------------------------- website tools

class _PageParser(HTMLParser):
    """Stdlib parser extracting title/meta/h1/text-head/anchors. No inventing."""

    _SKIP = frozenset({"script", "style", "noscript", "svg", "template"})
    _CTA_KEYWORDS = ("contact", "whatsapp", "sign up", "get started",
                     "اشترك", "تواصل", "ابدأ")
    _SOCIAL_HOSTS = {
        "instagram": ("instagram.com",),
        "twitter": ("twitter.com", "x.com"),
        "linkedin": ("linkedin.com",),
        "tiktok": ("tiktok.com",),
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta_description = ""
        self.h1 = ""
        self.has_viewport = False
        self.text_parts: list[str] = []
        self.links: list[dict] = []
        self._skip_depth = 0
        self._in_title = False
        self._in_h1 = False
        self._title_buf: list[str] = []
        self._h1_buf: list[str] = []
        self._in_a = False
        self._a_href = ""
        self._a_buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs or {})
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        elif tag == "h1" and not self.h1:
            self._in_h1 = True
        elif tag == "meta":
            name = (a.get("name") or "").strip().lower()
            if name == "description" and not self.meta_description:
                self.meta_description = (a.get("content") or "").strip()
            elif name == "viewport":
                self.has_viewport = True
        elif tag == "a":
            self._in_a = True
            self._a_href = (a.get("href") or "").strip()
            self._a_buf = []

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if tag == "title" and self._in_title:
            self._in_title = False
            self.title = " ".join("".join(self._title_buf).split())
        elif tag == "h1" and self._in_h1:
            self._in_h1 = False
            if not self.h1:
                self.h1 = " ".join("".join(self._h1_buf).split())
        elif tag == "a" and self._in_a:
            self._in_a = False
            text = " ".join("".join(self._a_buf).split())
            if self._a_href and len(self.links) < _MAX_LINKS:
                self.links.append({"href": self._a_href, "text": text[:200]})

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_title:
            self._title_buf.append(data)
            return
        if self._in_h1:
            self._h1_buf.append(data)
        if self._in_a:
            self._a_buf.append(data)
            return
        if data.strip():
            self.text_parts.append(data.strip())


def _default_website_transport(url: str, timeout_s: int = 10) -> dict:
    """Real urllib fetch. Never raises: errors come back as status=error."""
    started = time.monotonic()
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": _WEBSITE_UA,
                          "Accept": "text/html,application/xhtml+xml"})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            body = resp.read(1_000_000).decode(charset, errors="replace")
            http_status = int(resp.status)
    except Exception:
        latency = int((time.monotonic() - started) * 1000)
        return {"status": "error", "error": "The website could not be fetched.",
                "latency_ms": latency}
    latency = int((time.monotonic() - started) * 1000)
    parser = _PageParser()
    try:
        parser.feed(body)
        parser.close()
    except Exception:
        pass
    return _parse_result(url, url, http_status, parser, latency)


def _parse_result(url: str, final_url: str, http_status: int,
                  parser: _PageParser, latency_ms: int) -> dict:
    text_head = " ".join(parser.text_parts)[:_TEXT_HEAD_LIMIT]
    return {
        "url": url,
        "final_url": final_url,
        "status": "fetched",
        "http_status": http_status,
        "title": parser.title[:300],
        "meta_description": parser.meta_description[:500],
        "h1": parser.h1[:300],
        "text_head": text_head,
        "links": parser.links,
        "has_viewport": parser.has_viewport,
        "latency_ms": latency_ms,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _normalize_url(raw: str) -> str:
    url = str(raw or "").strip()
    if not url:
        return ""
    if not urllib.parse.urlparse(url).scheme:
        url = "https://" + url.lstrip("/")
    return url


def _fetch_page(url: str, transport) -> dict:
    """One page fetch through the transport. Never raises."""
    try:
        result = transport(url, _WEBSITE_FETCH_TIMEOUT_S)
    except TypeError:
        try:
            result = transport(url)
        except Exception:
            result = {"status": "error",
                      "error": "The website could not be fetched."}
    except Exception:
        result = {"status": "error", "error": "The website could not be fetched."}
    if not isinstance(result, dict):
        return {"status": "error", "error": "The website could not be fetched.",
                "url": url}
    result.setdefault("url", url)
    return result


def _same_host(a: str, b: str) -> bool:
    try:
        ha = urllib.parse.urlparse(a).hostname or ""
        hb = urllib.parse.urlparse(b).hostname or ""
    except Exception:
        return False
    return ha.lower() == hb.lower() and bool(ha)


def _crawl(url: str, max_pages: int, transport, deadline: float) -> list[dict]:
    """Same-origin BFS. Budget: deadline (monotonic). Best-effort fetches."""
    pages: list[dict] = []
    seen = {url}
    queue = [url]
    while queue and len(pages) < max_pages and time.monotonic() < deadline:
        current = queue.pop(0)
        page = _fetch_page(current, transport)
        if page.get("status") != "fetched":
            pages.append(page)
            continue
        pages.append(page)
        base = page.get("final_url") or current
        for link in page.get("links") or []:
            href = str((link or {}).get("href", "") or "")
            if not href:
                continue
            absolute = urllib.parse.urljoin(base, href)
            absolute = urllib.parse.urldefrag(absolute)[0]
            scheme = urllib.parse.urlparse(absolute).scheme.lower()
            if scheme not in ("http", "https"):
                continue
            if not _same_host(absolute, url):
                continue
            if absolute in seen:
                continue
            seen.add(absolute)
            queue.append(absolute)
    return pages


def t_website_fetch(conn, *, project_id, root, args, transport=None):
    if not project_id or not str(project_id).strip():
        return {"ok": False, "error": "project_id is required (fail closed)",
                "status": "failed"}
    url = _normalize_url(args.get("url"))
    if not url:
        return {"ok": False, "error": "url is required", "status": "failed"}
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        return {"ok": False, "error": "url must be http or https", "status": "failed"}
    transport = transport or _default_website_transport
    page = _fetch_page(url, transport)
    if page.get("status") != "fetched":
        return {"ok": False, "status": "NOT ACCESSIBLE", "page": None,
                "url": url, "query": "",
                "error": page.get("error") or "The website could not be fetched."}
    return {"ok": True, "status": STATUS, "page": page, "url": url, "query": ""}


def t_website_crawl(conn, *, project_id, root, args, transport=None):
    if not project_id or not str(project_id).strip():
        return {"ok": False, "error": "project_id is required (fail closed)",
                "status": "failed"}
    url = _normalize_url(args.get("url"))
    if not url:
        return {"ok": False, "error": "url is required", "status": "failed"}
    try:
        max_pages = max(1, min(10, int(args.get("max_pages", 5))))
    except (TypeError, ValueError):
        max_pages = 5
    transport = transport or _default_website_transport
    deadline = time.monotonic() + _WEBSITE_BUDGET_S
    pages = _crawl(url, max_pages, transport, deadline)
    fetched = [p for p in pages if p.get("status") == "fetched"]
    status = STATUS if fetched else "NOT ACCESSIBLE"
    return {"ok": True, "status": status, "pages": pages, "crawled": len(pages),
            "url": url, "query": ""}


def _page_lower(page: dict) -> str:
    blob = " ".join(str(page.get(f, "") or "")
                    for f in ("title", "meta_description", "h1", "text_head"))
    for link in page.get("links") or []:
        blob += " " + str((link or {}).get("href", "") or "") + " " \
            + str((link or {}).get("text", "") or "")
    return blob.lower()


def _audit_pages(pages: list[dict], start_url: str) -> dict:
    fetched = [p for p in pages if p.get("status") == "fetched"]
    home = fetched[0] if fetched else None
    blob = " ".join(_page_lower(p) for p in fetched)
    social_links = {}
    for name, hosts in _PageParser._SOCIAL_HOSTS.items():
        for p in fetched:
            hit = False
            for link in p.get("links") or []:
                href = str((link or {}).get("href", "") or "").lower()
                if any(h in href for h in hosts):
                    hit = True
                    break
            if hit:
                social_links[name] = True
                break
        if name not in social_links:
            social_links[name] = False
    is_https = start_url.lower().startswith("https://")
    cta = any(k in blob for k in _PageParser._CTA_KEYWORDS)
    email = bool(_EMAIL_RE.search(blob))
    phone = any(d in blob for d in ("+20", "+1", "tel:", "+44", "+971", "+966"))
    checklist = {
        "has_title": bool(home and home.get("title")),
        "has_meta_description": bool(home and home.get("meta_description")),
        "has_h1": bool(home and home.get("h1")),
        "has_viewport": any(bool(p.get("has_viewport")) for p in fetched),
        "is_https": is_https,
        "has_cta_keywords": cta,
        "has_email": email,
        "has_phone": phone,
        "social_links": social_links,
        "pages_checked": len(fetched),
    }
    return checklist, fetched


def t_website_marketing_audit(conn, *, project_id, root, args, transport=None):
    if not project_id or not str(project_id).strip():
        return {"ok": False, "error": "project_id is required (fail closed)",
                "status": "failed"}
    url = _normalize_url(args.get("url"))
    if not url:
        return {"ok": False, "error": "url is required", "status": "failed"}
    transport = transport or _default_website_transport
    deadline = time.monotonic() + _WEBSITE_BUDGET_S
    pages = _crawl(url, 1 + _MAX_AUDIT_PAGES, transport, deadline)
    fetched = [p for p in pages if p.get("status") == "fetched"]
    if not fetched:
        return {"ok": False, "status": "NOT ACCESSIBLE", "confidence": "LOW",
                "url": url, "pages": pages, "audit": None, "query": "",
                "error": "The website could not be fetched."}
    checklist, fetched = _audit_pages(pages, url)
    home = fetched[0]
    verdict = []
    if checklist["has_title"]:
        verdict.append(f"title: '{home.get('title', '')}'")
    else:
        verdict.append("no title tag observed")
    verdict.append("meta description: "
                   + ("present" if checklist["has_meta_description"] else "not observed"))
    verdict.append("H1: " + (f"'{home.get('h1', '')}'" if checklist["has_h1"]
                             else "not observed"))
    verdict.append("CTA keywords: "
                   + ("observed" if checklist["has_cta_keywords"] else "not observed"))
    verdict.append("contact email: "
                   + ("observed" if checklist["has_email"] else "not observed"))
    verdict.append("phone: " + ("observed" if checklist["has_phone"] else "not observed"))
    socials = [k for k, v in checklist["social_links"].items() if v]
    verdict.append("social links: " + (", ".join(socials) if socials else "not observed"))
    verdict.append("https: " + ("yes" if checklist["is_https"] else "no"))
    unknowns = []
    if not checklist["has_viewport"]:
        unknowns.append("viewport meta not observed in fetched content")
    if not checklist["has_meta_description"]:
        unknowns.append("meta description not observed")
    return {"ok": True, "status": STATUS, "confidence": "MEDIUM",
            "url": url, "pages": fetched, "crawled": len(pages),
            "audit": {"checklist": checklist, "verdict": verdict,
                      "unknowns": unknowns},
            "query": ""}


def make_website_fetch_handler(transport=None):
    def _h(conn, *, project_id, root, args):
        return t_website_fetch(conn, project_id=project_id, root=root,
                               args=args, transport=transport)
    return _h


def make_website_crawl_handler(transport=None):
    def _h(conn, *, project_id, root, args):
        return t_website_crawl(conn, project_id=project_id, root=root,
                               args=args, transport=transport)
    return _h


def make_website_audit_handler(transport=None):
    def _h(conn, *, project_id, root, args):
        return t_website_marketing_audit(conn, project_id=project_id, root=root,
                                         args=args, transport=transport)
    return _h


def register_website_tools(registry, transport=None):
    from .registry import ToolDef
    registry.register(ToolDef(
        name="website_fetch",
        description=("Fetch one public web page and report observed title, "
                     "meta description, H1, visible text head, and links. "
                     "Evidence only; no invented metrics."),
        parameters={"type": "object", "required": ["url"],
                    "properties": {"url": {"type": "string"}}},
        side_effect="green", handler=make_website_fetch_handler(transport)))
    registry.register(ToolDef(
        name="website_crawl",
        description=("Crawl a small same-origin set of pages starting at a URL "
                     "(BFS, max 10 pages). Evidence only; no invented metrics."),
        parameters={"type": "object", "required": ["url"],
                    "properties": {"url": {"type": "string"},
                                   "max_pages": {"type": "integer"}}},
        side_effect="green", handler=make_website_crawl_handler(transport)))
    registry.register(ToolDef(
        name="website_marketing_audit",
        description=("Fetch a site's home page plus up to 3 same-origin pages "
                     "and report a deterministic marketing checklist observed "
                     "in the live content. Evidence only."),
        parameters={"type": "object", "required": ["url"],
                    "properties": {"url": {"type": "string"}}},
        side_effect="green", handler=make_website_audit_handler(transport)))
