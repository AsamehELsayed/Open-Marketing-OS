"""Instagram package exports. Capability-first surface for graph/nodes:
request capability strings (instagram_public_profile /
instagram_owned_insights), never vendor implementations."""
from .base import InstagramProvider, audit_result, normalize_account, normalize_post
from .capabilities import (
    CAP_INSTAGRAM_OWNED_INSIGHTS,
    CAP_INSTAGRAM_PUBLIC_PROFILE,
    CREDENTIAL_SCOPE,
    DEFAULT_PROVIDER,
    FROZEN_CAPABILITIES,
    connect_public_profile,
    register_owned_connection,
    resolve_capability,
)
from .router import audit, audit_capability, capability_status, provider_status

__all__ = ["InstagramProvider", "audit_result", "normalize_account",
           "normalize_post", "audit", "audit_capability", "provider_status",
           "capability_status", "resolve_capability",
           "connect_public_profile", "register_owned_connection",
           "CAP_INSTAGRAM_PUBLIC_PROFILE", "CAP_INSTAGRAM_OWNED_INSIGHTS",
           "FROZEN_CAPABILITIES", "DEFAULT_PROVIDER", "CREDENTIAL_SCOPE"]
