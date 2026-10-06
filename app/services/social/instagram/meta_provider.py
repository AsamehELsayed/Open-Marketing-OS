"""Meta Instagram provider (owned/professional accounts via Graph API).
Capability: instagram_owned_insights (default provider meta, auth WORKSPACE
OAUTH at project/workspace scope).

Credential path: vault first (workspace-OAuth token stored under a
project-scoped vault:// ref resolved through the integration registry);
META_IG_ACCESS_TOKEN env is a bootstrap fallback ONLY when the vault is
empty — documented, never logged, never echoed in status payloads.
Account id (ig_account_id) is non-secret config: integration record config
first, META_IG_ACCOUNT_ID env fallback. Vendor endpoint/payload details stay
hidden inside this provider. Presence-only status; secrets stay in the vault.
"""
import httpx

from . import capabilities as _caps
from . import registry_bridge
from .base import audit_result, normalize_account, normalize_post, unavailable


class MetaProvider:
    name = "meta"
    needs = ()

    def _view(self, project_id: str | None = None):
        try:
            return _caps.resolve_capability(
                _caps.CAP_INSTAGRAM_OWNED_INSIGHTS, project_id)
        except Exception:
            return None

    def _credential(self, project_id: str | None = None) -> str:
        """Workspace-OAuth vault token first; env bootstrap fallback only when
        the vault is empty. Never logged; never placed in status output."""
        view = self._view(project_id)
        if view is not None and view.secret_ref:
            try:
                tok = registry_bridge.vault_resolve(view.secret_ref)
                if tok:
                    return tok
            except Exception:
                pass
        return ""

    def _account_id(self, project_id: str | None = None) -> str:
        """Non-secret account metadata: integration config first, env fallback."""
        view = self._view(project_id)
        if view is not None:
            got = (view.config or {}).get("ig_account_id")
            if got:
                return str(got)
        return ""

    def _available(self, project_id: str | None = None) -> tuple[bool, str]:
        if not self._credential(project_id):
            return False, "not configured (missing workspace credential)"
        if not self._account_id(project_id):
            return False, "not configured (missing instagram account id)"
        return True, "configured"

    def available(self):
        return self._available(None)

    def _get(self, path: str, params: dict, timeout: int = 20,
             project_id: str | None = None) -> dict:
        from app.services.config_service import ConfigService
        version = str(ConfigService.get_setting("meta_api_version", "v21.0") or "v21.0")
        url = f"https://graph.facebook.com/{version}/{path}"
        params = dict(params or {})
        params["access_token"] = self._credential(project_id)
        r = httpx.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError("Meta returned a provider error")
        return data

    def audit_owned(self, username: str = "", *,
                    project_id: str | None = None) -> dict:
        ok, why = self._available(project_id)
        if not ok:
            return unavailable(self.name, why)
        igid = self._account_id(project_id)
        unknowns = []
        try:
            prof = self._get(igid, {"fields": "username,name,biography,followers_count,follows_count,media_count,website,profile_picture_url"},
                             project_id=project_id)
        except Exception:
            return unavailable(
                self.name,
                "Instagram profile data could not be retrieved from the connected account.",
            )
        account = normalize_account(self.name, {
            "username": prof.get("username", username),
            "display_name": prof.get("name", ""),
            "bio": prof.get("biography", ""),
            "followers": prof.get("followers_count"),
            "following": prof.get("follows_count"),
            "media_count": prof.get("media_count"),
            "website": prof.get("website", ""),
            "account_type": "professional",
        })
        posts = []
        try:
            media = self._get(f"{igid}/media", {"fields": "id,caption,media_type,media_url,thumbnail_url,timestamp,like_count,comments_count", "limit": 25},
                              project_id=project_id)
            for m in (media.get("data") or [])[:25]:
                posts.append(normalize_post(self.name, {
                    "id": m.get("id", ""), "type": m.get("media_type", ""),
                    "caption": m.get("caption", ""),
                    "published_at": m.get("timestamp", ""),
                    "likes": m.get("like_count"), "comments": m.get("comments_count"),
                    "media_url": m.get("media_url") or m.get("thumbnail_url", ""),
                    "url": f"https://www.instagram.com/p/{m.get('id', '')}",
                }))
        except Exception:
            unknowns.append("Recent media were unavailable from the connected account")
        insights = {}
        try:
            ins = self._get(f"{igid}/insights", {"metric": "reach,profile_views", "period": "day"},
                            project_id=project_id)
            insights = {"raw_period": "day",
                        "series": [(d.get("name"), d.get("values")) for d in (ins.get("data") or [])]}
        except Exception:
            unknowns.append("account insights require additional permissions")
        unknowns.append("stories/highlights are not exposed by the Graph API here")
        return audit_result(self.name, "verified" if posts else "partial",
                            account=account, posts=posts, insights=insights, unknowns=unknowns)

    def audit_public(self, username: str, *, project_id: str | None = None) -> dict:
        return unavailable(self.name, "meta provider covers connected owned accounts only")
