"""Website-link social discovery + VERIFIED/LIKELY policy (Contract C4, DEV-003).

Evidence rule: only ``<a href>`` anchors found in the project's own fetched
website HTML count as website-link evidence (-> VERIFIED, source="website").
Social-platform pages are never fetched for self-verification, and a bare
username with no website anchor is never auto-VERIFIED (-> LIKELY).

No DB access here (pure dicts, no SocialAccounts import); merge helper works
against W1 row shapes without importing them.
"""
from html.parser import HTMLParser
from urllib.parse import urljoin

try:  # package-relative first; absolute fallback for direct module loading
    from .platforms import PLATFORMS, canonical_url, platform_from_url
except ImportError:  # pragma: no cover
    from app.services.social.platforms import (
        PLATFORMS,
        canonical_url,
        platform_from_url,
    )

_FETCH_TIMEOUT_SECONDS = 20


class _AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.hrefs.append(value.strip())


def _extract_hrefs(html: str) -> list:
    parser = _AnchorParser()
    try:
        parser.feed(html or "")
    except Exception:
        return list(parser.hrefs)
    return list(parser.hrefs)


def _fetch_html(website: str, fetcher=None) -> str:
    """Return page HTML for *website*.

    *fetcher* is an offline-injectable callable ``fetcher(website)`` used by
    tests; it may return a raw HTML string or an object with a ``.text``
    attribute. When ``fetcher`` is None, httpx is used (<=20s, redirects
    followed); any failure yields "" (discovery degrades to no candidates).
    """
    if fetcher is not None:
        try:
            result = fetcher(website)
        except Exception:
            return ""
        if result is None:
            return ""
        if isinstance(result, str):
            return result
        text = getattr(result, "text", "")
        return text if isinstance(text, str) else ""
    try:
        import httpx
    except ImportError:
        return ""
    try:
        with httpx.Client(timeout=_FETCH_TIMEOUT_SECONDS, follow_redirects=True) as client:
            response = client.get(
                website,
                headers={"User-Agent": "open-marketing-os-discovery/1.0"},
            )
            if response.status_code >= 400:
                return ""
            return response.text or ""
    except Exception:
        return ""


def classify_status(website_linked: bool = False, **kwargs) -> str:
    """VERIFIED iff the handle is linked from the project's own website."""
    if "website_linked" in kwargs:
        website_linked = kwargs["website_linked"]
    return "VERIFIED" if website_linked else "LIKELY"


def discover_from_website(website: str, fetcher=None) -> list:
    """Extract platform handles from a website's outbound anchors.

    Per-platform first-match wins (document order). Every candidate is
    website-link evidence: status=VERIFIED, source="website",
    evidence_url=website.
    """
    if not website or not isinstance(website, str):
        return []
    html = _fetch_html(website, fetcher)
    if not html:
        return []
    seen = set()
    candidates = []
    for href in _extract_hrefs(html):
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(website, href)
        if not absolute.lower().startswith(("http://", "https://")):
            continue
        platform, handle = platform_from_url(absolute)
        if platform is None or not handle:
            continue
        if platform in seen:
            continue
        seen.add(platform)
        try:
            url = canonical_url(platform, handle)
        except ValueError:
            continue
        candidates.append(
            {
                "platform": platform,
                "handle": handle,
                "url": url,
                "status": classify_status(website_linked=True),
                "source": "website",
                "evidence_url": website,
            }
        )
    return candidates


def discover_project_socials(project_id: str, website: str, existing,
                             *, fetcher=None, discovered=None) -> list:
    """Pure-merge website discovery over existing SocialAccounts-style rows.

    - Never deletes rows; VERIFIED rows are returned byte-identical in content
      (project_id ensured) even if discovery disagrees on the handle.
    - LIKELY/UNVERIFIED rows are upgraded to the website-linked handle
      (status=VERIFIED, source="website") only on website-link evidence.
    - Platforms found on the website but absent from *existing* are appended
      as new VERIFIED/source="website" rows.
    - *discovered* (a precomputed discover_from_website list) is accepted so
      callers/tests can merge without network; otherwise discovery runs with
      the optional *fetcher*.
    """
    rows = [dict(r) for r in (existing or [])]
    by_platform = {}
    for row in rows:
        key = row.get("platform")
        if key and key not in by_platform:
            by_platform[key] = row
    if discovered is None:
        discovered = discover_from_website(website, fetcher)
    found = {}
    for cand in discovered or []:
        key = (cand or {}).get("platform")
        if key and key not in found:
            found[key] = dict(cand)
    merged = []
    for row in rows:
        key = row.get("platform")
        row = dict(row)
        if project_id:
            row["project_id"] = project_id
        cand = found.get(key)
        if cand and row.get("status") != "VERIFIED":
            row["handle"] = cand["handle"]
            row["url"] = cand["url"]
            row["status"] = "VERIFIED"
            row["source"] = "website"
            row["evidence_url"] = website
        merged.append(row)
    for key in found:
        if key not in by_platform:
            if key not in PLATFORMS:
                continue
            cand = found[key]
            merged.append(
                {
                    "project_id": project_id,
                    "platform": cand["platform"],
                    "handle": cand["handle"],
                    "url": cand["url"],
                    "status": "VERIFIED",
                    "source": "website",
                    "evidence_url": website,
                }
            )
    return merged
