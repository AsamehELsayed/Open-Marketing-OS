"""Manager loop config from env. All defaults cheap/safe; no network needed."""
import os
from dataclasses import dataclass


@dataclass
class ManagerConfig:
    provider_name: str = "openai"
    model: str = "gpt-4o-mini"
    max_tool_iters: int = 6
    turn_timeout_s: int = 120
    per_call_timeout_s: int = 45
    max_context_tokens: int = 12000
    manager_mode: str = "deterministic"  # legacy; superseded by manager_provider
    manager_provider: str = "auto"  # auto | openai | deterministic
    provider_priority: tuple = ("openai", "deterministic")
    # DEV-005/007: v1 runtime switch. DEV-007 W1 default is langgraph;
    # AI_RUNTIME=legacy is rollback-only (v1.0.0 tag).
    ai_runtime: str = "langgraph"  # legacy | langgraph
    max_agent_concurrency: int = 4  # hard cap 4

    @property
    def agentic_enabled(self) -> bool:
        return self.manager_mode == "agentic"


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (ValueError, TypeError):
        return default


def _ai_runtime() -> str:
    raw = (os.environ.get("AI_RUNTIME", "") or "").strip().lower()
    return raw if raw in ("legacy", "langgraph") else "langgraph"


def _concurrency() -> int:
    try:
        n = int(os.environ.get("MAX_AGENT_CONCURRENCY", 4))
    except (ValueError, TypeError):
        return 4
    return n if 1 <= n <= 4 else 4


def config_from_env() -> ManagerConfig:
    from app.services.config_service import ConfigService
    return ManagerConfig(
        provider_name=str(
            ConfigService.get_setting("manager_provider", "auto") or "auto"),
        model=str(
            ConfigService.get_setting("active_model", "gpt-4o-mini")
            or "gpt-4o-mini"),
        max_tool_iters=_int("OPENAI_MAX_TOOL_ITERS", 6),
        turn_timeout_s=_int("OPENAI_TURN_TIMEOUT_S", 120),
        per_call_timeout_s=_int("OPENAI_PER_CALL_TIMEOUT_S", 45),
        max_context_tokens=_int("OPENAI_MAX_CONTEXT_TOKENS", 12000),
        manager_mode="deterministic",
        manager_provider=str(
            ConfigService.get_setting("manager_provider", "auto") or "auto"
        ).strip().lower(),
        provider_priority=("openai", "deterministic"),
        ai_runtime=_ai_runtime(),
        max_agent_concurrency=_concurrency(),
    )
