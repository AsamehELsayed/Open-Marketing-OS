"""Integration registry package (I-2): capability -> provider, secrets in the vault.

Public API: IntegrationRecord, register_integration, resolve_provider,
resolve_integration, integration_status, IntegrationNotFound, and the
validated vocabularies PROVIDERS / SCOPES / STATUSES.
"""
from app.services.integrations.registry import (
    INTEGRATIONS_DDL,
    IntegrationNotFound,
    IntegrationRecord,
    PROVIDERS,
    SCOPES,
    STATUSES,
    ensure_integrations,
    integration_status,
    register_integration,
    resolve_integration,
    resolve_provider,
)

__all__ = [
    "INTEGRATIONS_DDL",
    "IntegrationNotFound",
    "IntegrationRecord",
    "PROVIDERS",
    "SCOPES",
    "STATUSES",
    "ensure_integrations",
    "integration_status",
    "register_integration",
    "resolve_integration",
    "resolve_provider",
]
