"""Provider routing: MANAGER_PROVIDER selection + auto resolution.

auto: iterate MANAGER_PROVIDER_PRIORITY (default openai,deterministic),
pick first available. Explicit selection wins. Legacy MANAGER_MODE=agentic
(with provider=auto) preserves the old openai-or-deterministic behavior.
Application code never touches OpenAI directly — only provider modules do.

DEV-007 W1: OpenCode removed from the product routing graph; the in-process
LangGraph path is the shipped runtime (app/graphs).
"""
import time

_VALID = ("auto", "openai", "openrouter", "deterministic")

_probe_cache: dict = {}


def normalize_selection(value: str | None) -> str:
    v = (value or "auto").strip().lower()
    return v if v in _VALID else "auto"


def openai_configured() -> bool:
    try:
        from app.services.config_service import ConfigService
        return ConfigService.is_openai_configured()
    except Exception:
        return False


def clear_probe_cache() -> None:
    _probe_cache.clear()


def resolve_candidates(selection: str, priority: tuple,
                       *, openai_ok: bool,
                       legacy_agentic: bool = False) -> list[str]:
    """Pure routing decision. Returns ordered candidate names to try in order."""
    sel = normalize_selection(selection)
    if sel != "auto":
        return [sel]
    if legacy_agentic:
        order = ("openai", "deterministic")
    else:
        order = tuple(p for p in (priority or ()) if p in ("openai", "deterministic"))
        order = order or ("openai", "deterministic")
    out: list[str] = []
    for name in order:
        if name == "openai" and not openai_ok:
            continue
        out.append(name)
    if "deterministic" not in out:
        out.append("deterministic")
    return out
