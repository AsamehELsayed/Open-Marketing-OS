"""Instagram provider abstraction. Every provider normalizes to InstagramAuditData.

Common contract (all fields optional unless noted; never fabricate):
  account: username*, display_name, bio, followers, following, media_count,
           verified, website, account_type, collected_at, source
  posts: [{id, url, type, caption, published_at, likes, comments, views, media_urls}]
  insights: provider-specific normalized metrics (owned accounts only)
  evidence: {source, status, collected_at}
  unknowns: [explicit gaps — never guessed]
(* username required when a profile is returned.)
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

# Structured Integration Errors (DEV-007R-HOTFIX §11)
AUTH_ERROR = "AUTH_ERROR"
PROVIDER_NOT_FOUND = "PROVIDER_NOT_FOUND"
CAPABILITY_NOT_FOUND = "CAPABILITY_NOT_FOUND"
INVALID_INPUT = "INVALID_INPUT"
RATE_LIMITED = "RATE_LIMITED"
PAYMENT_REQUIRED = "PAYMENT_REQUIRED"
PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
TIMEOUT = "TIMEOUT"
CONFIG_ERROR = "CONFIG_ERROR"
UNKNOWN = "UNKNOWN"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _num(v) -> int | None:
    try:
        n = int(v)
        return n if n >= 0 else None
    except (TypeError, ValueError):
        return None


def normalize_handle(raw: str) -> str:
    """Normalize input handle into a canonical Instagram username slug.

    Handles:
      @example_handle -> example_handle
      example_handle -> example_handle
      https://instagram.com/example_handle/ -> example_handle
      https://www.instagram.com/example_handle?igsh=123 -> example_handle
      instagram.com/example_handle -> example_handle
    """
    s = (raw or "").strip()
    if not s:
        return ""
    # Strip URLs
    if "instagram.com" in s.lower():
        # prepend scheme if missing so urlparse works
        if not s.startswith("http://") and not s.startswith("https://"):
            s = f"https://{s}"
        try:
            parsed = urlparse(s)
            path_parts = [p for p in parsed.path.split("/") if p]
            # Handle /p/postId or /reel/reelId vs /username
            if path_parts:
                candidate = path_parts[0]
                if candidate in ("p", "reel", "stories", "tv") and len(path_parts) > 1:
                    candidate = path_parts[1]
                s = candidate
        except Exception:
            pass
    # Strip leading @ and any trailing slashes or whitespace
    s = re.sub(r"^@+", "", s.strip())
    s = s.rstrip("/")
    # Clean any query parameter or fragment leftover
    if "?" in s:
        s = s.split("?")[0]
    if "#" in s:
        s = s.split("#")[0]
    return s.strip().lower()


@dataclass(frozen=True)
class InstagramPublicProfile:
    """Neutral schema for public Instagram profile data (DEV-007R-HOTFIX §7).

    Fields unsupported by a provider remain None (never fabricated).
    """
    handle: str
    display_name: str | None = None
    bio: str | None = None
    followers: int | None = None
    following: int | None = None
    posts_count: int | None = None
    verified: bool | None = None
    business_category: str | None = None
    external_url: str | None = None
    recent_posts: list[dict[str, Any]] | None = None
    source_provider: str = "apify"
    cost_type: str = "metered"
    fetched_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if not d["fetched_at"]:
            d["fetched_at"] = _now()
        return d


def normalize_account(source: str, raw: dict) -> dict:
    uname = normalize_handle(raw.get("username") or raw.get("handle") or "")
    return {
        "username": uname,
        "display_name": str(raw.get("display_name") or raw.get("full_name") or raw.get("fullName") or raw.get("name") or ""),
        "bio": str(raw.get("bio") or raw.get("biography") or ""),
        "followers": _num(raw.get("followers", raw.get("followers_count", raw.get("followersCount", raw.get("edge_followed_by"))))),
        "following": _num(raw.get("following", raw.get("follows_count", raw.get("followsCount", raw.get("edge_follow"))))),
        "media_count": _num(raw.get("media_count", raw.get("posts_count", raw.get("postsCount", raw.get("edge_media_count"))))),
        "verified": bool(raw.get("verified", raw.get("is_verified", raw.get("isVerified", False)))),
        "website": str(raw.get("website") or raw.get("external_url") or raw.get("externalUrl") or ""),
        "account_type": str(raw.get("account_type") or raw.get("business_category") or raw.get("businessCategoryName") or ""),
        "collected_at": _now(),
        "source": source,
    }


def normalize_post(source: str, raw: dict) -> dict:
    media = raw.get("media_urls") or raw.get("media") or []
    if isinstance(media, str):
        media = [media]
    single = raw.get("media_url") or raw.get("display_url") or raw.get("displayUrl") or raw.get("thumbnail_url")
    if single and single not in media:
        media = [single] + list(media)
    return {
        "id": str(raw.get("id") or raw.get("shortcode") or raw.get("shortCode") or ""),
        "url": str(raw.get("url") or raw.get("post_url") or ""),
        "type": str(raw.get("type") or raw.get("media_type") or raw.get("__typename") or ""),
        "caption": str(raw.get("caption") or raw.get("text") or "")[:2000],
        "published_at": str(raw.get("published_at") or raw.get("timestamp") or raw.get("taken_at") or ""),
        "likes": _num(raw.get("likes", raw.get("like_count", raw.get("likesCount", raw.get("edge_liked_by"))))),
        "comments": _num(raw.get("comments", raw.get("comment_count", raw.get("commentsCount", raw.get("edge_comment_count"))))),
        "views": _num(raw.get("views", raw.get("view_count", raw.get("videoViewCount", raw.get("video_view_count"))))),
        "media_urls": [str(u) for u in media if u][:10],
        "source": source,
    }


def audit_result(source: str, status: str, account=None, posts=None,
                 insights=None, unknowns=None, profile=None) -> dict:
    res = {
        "account": account,
        "posts": posts or [],
        "insights": insights or {},
        "evidence": {"source": source, "status": status, "collected_at": _now()},
        "unknowns": list(unknowns or []),
    }
    if profile is not None:
        res["profile"] = profile if isinstance(profile, dict) else profile.to_dict()
    return res


def unavailable(source: str, reason: str, error_code: str | None = None) -> dict:
    res = audit_result(source, "unavailable", unknowns=[reason])
    if error_code:
        res["error_code"] = error_code
    return res


class InstagramProvider:
    """Interface. configure() reads env/settings; available() is pure check."""
    name = "base"
    cost_type = "free"
    needs = ()  # env var names required

    def available(self) -> tuple[bool, str]:
        return False, "provider availability has not been verified"

    def audit_owned(self, username: str, *, project_id: str | None = None) -> dict:
        raise NotImplementedError

    def audit_public(self, username: str, *, project_id: str | None = None) -> dict:
        raise NotImplementedError
