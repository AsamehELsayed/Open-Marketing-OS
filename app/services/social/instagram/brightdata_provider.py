from __future__ import annotations

from datetime import datetime, timezone

import httpx

from . import capabilities as _caps
from . import registry_bridge
from .base import InstagramProvider, audit_result, normalize_account, normalize_post, unavailable


class BrightDataProvider(InstagramProvider):
    name = "brightdata"
    cost_type = "metered"
    needs = ()

    def _view(self, project_id: str | None = None):
        return _caps.resolve_capability(
            _caps.CAP_INSTAGRAM_PUBLIC_PROFILE, project_id)

    def _credential(self, project_id: str | None = None) -> str:
        view = self._view(project_id)
        if view is None or view.provider != self.name or not view.secret_ref:
            return ""
        return registry_bridge.vault_resolve(view.secret_ref) or ""

    def _dataset_id(self, project_id: str | None = None) -> str:
        view = self._view(project_id)
        if view is None or view.provider != self.name:
            return ""
        return str((view.config or {}).get("dataset_id") or "").strip()

    def _available(self, project_id: str | None = None) -> tuple[bool, str]:
        if not self._credential(project_id):
            return False, "Bright Data is not connected"
        if not self._dataset_id(project_id):
            return False, "Bright Data provider configuration is incomplete"
        return True, "configured"

    def available(self) -> tuple[bool, str]:
        return self._available(None)

    def test_connection(self, project_id: str | None = None) -> dict:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        token = self._credential(project_id)
        if not token:
            return {
                "credential_status": "not_configured",
                "provider_health": "unknown",
                "capability_health": "unknown",
                "detail": "Bright Data is not connected",
                "last_checked_at": now,
            }
        if not self._dataset_id(project_id):
            return {
                "credential_status": "connected",
                "provider_health": "unknown",
                "capability_health": "not_verified",
                "detail": "Bright Data provider configuration is incomplete",
                "last_checked_at": now,
            }
        try:
            response = httpx.get(
                "https://api.brightdata.com/datasets/v3/scrapers",
                params={"dataset_id": self._dataset_id(project_id)},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            response.raise_for_status()
        except Exception as exc:
            status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
            if status in (401, 403):
                credential_status = "auth_error"
                provider_health = "error"
                capability_health = "unknown"
                detail = "Bright Data rejected the stored credential"
            elif status in (400, 404):
                credential_status = "connected"
                provider_health = "error"
                capability_health = "configuration_error"
                detail = "Bright Data provider configuration was rejected"
            else:
                credential_status = "connected"
                provider_health = "error"
                capability_health = "unknown"
                detail = "Bright Data provider test failed"
            return {
                "credential_status": credential_status,
                "provider_health": provider_health,
                "capability_health": capability_health,
                "detail": detail,
                "last_checked_at": now,
            }
        return {
            "credential_status": "connected",
            "provider_health": "healthy",
            "capability_health": "verified",
            "detail": "Bright Data provider responded",
            "last_checked_at": now,
        }

    def audit_public(self, username: str, *, project_id: str | None = None) -> dict:
        ok, why = self._available(project_id)
        if not ok:
            return unavailable(self.name, why)
        clean_username = (username or "").strip().lstrip("@")
        if not clean_username:
            return unavailable(self.name, "username is required")
        token = self._credential(project_id)
        dataset_id = self._dataset_id(project_id)
        try:
            response = httpx.post(
                "https://api.brightdata.com/datasets/v3/trigger",
                params={"dataset_id": dataset_id, "include_errors": "true"},
                headers={"Authorization": f"Bearer {token}"},
                json=[{"url": f"https://www.instagram.com/{clean_username}/"}],
                timeout=180,
            )
            response.raise_for_status()
            snapshot = response.json()
            snapshot_id = snapshot.get("snapshot_id", "") if isinstance(snapshot, dict) else ""
            if not snapshot_id:
                return unavailable(self.name, "No provider snapshot was created")
            dataset_response = httpx.get(
                f"https://api.brightdata.com/datasets/v3/snapshot/{snapshot_id}",
                params={"format": "json"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=180,
            )
            dataset_response.raise_for_status()
            rows = dataset_response.json()
            rows = rows if isinstance(rows, list) else []
        except Exception:
            return unavailable(
                self.name,
                "Instagram profile data could not be retrieved from the configured provider.",
            )
        if not rows:
            return unavailable(
                self.name, f"No profile data was returned for @{clean_username}")
        profile = rows[0] or {}
        account = normalize_account(self.name, {
            "username": profile.get("username", clean_username),
            "display_name": profile.get("display_name") or profile.get("full_name") or profile.get("name") or "",
            "bio": profile.get("biography") or profile.get("bio") or "",
            "followers": profile.get("followers") or profile.get("follower_count"),
            "following": profile.get("following") or profile.get("following_count"),
            "media_count": profile.get("posts_count") or profile.get("media_count"),
            "verified": profile.get("is_verified") or profile.get("verified", False),
            "website": profile.get("external_url") or profile.get("website") or "",
        })
        recent = profile.get("recent_posts") or profile.get("posts") or []
        posts = [normalize_post(self.name, {
            "id": item.get("id") or item.get("shortcode", ""),
            "url": item.get("url") or item.get("post_url", ""),
            "type": item.get("type") or item.get("media_type", ""),
            "caption": item.get("caption") or item.get("text", ""),
            "published_at": item.get("date_posted") or item.get("timestamp", ""),
            "likes": item.get("likes") or item.get("likes_count"),
            "comments": item.get("comments") or item.get("comments_count"),
            "views": item.get("views") or item.get("video_views"),
            "media_urls": item.get("photos") or item.get("images") or [],
        }) for item in recent[:20]]
        return audit_result(
            self.name,
            "verified" if posts else "partial",
            account=account,
            posts=posts,
            unknowns=["Stories and highlights were not retrieved"],
        )

    def audit_owned(self, username: str, *, project_id: str | None = None) -> dict:
        return self.audit_public(username, project_id=project_id)
