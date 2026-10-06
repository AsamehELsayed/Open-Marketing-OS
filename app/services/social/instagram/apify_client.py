"""Centralized Apify client (DEV-007R-HOTFIX).

Rules:
- Actor identifier is stored and handled separately from API route paths.
- Canonical actor ID format uses `<owner>~<name>` (e.g. `apify~instagram-profile-scraper`)
  or an alphanumeric Actor ID. Any `owner/name` format is automatically normalized to `owner~name`.
- Centralized URL construction: no feature-specific code concatenates Apify URLs with slashes.
- Auth uses `Authorization: Bearer <token>` in headers. Query parameter token auth is banned.
- Strict secret redaction: tokens NEVER leak in logs, error messages, telemetry, or exceptions.
- Real connection test separates Credential validation (Step A: GET /v2/users/me) from
  Actor validation (Step B: GET /v2/actors/{actor_id}).
- Error classification into structured integration errors.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import httpx

from .base import (
    AUTH_ERROR,
    CONFIG_ERROR,
    INVALID_INPUT,
    PAYMENT_REQUIRED,
    PROVIDER_NOT_FOUND,
    PROVIDER_UNAVAILABLE,
    RATE_LIMITED,
    TIMEOUT,
    UNKNOWN,
)

APIFY_BASE_URL = "https://api.apify.com/v2"
DEFAULT_PROFILE_ACTOR = "apify~instagram-profile-scraper"
DEFAULT_POST_ACTOR = "apify~instagram-post-scraper"

_TOKEN_SCRUB_RE = re.compile(r"apify_api_[A-Za-z0-9_-]{10,}", re.IGNORECASE)
_BEARER_SCRUB_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9_.~-]{6,}", re.IGNORECASE)
_QUERY_TOKEN_SCRUB_RE = re.compile(r"((?:token|access_token|key|api_key)=)[^&\s]+", re.IGNORECASE)


def sanitize_secrets(text: str) -> str:
    """Scrub any Apify token or sensitive credential from strings, URLs, or errors."""
    if not text:
        return ""
    s = _TOKEN_SCRUB_RE.sub("[REDACTED_APIFY_TOKEN]", str(text))
    s = _BEARER_SCRUB_RE.sub(r"\1[REDACTED]", s)
    s = _QUERY_TOKEN_SCRUB_RE.sub(r"\1[REDACTED]", s)
    return s


def canonical_actor_id(actor_id: str) -> str:
    """Normalize actor ID to Apify API expected format: owner~name or actor_id.

    Replaces any slash with tilde (e.g. 'apify/instagram-profile-scraper' ->
    'apify~instagram-profile-scraper').
    """
    raw = (actor_id or "").strip()
    if not raw:
        return DEFAULT_PROFILE_ACTOR
    # If a full URL was accidentally passed, extract the actor name
    if "api.apify.com" in raw or "apify.com" in raw:
        parts = [p for p in raw.split("/") if p]
        if parts:
            raw = parts[-1]
            if len(parts) >= 2 and parts[-2] in ("acts", "actors"):
                raw = parts[-1]
            elif len(parts) >= 2 and parts[-2] not in ("v2", "api.apify.com"):
                raw = f"{parts[-2]}~{parts[-1]}"
    raw = raw.strip().strip("/")
    if "/" in raw:
        owner, name = raw.split("/", 1)
        raw = f"{owner.strip()}~{name.strip()}"
    return raw.strip().strip("/")


class ApifyClient:
    """Central client for Apify API interactions."""

    def __init__(self, base_url: str = APIFY_BASE_URL):
        self.base_url = base_url.rstrip("/")

    def _headers(self, token: str) -> dict[str, str]:
        t = (token or "").strip()
        headers = {"Content-Type": "application/json"}
        if t:
            headers["Authorization"] = f"Bearer {t}"
        return headers

    def actor_url(self, actor_id: str) -> str:
        """Construct the actor resource endpoint using canonical actor ID."""
        aid = canonical_actor_id(actor_id)
        return f"{self.base_url}/actors/{aid}"

    def actor_run_sync_url(self, actor_id: str) -> str:
        """Construct the sync-run-and-get-dataset-items endpoint."""
        aid = canonical_actor_id(actor_id)
        return f"{self.base_url}/actors/{aid}/run-sync-get-dataset-items"

    def classify_error(self, exc: Exception | None, status_code: int | None = None) -> str:
        if status_code in (401, 403):
            return AUTH_ERROR
        if status_code == 404:
            return PROVIDER_NOT_FOUND
        if status_code == 429:
            return RATE_LIMITED
        if status_code == 402:
            return PAYMENT_REQUIRED
        if status_code and 500 <= status_code < 600:
            return PROVIDER_UNAVAILABLE
        if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
            return TIMEOUT
        if isinstance(exc, (ValueError, TypeError)):
            return INVALID_INPUT
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code if exc.response is not None else 0
            return self.classify_error(None, status_code=code)
        if exc is not None:
            msg = str(exc)
            if "404" in msg or PROVIDER_NOT_FOUND in msg or "not found" in msg.lower():
                return PROVIDER_NOT_FOUND
            if "401" in msg or "403" in msg or AUTH_ERROR in msg or "unauthorized" in msg.lower():
                return AUTH_ERROR
            if "429" in msg or RATE_LIMITED in msg:
                return RATE_LIMITED
            if "402" in msg or PAYMENT_REQUIRED in msg:
                return PAYMENT_REQUIRED
            if TIMEOUT in msg or "timed out" in msg.lower():
                return TIMEOUT
            if CONFIG_ERROR in msg:
                return CONFIG_ERROR
        return UNKNOWN

    def test_connection(self, token: str, actor_id: str = DEFAULT_PROFILE_ACTOR,
                        timeout: int = 15) -> dict[str, Any]:
        """Real bounded provider capability test.

        Step A: Validate credential against Apify (GET /v2/users/me with Bearer auth).
        Step B: Validate configured Actor exists/is accessible (GET /v2/actors/{actor_id}).

        Returns structured health without executing any paid scrapers.
        """
        now_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        clean_actor = canonical_actor_id(actor_id)
        tok = (token or "").strip()

        if not tok:
            return {
                "credential_status": "not_configured",
                "provider_health": "unknown",
                "capability_health": "not_configured",
                "last_checked_at": now_ts,
                "last_error_code": AUTH_ERROR,
                "actor_id": clean_actor,
                "detail": "No Apify API token configured.",
            }

        # Step A: Validate credential
        user_url = f"{self.base_url}/users/me"
        try:
            r_user = httpx.get(user_url, headers=self._headers(tok), timeout=timeout)
        except Exception as exc:
            return {
                "credential_status": "error",
                "provider_health": "unreachable",
                "capability_health": "error",
                "last_checked_at": now_ts,
                "last_error_code": self.classify_error(exc),
                "actor_id": clean_actor,
                "detail": "Apify could not be reached",
            }

        if r_user.status_code in (401, 403):
            return {
                "credential_status": "auth_error",
                "provider_health": "error",
                "capability_health": "error",
                "last_checked_at": now_ts,
                "last_error_code": AUTH_ERROR,
                "actor_id": clean_actor,
                "detail": "Apify token authentication failed (invalid or expired token).",
            }
        elif r_user.status_code >= 400:
            return {
                "credential_status": "error",
                "provider_health": "degraded",
                "capability_health": "error",
                "last_checked_at": now_ts,
                "last_error_code": self.classify_error(None, r_user.status_code),
                "actor_id": clean_actor,
                "detail": f"Apify users/me returned status {r_user.status_code}.",
            }

        # Step B: Validate configured Actor exists and is accessible
        act_url = self.actor_url(clean_actor)
        try:
            r_act = httpx.get(act_url, headers=self._headers(tok), timeout=timeout)
        except Exception as exc:
            return {
                "credential_status": "connected",
                "provider_health": "unreachable",
                "capability_health": "error",
                "last_checked_at": now_ts,
                "last_error_code": self.classify_error(exc),
                "actor_id": clean_actor,
                "detail": "The configured Apify actor could not be checked",
            }

        if r_act.status_code in (401, 403):
            return {
                "credential_status": "connected",
                "provider_health": "error",
                "capability_health": "error",
                "last_checked_at": now_ts,
                "last_error_code": AUTH_ERROR,
                "actor_id": clean_actor,
                "detail": "The configured Apify actor rejected authentication",
            }
        if r_act.status_code == 404:
            return {
                "credential_status": "connected",
                "provider_health": "ok",
                "capability_health": "config_error",
                "last_checked_at": now_ts,
                "last_error_code": PROVIDER_NOT_FOUND,
                "actor_id": clean_actor,
                "detail": "The configured Apify actor was not found",
            }
        elif r_act.status_code >= 400:
            return {
                "credential_status": "connected",
                "provider_health": "ok",
                "capability_health": "error",
                "last_checked_at": now_ts,
                "last_error_code": self.classify_error(None, r_act.status_code),
                "actor_id": clean_actor,
                "detail": "The Apify actor check returned an error",
            }

        act_data = (r_act.json() or {}).get("data", {})
        act_name = act_data.get("name") or clean_actor

        return {
            "credential_status": "connected",
            "provider_health": "ok",
            "capability_health": "verified",
            "last_checked_at": now_ts,
            "last_error_code": None,
            "actor_id": clean_actor,
            "actor_name": act_name,
            "detail": "Credential authenticated and the configured actor was verified",
        }

    def run_actor_sync(self, actor_id: str, payload: dict, token: str,
                       timeout: int = 120) -> list[dict[str, Any]]:
        """Run the configured actor synchronously and return dataset items.

        Uses Bearer header authentication (never query parameters).
        Scrubs any leaked token from exceptions before raising.
        """
        tok = (token or "").strip()
        if not tok:
            raise RuntimeError("Instagram Public Research is not connected. Connect Apify in Settings.")

        url = self.actor_run_sync_url(actor_id)
        headers = self._headers(tok)

        try:
            r = httpx.post(url, headers=headers, json=payload, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
                return data["items"]
            return []
        except httpx.HTTPStatusError as e:
            code = e.response.status_code if e.response is not None else 0
            err_class = self.classify_error(None, status_code=code)
            safe_msg = sanitize_secrets(str(e))
            raise RuntimeError(f"Apify actor run failed [{err_class}] HTTP {code}: {safe_msg}") from None
        except Exception as e:
            err_class = self.classify_error(e)
            safe_msg = sanitize_secrets(str(e))
            raise RuntimeError(f"Apify actor run failed [{err_class}]: {safe_msg}") from None
