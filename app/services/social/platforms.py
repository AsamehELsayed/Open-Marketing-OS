"""Six-platform registry: detection, handle normalization, canonical URLs.

Contract C3 (DEV-003): PLATFORMS, platform_from_url, normalize_handle,
canonical_url. No DB/network access; no client-specific literals.
"""
from urllib.parse import urlparse

PLATFORMS = ("instagram", "tiktok", "x", "facebook", "linkedin", "youtube")

_HANDLE_TRUNCATE = 80

# Host suffixes (after stripping a leading "www.") per platform.
_HOSTS = {
    "instagram": ("instagram.com",),
    "tiktok": ("tiktok.com",),
    "x": ("x.com", "twitter.com"),
    "facebook": ("facebook.com", "fb.com"),
    "linkedin": ("linkedin.com",),
    "youtube": ("youtube.com", "youtu.be"),
}

# First path segments that are site chrome, not handles.
_RESERVED = {
    "instagram": {
        "p", "reel", "reels", "tv", "explore", "accounts", "stories",
        "direct", "about", "developer", "legal", "emojis",
    },
    "tiktok": {"tag", "discover", "music", "trending", "foryou", "live", "search"},
    "x": {
        "home", "explore", "notifications", "messages", "search", "settings",
        "i", "intent", "share", "hashtag",
    },
    "facebook": {
        "sharer", "dialog", "plugins", "tr", "ads", "business", "help",
        "legal", "policies", "pages",
    },
    "linkedin": set(),
    "youtube": set(),
}


def _host_platform(host: str):
    host = (host or "").lower().strip().lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    for platform, suffixes in _HOSTS.items():
        for suffix in suffixes:
            if host == suffix or host.endswith("." + suffix):
                return platform
    return None


def _segments(path: str) -> list:
    return [s for s in (path or "").split("/") if s]


def _handle_from_path(platform: str, path: str, host: str):
    """Extract a raw handle candidate from a platform URL path."""
    segs = _segments(path)
    if not segs:
        return None
    if platform == "linkedin":
        # /company/<h>, /in/<h>, /school/<h>, /showcase/<h>
        if segs[0].lower() in ("company", "in", "school", "showcase"):
            return segs[1] if len(segs) > 1 else None
        return segs[0]
    if platform == "youtube":
        first = segs[0]
        if first.startswith("@"):
            return first
        if first.lower() in ("c", "user", "channel"):
            return segs[1] if len(segs) > 1 else None
        if host == "youtu.be":
            return None  # video id, not a handle
        return first
    first = segs[0]
    if first.lower() in _RESERVED.get(platform, set()):
        return None
    return first


def platform_from_url(url: str):
    """Map a URL to (platform, handle|None).

    Returns (None, None) for non-platform URLs; (platform, None) when the
    domain matches but no handle segment is extractable.
    """
    if not url or not isinstance(url, str):
        return (None, None)
    text = url.strip()
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlparse(text)
    except (ValueError, AttributeError):
        return (None, None)
    if parts.scheme not in ("http", "https"):
        return (None, None)
    platform = _host_platform(parts.hostname or "")
    if platform is None:
        return (None, None)
    raw = _handle_from_path(platform, parts.path or "", (parts.hostname or "").lower())
    if raw is None:
        return (platform, None)
    handle = normalize_handle(platform, raw)
    if not handle:
        return (platform, None)
    return (platform, handle)


def normalize_handle(platform: str, raw) -> str:
    """Normalize a handle: strip @/URL wrapping, whitespace, truncate to 80."""
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    if "://" in text or ("/" in text and "." in text.split("/")[0]):
        platform_guess, handle = platform_from_url(text)
        if handle and (platform_guess == platform or not platform):
            return handle[:_HANDLE_TRUNCATE]
        # Fall through to segment-based cleanup for odd inputs.
    # Take the last non-empty path segment, drop query/fragment/params.
    segment = [s for s in text.split("/") if s]
    text = segment[-1] if segment else text
    for sep in ("?", "#", "&"):
        text = text.split(sep)[0]
    text = text.strip().split()[0] if text.strip().split() else ""
    if text.startswith("@"):
        text = text[1:]
    return text.strip()[:_HANDLE_TRUNCATE]


def canonical_url(platform: str, handle: str) -> str:
    """Canonical public URL for a (platform, handle) pair."""
    if platform not in PLATFORMS:
        raise ValueError(f"unknown platform: {platform!r}")
    clean = normalize_handle(platform, handle)
    if not clean:
        return ""
    if platform == "instagram":
        return f"https://www.instagram.com/{clean}/"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{clean}"
    if platform == "x":
        return f"https://x.com/{clean}"
    if platform == "facebook":
        return f"https://www.facebook.com/{clean}"
    if platform == "linkedin":
        return f"https://www.linkedin.com/company/{clean}"
    if platform == "youtube":
        return f"https://www.youtube.com/@{clean}"
    raise ValueError(f"unknown platform: {platform!r}")
