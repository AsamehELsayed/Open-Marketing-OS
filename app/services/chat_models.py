"""Conversation model selection helpers shared by chat routes and the router."""
from __future__ import annotations


def available_chat_models() -> dict:
    """Return only concrete models that the configured runtime can use."""
    local_models: list[dict] = []
    try:
        from app.routes.graph_runtime import _get_model_router

        provider = _get_model_router().local_provider
        model_id = str(getattr(getattr(provider, "config", None), "model", "") or "").strip()
        if provider is not None and model_id:
            local_models = [{"id": model_id, "name": model_id}]
    except Exception:
        local_models = []

    from app.services.config_service import ConfigService

    openrouter_models: list[dict] = []
    openrouter_connected = ConfigService.is_openrouter_configured()
    openrouter_detail = "OpenRouter is not connected. Add an OpenRouter key in Settings."
    if openrouter_connected:
        from app.services.llm import openrouter_provider as orp

        try:
            openrouter_models = [
                {"id": str(item.get("id") or "").strip(),
                 "name": str(item.get("name") or item.get("id") or "").strip()}
                for item in orp.model_catalog()
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            ]
            openrouter_detail = ("" if openrouter_models else
                                 "OpenRouter is connected, but its model catalog is unavailable.")
        except Exception:
            openrouter_detail = "OpenRouter is connected, but its model catalog could not be loaded."

    return {
        "providers": [
            {"provider": "AUTO", "available": True,
             "detail": "Use the configured automatic routing policy.",
             "models": [{"id": "", "name": "Automatic"}]},
            {"provider": "LOCAL", "available": bool(local_models),
             "detail": "" if local_models else "No verified local model is available. Check Settings.",
             "models": local_models},
            {"provider": "OPENROUTER", "available": bool(openrouter_connected and openrouter_models),
             "detail": openrouter_detail, "models": openrouter_models},
        ]
    }


def normalize_chat_selection(provider: str, model_id: str = "",
                             options: dict | None = None) -> dict:
    """Validate and normalize a conversation selection without fallback."""
    selected = str(provider or "").strip().upper()
    model = str(model_id or "").strip()
    if selected not in {"AUTO", "LOCAL", "OPENROUTER"}:
        raise ValueError("Choose AUTO, LOCAL, or OPENROUTER.")
    if selected == "AUTO":
        if model:
            raise ValueError("AUTO does not accept a specific model.")
        return {"model_provider": "AUTO", "model_id": ""}

    catalog = options if options is not None else available_chat_models()
    provider_info = next((item for item in catalog.get("providers", [])
                          if item.get("provider") == selected), {})
    if not provider_info.get("available"):
        detail = str(provider_info.get("detail") or "The selected model provider is unavailable.")
        raise ValueError(detail)
    supported = {str(item.get("id") or "").strip()
                 for item in provider_info.get("models", []) if isinstance(item, dict)}
    if not model:
        raise ValueError(f"Choose a supported {selected} model.")
    if model not in supported:
        raise ValueError(f"The selected {selected} model is unavailable. Refresh the model list.")
    return {"model_provider": selected, "model_id": model}
