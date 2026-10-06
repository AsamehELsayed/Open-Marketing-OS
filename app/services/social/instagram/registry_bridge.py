from __future__ import annotations

import importlib
import re
from dataclasses import dataclass, field
from typing import Any, Callable

INTEGRATIONS_MODULES = ("app.services.integrations", "app.services.integrations.registry")
CREDENTIALS_MODULES = (
    "app.services.credentials",
    "app.services.credentials.vault",
    "app.services.credentials.store",
)


@dataclass(frozen=True)
class IntegrationView:
    integration_id: str = ""
    capability: str = ""
    provider: str = ""
    scope: str = ""
    status: str = ""
    config: dict = field(default_factory=dict)
    secret_ref: str = ""
    last_health: Any = None


def _import_any(paths: tuple[str, ...]):
    for path in paths:
        try:
            return importlib.import_module(path)
        except Exception:
            continue
    return None


def _fn(name: str, paths: tuple[str, ...] = INTEGRATIONS_MODULES) -> Callable | None:
    for path in paths:
        try:
            module = importlib.import_module(path)
        except Exception:
            continue
        candidate = getattr(module, name, None)
        if callable(candidate):
            return candidate
    return None


def integrations_available() -> bool:
    return _import_any(INTEGRATIONS_MODULES) is not None


def vault_available() -> bool:
    return _import_any(CREDENTIALS_MODULES) is not None


def _call_first(calls: tuple[Callable[[], Any], ...]) -> tuple[bool, Any]:
    for call in calls:
        try:
            return True, call()
        except TypeError:
            continue
        except Exception:
            return False, None
    return False, None


def _to_view(raw: Any, capability: str) -> IntegrationView | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        provider = raw.strip()
        if not provider:
            return None
        return IntegrationView(
            capability=capability, provider=provider, status="connected")
    if isinstance(raw, dict):
        data = raw
    else:
        data = {}
        for key in (
            "integration_id", "capability", "provider", "scope", "status",
            "config", "secret_ref", "last_health",
        ):
            if hasattr(raw, key):
                data[key] = getattr(raw, key)
        if not data:
            return None
    provider = str(data.get("provider") or "").strip()
    if not provider:
        return None
    config = data.get("config")
    if not isinstance(config, dict):
        config = {}
    return IntegrationView(
        integration_id=str(data.get("integration_id") or ""),
        capability=str(data.get("capability") or capability),
        provider=provider,
        scope=str(data.get("scope") or ""),
        status=str(data.get("status") or "connected"),
        config=config,
        secret_ref=str(data.get("secret_ref") or ""),
        last_health=data.get("last_health"),
    )


def resolve(capability: str, project_id: str | None = None) -> IntegrationView | None:
    if not capability:
        return None
    record_fn = _fn("resolve_integration")
    if record_fn is not None:
        called, raw = _call_first((
            lambda: record_fn(capability, project_id),
            lambda: record_fn(capability=capability, project_id=project_id),
        ))
        if called:
            return _to_view(raw, capability)
    provider_fn = _fn("resolve_provider")
    if provider_fn is None:
        return None
    called, raw = _call_first((
        lambda: provider_fn(capability, project_id),
        lambda: provider_fn(capability),
        lambda: provider_fn(capability=capability, project_id=project_id),
    ))
    return _to_view(raw, capability) if called else None


def register(record: dict) -> bool:
    if not isinstance(record, dict) or not record.get("capability"):
        return False
    module = _import_any(INTEGRATIONS_MODULES)
    record_type = getattr(module, "IntegrationRecord", None) if module else None
    register_fn = _fn("register_integration")
    if register_fn is None:
        return False
    if record_type is not None:
        try:
            clean_record = record_type(
                integration_id=str(record.get("integration_id") or ""),
                capability=str(record.get("capability") or ""),
                provider=str(record.get("provider") or ""),
                scope=str(record.get("scope") or ""),
                status=str(record.get("status") or ""),
                config=dict(record.get("config") or {}),
                secret_ref=record.get("secret_ref"),
                last_health=record.get("last_health"),
            )
            scope = clean_record.scope
            kwargs = {}
            if scope == "project":
                kwargs["project_id"] = str(
                    (clean_record.config or {}).get("project_id") or "")
            elif scope == "user":
                kwargs["user_id"] = str(
                    (clean_record.config or {}).get("user_id") or "default")
            return bool(register_fn(clean_record, **kwargs))
        except Exception:
            return False
    clean = {
        key: value for key, value in record.items()
        if value is not None or key in ("secret_ref", "last_health")
    }
    called, raw = _call_first((
        lambda: register_fn(**clean),
        lambda: register_fn(clean),
        lambda: register_fn(record_dict=clean),
    ))
    if not called:
        return False
    if isinstance(raw, dict):
        return bool(raw.get("ok", True))
    if isinstance(raw, (bool, type(None))):
        return bool(raw)
    return True


def status(project_id: str | None) -> list[dict]:
    fn = _fn("integration_status")
    if fn is None:
        return []
    called, raw = _call_first((
        lambda: fn(project_id),
        lambda: fn(),
        lambda: fn(project_id=project_id),
    ))
    if not called:
        return []
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    if isinstance(raw, dict):
        return [raw]
    return []


def vault_fn(*names: str) -> Callable | None:
    for name in names:
        fn = _fn(name, CREDENTIALS_MODULES)
        if fn is not None:
            return fn
    return None


def vault_resolve(secret_ref: str | None) -> str | None:
    if not secret_ref:
        return None
    fn = vault_fn("resolve")
    if fn is None:
        return None
    called, raw = _call_first((
        lambda: fn(secret_ref),
        lambda: fn(ref=secret_ref),
        lambda: fn(secret_ref=secret_ref),
    ))
    if not called:
        return None
    if isinstance(raw, str) and raw:
        return raw
    if isinstance(raw, (tuple, list)):
        for item in raw:
            if isinstance(item, str) and item:
                return item
    return None


def _label(value: str | None) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "integration")).strip("._-")
    return clean[:64] or "integration"


def store_secret(
    value: str,
    *,
    scope: str,
    name: str | None = None,
    project_id: str | None = None,
) -> str | None:
    if not value or not scope:
        return None
    module = _import_any(CREDENTIALS_MODULES)
    store_call = getattr(module, "store", None) if module else None
    if callable(store_call):
        label = _label(name)
        owner = project_id
        if scope == "project" and not owner and name and "/" in name:
            owner, label_name = name.split("/", 1)
            label = _label(label_name)
        try:
            raw = store_call(
                scope, label, value, project_id=owner, conn=None)
            return raw if isinstance(raw, str) and raw else None
        except Exception:
            return None
    fn = vault_fn("store")
    if fn is None:
        return None
    extra = {"name": name} if name else {}
    called, raw = _call_first((
        lambda: fn(value, scope=scope, **extra),
        lambda: fn(value, scope),
        lambda: fn(scope=scope, value=value, **extra),
        lambda: fn(scope, value),
    ))
    if not called:
        return None
    if isinstance(raw, str) and raw:
        return raw
    if isinstance(raw, dict):
        for key in ("secret_ref", "ref", "reference"):
            value = raw.get(key)
            if isinstance(value, str) and value:
                return value
    return None
