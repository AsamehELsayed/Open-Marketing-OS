"""DEV-005 W3 — CPU/GPU-neutral loopback config for the local llama.cpp server.

No model download, no server start, no secrets. Loopback-only by default
(fail-closed); CPU fallback unless the founder explicitly opts into GPU
offload. Mirrors llama-cpp skill: bind 127.0.0.1 first, prove /health,
/v1/models, and one streaming call before any tuning claim.
"""
import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_BASE_URL = "http://127.0.0.1:8080"
LOCAL_PRICING_VERSION = "local-2026-09-22"
DEFAULT_BASE_MODEL = "Qwen2.5-7B-Instruct"
DEFAULT_MARKETING_ADAPTER = "OMOS-Qwen2.5-7B-Marketing-v1"


def is_loopback_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in LOOPBACK_HOSTS


@dataclass
class LocalLlamaConfig:
    """Neutral defaults: CPU works everywhere; GPU is opt-in, never assumed."""

    base_url: str = DEFAULT_BASE_URL
    model: str = ""
    quantization: str = ""
    adapter: str = ""
    timeout_s: int = 45
    health_timeout_s: int = 5
    n_ctx: int = 8192
    n_gpu_layers: int = 0  # 0 = CPU fallback; founder raises after load-log proof
    extra_server_flags: tuple = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not is_loopback_url(self.base_url):
            raise ValueError(
                f"LocalLlamaConfig refuses non-loopback base_url={self.base_url!r} "
                "(loopback-only; external exposure needs an explicit ADR)")
        self.timeout_s = max(1, int(self.timeout_s))
        self.health_timeout_s = max(1, int(self.health_timeout_s))
        if self.n_ctx < 1024:
            raise ValueError("n_ctx must be >= 1024")
        if self.n_gpu_layers < 0:
            raise ValueError("n_gpu_layers must be >= 0")

    def suggested_server_args(self) -> list:
        """Documentation helper only — never executed by this module."""
        args = ["llama-server", "--host", "127.0.0.1", "--port",
                str(urlparse(self.base_url).port or 8080),
                "--ctx-size", str(self.n_ctx)]
        if self.n_gpu_layers > 0:
            args += ["--n-gpu-layers", str(self.n_gpu_layers)]
        args += list(self.extra_server_flags)
        return args


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (ValueError, TypeError):
        return default


def config_from_env() -> LocalLlamaConfig:
    from app.services.config_service import ConfigService
    adapter_override = ConfigService.get_setting("marketing_adapter", None)
    adapter_env = adapter_override or os.environ.get("LLAMA_ADAPTER")
    router_mode = str(
        ConfigService.get_setting("ai_mode", None)
        or os.environ.get("ROUTER_MODE", "")
        or "AUTO"
    ).strip().upper()
    if router_mode in ("BASE", "LOCAL"):
        adapter = ""
    elif router_mode == "MARKETING_LORA":
        adapter = DEFAULT_MARKETING_ADAPTER if not adapter_env else adapter_env
    else:
        adapter = DEFAULT_MARKETING_ADAPTER if adapter_env is None else adapter_env
    model = str(
        ConfigService.get_setting("active_model", None)
        or os.environ.get("LLAMA_MODEL", DEFAULT_BASE_MODEL)
    )
    base_url = str(
        ConfigService.get_setting("llama_base_url", None)
        or os.environ.get("LLAMA_BASE_URL", DEFAULT_BASE_URL)
    )
    quantization = str(
        ConfigService.get_setting("llama_quantization", None)
        or os.environ.get("LLAMA_QUANT", "")
    )
    return LocalLlamaConfig(
        base_url=base_url,
        model=model,
        quantization=quantization,
        adapter=adapter,
        timeout_s=_int("LLAMA_TIMEOUT_S", 45),
        health_timeout_s=_int("LLAMA_HEALTH_TIMEOUT_S", 5),
        n_ctx=_int("LLAMA_N_CTX", 8192),
        n_gpu_layers=_int("LLAMA_N_GPU_LAYERS", 0),
    )
