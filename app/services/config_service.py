from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from app import deps
from app.database import repos
from app.database.identity import DEFAULT_PROJECT_ID
from app.services.credentials import vault as _vault

DEFAULT_THEME = "system"
DEFAULT_AI_MODE = "AUTO"
DEFAULT_BASE_MODEL = "Qwen2.5-7B-Instruct"
DEFAULT_MARKETING_ADAPTER = "OMOS Marketing v1"
DEFAULT_MANAGER_PROVIDER = "auto"
DEFAULT_INSTAGRAM_PROVIDER = "apify"
DEFAULT_LLAMA_BASE_URL = "http://localhost:8080"
DEFAULT_DATA_DIR = "data"
DEFAULT_LANGUAGE = "en"

LABEL_OPENAI = "integration_openai"
LABEL_APIFY = "integration_apify"
LABEL_BRIGHTDATA = "integration_brightdata"
LABEL_META = "integration_meta"
LABEL_OPENROUTER = "integration_openrouter"

_TEST_OVERRIDES: dict[str, Any] = {}


def set_test_override(key: str, value: Any) -> None:
    _TEST_OVERRIDES[key] = value


def clear_test_override(key: str) -> None:
    _TEST_OVERRIDES.pop(key, None)


def clear_all_test_overrides() -> None:
    _TEST_OVERRIDES.clear()


@contextmanager
def _get_conn(conn=None):
    if conn is not None:
        yield conn
    else:
        with deps.get_db() as current:
            yield current


class ConfigService:
    @classmethod
    def get_setting(cls, key: str, default: Any = None, conn=None) -> Any:
        if key in _TEST_OVERRIDES:
            return _TEST_OVERRIDES[key]
        with _get_conn(conn) as current:
            try:
                row = repos.Settings.get(current, key)
                if row and row.get("value") is not None and str(row["value"]).strip():
                    return row["value"]
            except Exception:
                pass
        return default

    @classmethod
    def set_setting(cls, key: str, value: Any, conn=None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with _get_conn(conn) as current:
            repos.Settings.set(current, key, "" if value is None else str(value), now)

    @classmethod
    def get_general_config(cls, conn=None) -> dict[str, Any]:
        return {
            "theme": cls.get_setting("theme", DEFAULT_THEME, conn=conn),
            "auto_load_project": cls.get_setting(
                "auto_load_project", "1", conn=conn) == "1",
            "language": cls.get_setting("language", DEFAULT_LANGUAGE, conn=conn),
        }

    @classmethod
    def update_general_config(
        cls,
        theme: str | None = None,
        auto_load_project: bool | None = None,
        language: str | None = None,
        conn=None,
    ) -> dict[str, Any]:
        with _get_conn(conn) as current:
            if theme is not None:
                cls.set_setting("theme", theme, conn=current)
            if auto_load_project is not None:
                cls.set_setting(
                    "auto_load_project", "1" if auto_load_project else "0", conn=current)
            if language is not None:
                cls.set_setting("language", language, conn=current)
        return cls.get_general_config(conn=conn)

    @classmethod
    def get_ai_config(cls, conn=None) -> dict[str, Any]:
        with _get_conn(conn) as current:
            return {
                "ai_mode": str(cls.get_setting(
                    "ai_mode", DEFAULT_AI_MODE, conn=current)).upper(),
                "local_ai_status": cls.get_setting(
                    "health_local_provider_capability", "unknown", conn=current),
                "active_model": cls.get_setting(
                    "active_model", DEFAULT_BASE_MODEL, conn=current),
                "marketing_adapter": cls.get_setting(
                    "marketing_adapter", DEFAULT_MARKETING_ADAPTER, conn=current),
                "manager_provider": str(cls.get_setting(
                    "manager_provider", DEFAULT_MANAGER_PROVIDER,
                    conn=current)).lower(),
                "openai_connected": cls.is_openai_configured(conn=current),
                "openrouter_credential_status": cls.get_openrouter_credential_status(
                    conn=current),
                "openrouter_connected": cls.is_openrouter_configured(conn=current),
                "openrouter_default_model": cls.get_setting(
                    "openrouter_default_model", "", conn=current) or "",
                "cloud_escalation": cls.get_setting(
                    "cloud_escalation", "off", conn=current),
                "local_model_enabled": str(cls.get_setting(
                    "local_model_enabled", "on", conn=current)).lower()
                    in ("1", "on", "true", "yes"),
                "orchestrator": "langgraph",
            }

    @classmethod
    def update_ai_config(
        cls,
        ai_mode: str | None = None,
        active_model: str | None = None,
        marketing_adapter: str | None = None,
        manager_provider: str | None = None,
        openrouter_default_model: str | None = None,
        cloud_escalation: str | None = None,
        local_model_enabled: bool | None = None,
        conn=None,
    ) -> dict[str, Any]:
        with _get_conn(conn) as current:
            if ai_mode is not None:
                cls.set_setting("ai_mode", ai_mode.upper(), conn=current)
            if active_model is not None:
                cls.set_setting("active_model", active_model, conn=current)
            if marketing_adapter is not None:
                cls.set_setting("marketing_adapter", marketing_adapter, conn=current)
            if manager_provider is not None:
                cls.set_setting(
                    "manager_provider", manager_provider.lower(), conn=current)
            if openrouter_default_model is not None:
                cls.set_setting(
                    "openrouter_default_model",
                    str(openrouter_default_model).strip(), conn=current)
            if cloud_escalation is not None:
                enabled = str(cloud_escalation).lower() in ("1", "on", "true")
                cls.set_setting("cloud_escalation", "on" if enabled else "off", conn=current)
            if local_model_enabled is not None:
                cls.set_setting("local_model_enabled",
                                "on" if local_model_enabled else "off", conn=current)
        return cls.get_ai_config(conn=conn)

    @classmethod
    def _configured(cls, override: str, label: str, scope: str,
                    user_id: str | None = None, conn=None) -> bool:
        if override in _TEST_OVERRIDES:
            return bool(_TEST_OVERRIDES[override])
        with _get_conn(conn) as current:
            return bool(_vault.get_secret_ref(
                scope, label, user_id=user_id, conn=current))

    @classmethod
    def _resolve(cls, override: str, label: str, scope: str,
                 user_id: str | None = None, conn=None) -> str:
        if override in _TEST_OVERRIDES:
            return str(_TEST_OVERRIDES[override])
        with _get_conn(conn) as current:
            ref = _vault.get_secret_ref(
                scope, label, user_id=user_id, conn=current)
            if not ref:
                return ""
            try:
                return _vault.resolve(ref, conn=current)
            except Exception:
                return ""

    @classmethod
    def _store(cls, value: str, label: str, scope: str,
               user_id: str | None = None, conn=None) -> str:
        clean = (value or "").strip()
        if not clean:
            raise ValueError("Credential cannot be empty")
        with _get_conn(conn) as current:
            return _vault.store(
                scope, label, clean, user_id=user_id, conn=current)

    @classmethod
    def _clear(cls, label: str, scope: str,
               user_id: str | None = None, conn=None) -> bool:
        with _get_conn(conn) as current:
            ref = _vault.get_secret_ref(
                scope, label, user_id=user_id, conn=current)
            if not ref:
                return False
            _vault.revoke(ref, conn=current)
            return True

    @classmethod
    def is_openai_configured(cls, conn=None) -> bool:
        if cls._configured("openai_api_key", LABEL_OPENAI, "user", "default", conn):
            return True
        return cls._configured("openai_api_key", LABEL_OPENAI, "installation", conn=conn)

    @classmethod
    def get_openai_api_key(cls, conn=None) -> str:
        if "openai_api_key" in _TEST_OVERRIDES:
            return str(_TEST_OVERRIDES["openai_api_key"])
        with _get_conn(conn) as current:
            for scope, user_id in (("user", "default"), ("installation", None)):
                ref = _vault.get_secret_ref(
                    scope, LABEL_OPENAI, user_id=user_id, conn=current)
                if not ref:
                    continue
                try:
                    return _vault.resolve(ref, conn=current)
                except Exception:
                    continue
            legacy = _vault.get_secret_ref(
                "installation", "openai_api_key", conn=current)
            if legacy:
                try:
                    return _vault.resolve(legacy, conn=current)
                except Exception:
                    return ""
        return ""

    @classmethod
    def store_openai_api_key(cls, key: str, scope: str = "installation", conn=None) -> str:
        return cls._store(
            key, LABEL_OPENAI, scope,
            "default" if scope == "user" else None, conn)

    @classmethod
    def clear_openai_api_key(cls, conn=None) -> bool:
        with _get_conn(conn) as current:
            cleared = False
            for scope, user_id in (("user", "default"), ("installation", None)):
                ref = _vault.get_secret_ref(
                    scope, LABEL_OPENAI, user_id=user_id, conn=current)
                if ref:
                    _vault.revoke(ref, conn=current)
                    cleared = True
            legacy = _vault.get_secret_ref(
                "installation", "openai_api_key", conn=current)
            if legacy:
                _vault.revoke(legacy, conn=current)
                cleared = True
            return cleared

    @classmethod
    def is_openrouter_configured(cls, conn=None) -> bool:
        return cls.get_openrouter_credential_status(conn=conn) == "configured"

    @classmethod
    def get_openrouter_credential_status(cls, conn=None) -> str:
        """Read-only local vault readiness; never performs provider connectivity."""
        if "openrouter_key" in _TEST_OVERRIDES:
            return "configured" if bool(_TEST_OVERRIDES["openrouter_key"]) else "not_configured"
        with _get_conn(conn) as current:
            return _vault.credential_status(
                "installation", LABEL_OPENROUTER, conn=current)

    @classmethod
    def get_openrouter_api_key(cls, conn=None) -> str:
        return cls._resolve(
            "openrouter_key", LABEL_OPENROUTER, "installation", conn=conn)

    @classmethod
    def store_openrouter_key(cls, key: str, conn=None) -> str:
        return cls._store(key, LABEL_OPENROUTER, "installation", conn=conn)

    @classmethod
    def clear_openrouter_key(cls, conn=None) -> bool:
        return cls._clear(
            LABEL_OPENROUTER, "installation", conn=conn)

    @classmethod
    def is_apify_configured(cls, conn=None) -> bool:
        return cls._configured(
            "apify_token", LABEL_APIFY, "installation", conn=conn)

    @classmethod
    def get_apify_token(cls, conn=None) -> str:
        return cls._resolve(
            "apify_token", LABEL_APIFY, "installation", conn=conn)

    @classmethod
    def store_apify_token(cls, token: str, conn=None) -> str:
        return cls._store(token, LABEL_APIFY, "installation", conn=conn)

    @classmethod
    def clear_apify_token(cls, conn=None) -> bool:
        return cls._clear(LABEL_APIFY, "installation", conn=conn)

    @classmethod
    def is_brightdata_configured(cls, conn=None) -> bool:
        return cls._configured(
            "brightdata_key", LABEL_BRIGHTDATA, "installation", conn=conn)

    @classmethod
    def get_brightdata_key(cls, conn=None) -> str:
        return cls._resolve(
            "brightdata_key", LABEL_BRIGHTDATA, "installation", conn=conn)

    @classmethod
    def store_brightdata_key(cls, key: str, conn=None) -> str:
        return cls._store(key, LABEL_BRIGHTDATA, "installation", conn=conn)

    @classmethod
    def clear_brightdata_key(cls, conn=None) -> bool:
        return cls._clear(LABEL_BRIGHTDATA, "installation", conn=conn)

    @classmethod
    def get_integrations_overview(
        cls, project_id: str | None = None, conn=None
    ) -> dict[str, Any]:
        with _get_conn(conn) as current:
            pid = project_id or ""
            if not pid:
                try:
                    from app.services.state import active_project_id
                    pid = active_project_id(current)
                except Exception:
                    pid = DEFAULT_PROJECT_ID

            apify_credential = cls.is_apify_configured(conn=current)
            brightdata_credential = cls.is_brightdata_configured(conn=current)
            public_provider = str(cls.get_setting(
                "instagram_public_provider", DEFAULT_INSTAGRAM_PROVIDER,
                conn=current)).lower()

            handle = ""
            try:
                for row in repos.SocialAccounts.for_project(current, pid):
                    if row.get("platform") == "instagram":
                        handle = str(row.get("handle") or "")
                        break
            except Exception:
                pass

            def health(key: str) -> dict[str, str]:
                return {
                    "credential": str(cls.get_setting(
                        f"health_{key}_credential", "not_configured", conn=current)),
                    "provider": str(cls.get_setting(
                        f"health_{key}_provider", "unknown", conn=current)),
                    "capability": str(cls.get_setting(
                        f"health_{key}_capability", "unknown", conn=current)),
                    "last_checked_at": str(cls.get_setting(
                        f"health_{key}_last_checked", "", conn=current) or ""),
                }

            apify_health = health("apify")
            brightdata_health = health("brightdata")

            public_view = None
            owned_view = None
            registry_rows: list[dict[str, Any]] = []
            try:
                from app.services.social.instagram import capabilities as instagram_caps
                from app.services.social.instagram import registry_bridge
                public_view = registry_bridge.resolve(
                    instagram_caps.CAP_INSTAGRAM_PUBLIC_PROFILE, pid)
                owned_view = registry_bridge.resolve(
                    instagram_caps.CAP_INSTAGRAM_OWNED_INSIGHTS, pid)
                registry_rows = registry_bridge.status(pid)
            except Exception:
                pass

            def _provider_row(provider: str) -> dict[str, Any] | None:
                for row in registry_rows:
                    if row.get("provider") == provider:
                        return row
                return None

            def _connected(view: Any) -> bool:
                if view is None:
                    return False
                if isinstance(view, dict):
                    return bool(
                        view.get("status") == "connected"
                        and view.get("has_secret")
                    )
                return bool(
                    view.status == "connected" and view.secret_ref
                )

            apify_view = _provider_row("apify")
            brightdata_view = _provider_row("brightdata")
            public_connected = bool(
                _connected(public_view)
                or _connected(apify_view)
                or _connected(brightdata_view)
            )
            apify_ready = apify_health["capability"] == "verified"
            brightdata_ready = brightdata_health["capability"] == "verified"
            brightdata_dataset_configured = bool(
                brightdata_view
                and (brightdata_view.get("config") or {}).get("dataset_id")
                and _connected(brightdata_view)
            )
            brightdata_dataset_id = str(
                (brightdata_view or {}).get("config", {}).get("dataset_id", "") or ""
            )

            meta_ref_active = False
            if owned_view is not None and owned_view.secret_ref:
                try:
                    ref_row = _vault.get_ref_row(owned_view.secret_ref, conn=current)
                    meta_ref_active = bool(
                        ref_row and ref_row.get("status") == _vault.ACTIVE)
                except Exception:
                    meta_ref_active = False
            meta_connected = bool(
                owned_view is not None
                and owned_view.provider == "meta"
                and owned_view.status == "connected"
                and meta_ref_active
                and (owned_view.config or {}).get("ig_account_id")
            )
            openrouter_status = cls.get_openrouter_credential_status(conn=current)
            openrouter_credential = openrouter_status == "configured"
            openrouter_health = health("openrouter")
            meta_health = health("meta")

            mcp_servers: list[dict[str, Any]] = []
            try:
                from app.services.mcp import mcp_status
                for row in mcp_status(pid) or []:
                    if not isinstance(row, dict):
                        continue
                    server_id = str(
                        row.get("server_id") or row.get("server") or row.get("name") or "unknown"
                    )
                    mcp_servers.append({
                        "server_id": server_id,
                        "server": server_id,
                        "name": server_id,
                        "connected": bool(row.get("connected")),
                        "status": row.get("status") or ("connected" if row.get("connected") else "unknown"),
                        "last_health": row.get("last_health") or "unknown",
                        "last_checked_at": row.get("last_checked_at") or "",
                        "scope": row.get("scope", "project"),
                        "capabilities": row.get("capabilities", []),
                    })
            except Exception:
                pass

            return {
                "instagram_public": {
                    "connected": public_connected,
                    "provider": public_provider,
                    "apify_credential": apify_credential,
                    "apify_ready": apify_ready,
                    "apify_credential_status": apify_health["credential"],
                    "apify_provider_health": apify_health["provider"],
                    "apify_capability_health": apify_health["capability"],
                    "apify_last_checked_at": apify_health["last_checked_at"],
                    "apify_actor_id": str(
                        (public_view.config if public_view else {}).get(
                            "actor_id", "") or ""),
                    "brightdata_credential": brightdata_credential,
                    "brightdata_configured": _connected(brightdata_view),
                    "brightdata_dataset_configured": brightdata_dataset_configured,
                    "brightdata_dataset_id": brightdata_dataset_id,
                    "brightdata_ready": brightdata_ready,
                    "brightdata_credential_status": brightdata_health["credential"],
                    "brightdata_provider_health": brightdata_health["provider"],
                    "brightdata_capability_health": brightdata_health["capability"],
                    "brightdata_last_checked_at": brightdata_health["last_checked_at"],
                    "browser_fallback": False,
                    "cost_type": "metered",
                },
                "instagram_project": {
                    "project_id": pid,
                    "handle": handle,
                    "verified": False,
                },
                "openai": {
                    "connected": cls.is_openai_configured(conn=current),
                },
                "openrouter": {
                    "connected": openrouter_credential,
                    "credential": openrouter_status,
                    "provider": openrouter_health["provider"],
                    "capability": openrouter_health["capability"],
                    "last_checked_at": openrouter_health["last_checked_at"],
                    "default_model": cls.get_setting(
                        "openrouter_default_model", "", conn=current) or "",
                    "cloud_escalation": cls.get_setting(
                        "cloud_escalation", "off", conn=current),
                },
                "vision": {
                    "provider": "openai",
                    "status": "unavailable",
                    "credential_status": (
                        "connected" if cls.is_openai_configured(conn=current)
                        else "not_configured"
                    ),
                    "capability_health": "unavailable",
                    "last_checked_at": "",
                    "ready": False,
                    "detail": "Vision inference is unavailable in this build.",
                },
                "meta_insights": {
                    "connected": meta_connected,
                    "status": "connected" if meta_connected else "not_connected",
                    "credential_status": meta_health["credential"],
                    "provider_health": meta_health["provider"],
                    "capability_health": meta_health["capability"],
                    "last_checked_at": meta_health["last_checked_at"],
                    "account_id": str(
                        (owned_view.config if owned_view else {}).get(
                            "ig_account_id", "") or ""
                    ),
                    "ig_account_id": str(
                        (owned_view.config if owned_view else {}).get(
                            "ig_account_id", "") or ""
                    ),
                },
                "web_research": {
                    "status": "unknown",
                    "provider": "unknown",
                },
                "mcp": {
                    "servers": mcp_servers,
                    "count": len(mcp_servers),
                },
            }

    @classmethod
    def get_files_and_knowledge_status(
        cls, project_id: str | None = None, conn=None
    ) -> dict[str, Any]:
        with _get_conn(conn) as current:
            pid = (project_id or "").strip()
            if not pid:
                raise ValueError("project_id is required for knowledge status")
            documents: int | None = None
            files: int | None = None
            query_error = ""
            try:
                row = current.execute(
                    "SELECT COUNT(*) FROM documents WHERE project_id = ?",
                    (pid,)).fetchone()
                documents = int(row[0]) if row else 0
            except Exception:
                query_error = "knowledge_counts_unavailable"
            try:
                row = current.execute(
                    "SELECT COUNT(*) FROM project_files WHERE project_id = ? "
                    "AND attach_scope = 'project'", (pid,)).fetchone()
                files = int(row[0]) if row else 0
            except Exception:
                query_error = query_error or "knowledge_counts_unavailable"
            try:
                from app.services.knowledge_index import retrieval_status, file_capabilities
                runtime = retrieval_status(cls._root_for_knowledge(), pid, conn=current)
                capabilities = file_capabilities()
            except Exception:
                runtime = {"search_mode": "UNAVAILABLE", "vector_status": "NOT_AVAILABLE",
                           "vector_reason": "runtime_status_unavailable"}
                capabilities = {"accepted_types": [], "max_document_bytes": 25 * 1024 * 1024,
                                "max_image_bytes": 15 * 1024 * 1024, "ocr_supported": False}
            conflicts: list = []
            try:
                from app.services import state as store
                conflicts = store.list_conflicts(current)
            except Exception:
                pass
            return {
                "project_id": pid,
                "indexed_documents": documents,
                "project_files": files,
                "count_status": "available" if not query_error else "unavailable",
                "count_error": query_error,
                **runtime,
                **capabilities,
                "storage_location": f"project_workspace/{pid}",
                "max_upload_mb": round((capabilities.get("max_document_bytes") or 0) / (1024 * 1024), 2),
                "conflicts": conflicts,
            }

    @staticmethod
    def _root_for_knowledge():
        from app import deps
        return deps.ROOT

    @classmethod
    def get_privacy_and_security(cls, conn=None) -> dict[str, Any]:
        with _get_conn(conn) as current:
            _vault.ensure_credentials_refs(current)
            rows = current.execute(
                "SELECT scope, label, project_id, user_id, status, created_at "
                "FROM credentials_refs WHERE status = 'active'"
            ).fetchall()
            credentials = [
                {
                    "scope": row["scope"],
                    "label": row["label"],
                    "created_at": row["created_at"],
                    "status": row["status"],
                }
                for row in rows
            ]
            backend = _vault.default_backend()
            return {
                "vault_backend": backend.name,
                "encryption": "OS DPAPI / scrypt" if backend.name == "dpapi_file" else backend.name,
                "active_credentials": credentials,
                "total_stored": len(credentials),
                "data_privacy": "Configuration, credentials, and knowledge files stay on this device.",
            }

    @classmethod
    def get_system_health(cls, conn=None) -> dict[str, Any]:
        with _get_conn(conn) as current:
            database = "unknown"
            try:
                current.execute("SELECT 1").fetchone()
                database = "available"
            except Exception:
                database = "unknown"
            from app.contracts.runtime import get_ai_runtime
            return {
                "version": "1.0.0",
                "status": "unknown",
                "backend": "unknown",
                "orchestrator": get_ai_runtime(),
                "database": database,
                "rag": cls.get_setting("chroma_status", "unknown", conn=current),
                "model_runtime": "unknown",
                "vision": "unavailable",
                "active_model": cls.get_setting(
                    "active_model", DEFAULT_BASE_MODEL, conn=current),
            }
