"""Apify public Instagram provider (first external scraper). Capability:
instagram_public_profile (default provider, credential scope INSTALLATION).

Credential path: vault first (installation-scoped secret_ref resolved through
the integration registry, e.g. vault://installation/...); APIFY_API_TOKEN env
is a bootstrap fallback ONLY when the vault is empty — documented, never
logged, never echoed in status payloads.

Actor IDs are stored separately from API routes and normalized with `~`
(e.g. `apify~instagram-profile-scraper`). URL construction is centralized in
`ApifyClient`. Bearer authentication is enforced. Secrets are redacted.
"""
from __future__ import annotations

import os
from typing import Any

from . import capabilities as _caps
from . import registry_bridge
from .apify_client import (
    DEFAULT_POST_ACTOR,
    DEFAULT_PROFILE_ACTOR,
    ApifyClient,
    canonical_actor_id,
)
from .base import (
    AUTH_ERROR,
    CONFIG_ERROR,
    InstagramProvider,
    InstagramPublicProfile,
    _now,
    _num,
    audit_result,
    normalize_account,
    normalize_handle,
    normalize_post,
    unavailable,
)

PROFILE_ACTOR = DEFAULT_PROFILE_ACTOR
POST_ACTOR = DEFAULT_POST_ACTOR


class ApifyProvider(InstagramProvider):
    name = "apify"
    cost_type = "metered"
    needs = ()

    def __init__(self, client: ApifyClient | None = None):
        self.client = client or ApifyClient()

    def _credential(self, project_id: str | None = None) -> str:
        try:
            view = _caps.resolve_capability(
                _caps.CAP_INSTAGRAM_PUBLIC_PROFILE, project_id)
            if view is not None and view.secret_ref:
                token = registry_bridge.vault_resolve(view.secret_ref)
                if token:
                    return token
        except Exception:
            pass
        return ""

    def _configured_actor_id(self, project_id: str | None = None) -> str:
        """Actor ID from capability view config -> env override -> default."""
        try:
            view = _caps.resolve_capability(
                _caps.CAP_INSTAGRAM_PUBLIC_PROFILE, project_id)
            if view is not None and isinstance(getattr(view, "config", None), dict):
                act = view.config.get("actor_id")
                if act:
                    return canonical_actor_id(str(act))
        except Exception:
            pass
        env_actor = os.getenv("APIFY_IG_PROFILE_ACTOR", "")
        if env_actor:
            return canonical_actor_id(env_actor)
        return DEFAULT_PROFILE_ACTOR

    def _available(self, project_id: str | None = None) -> tuple[bool, str]:
        if not self._credential(project_id):
            return False, "Instagram Public Research is not connected"
        return True, "configured"

    def available(self) -> tuple[bool, str]:
        return self._available(None)

    def test_connection(self, project_id: str | None = None) -> dict[str, Any]:
        """Bounded capability test using centralized ApifyClient."""
        token = self._credential(project_id)
        actor_id = self._configured_actor_id(project_id)
        return self.client.test_connection(token, actor_id=actor_id)

    def audit_public(self, username: str, *, project_id: str | None = None) -> dict:
        ok, why = self._available(project_id)
        if not ok:
            return unavailable(self.name, why, error_code=AUTH_ERROR)

        canonical_username = normalize_handle(username)
        if not canonical_username:
            return unavailable(self.name, "username is required")

        token = self._credential(project_id)
        prof_actor = self._configured_actor_id(project_id)
        unknowns = []

        try:
            # Map capability instagram_public_profile(handle) to Actor's current expected input:
            # {"usernames": [canonical_username]}
            rows = self.client.run_actor_sync(
                prof_actor,
                {"usernames": [canonical_username]},
                token=token,
                timeout=120,
            )
        except Exception as exc:
            err_code = self.client.classify_error(exc)
            return unavailable(
                self.name,
                "Instagram profile data could not be retrieved from the configured provider.",
                error_code=err_code,
            )

        if not rows:
            return unavailable(self.name, f"no profile data returned for @{canonical_username}",
                               error_code="NO_DATA")

        p = rows[0] or {}
        raw_uname = str(p.get("username") or canonical_username)
        resolved_uname = normalize_handle(raw_uname) or canonical_username

        account = normalize_account(self.name, {
            "username": resolved_uname,
            "display_name": p.get("fullName", p.get("full_name", "")),
            "bio": p.get("biography", ""),
            "followers": p.get("followersCount", p.get("followers")),
            "following": p.get("followsCount", p.get("following")),
            "media_count": p.get("postsCount", p.get("media_count")),
            "verified": p.get("verified", p.get("isVerified", False)),
            "website": p.get("externalUrl", p.get("website", "")),
            "business_category": p.get("businessCategoryName", p.get("business_category", "")),
        })

        posts: list[dict[str, Any]] = []
        post_actor = canonical_actor_id(
            os.getenv("APIFY_IG_POST_ACTOR", "") or POST_ACTOR)

        try:
            items = self.client.run_actor_sync(
                post_actor,
                {"username": [resolved_uname], "resultsLimit": 20},
                token=token,
                timeout=180,
            )
            for m in (items or [])[:20]:
                posts.append(normalize_post(self.name, {
                    "id": m.get("id", m.get("shortCode", m.get("shortcode", ""))),
                    "url": m.get("url", ""),
                    "type": m.get("type", ""),
                    "caption": m.get("caption", m.get("text", "")),
                    "published_at": m.get("timestamp", ""),
                    "likes": m.get("likesCount", m.get("likes")),
                    "comments": m.get("commentsCount", m.get("comments")),
                    "views": m.get("videoViewCount", m.get("views")),
                    "media_urls": [m.get("displayUrl", m.get("imageUrl", ""))],
                }))
        except Exception:
            unknowns.append("Recent posts were unavailable from the configured provider.")

        unknowns.append("stories/highlights are explicitly unknown (not retrieved)")

        # Neutral schema InstagramPublicProfile (DEV-007R-HOTFIX §7)
        profile = InstagramPublicProfile(
            handle=resolved_uname,
            display_name=account.get("display_name") or None,
            bio=account.get("bio") or None,
            followers=account.get("followers"),
            following=account.get("following"),
            posts_count=account.get("media_count"),
            verified=account.get("verified"),
            business_category=p.get("businessCategoryName") or p.get("business_category") or None,
            external_url=account.get("website") or None,
            recent_posts=posts,
            source_provider=self.name,
            fetched_at=_now(),
        )

        res = audit_result(self.name, "verified" if posts else "partial",
                           account=account, posts=posts, unknowns=unknowns,
                           profile=profile)
        res["cost_type"] = self.cost_type
        return res

    def audit_owned(self, username: str, *, project_id: str | None = None) -> dict:
        # A connected account is also publicly visible; same path applies.
        return self.audit_public(username, project_id=project_id)
