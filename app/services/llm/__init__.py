"""LLM provider abstraction (v0.2 §4). Loop speaks this; no app code touches OpenAI.

DEV-007 W1: OpenCode provider removed from the product import graph.
"""
from .base import LLMProvider, LLMResponse, ToolCall, ToolSpec
from .config import ManagerConfig, config_from_env
from .fake_provider import FakeProvider
from .openai_provider import OpenAIProvider, build_provider
from .router import normalize_selection, openai_configured, resolve_candidates

__all__ = ["LLMProvider", "LLMResponse", "ToolCall", "ToolSpec",
           "ManagerConfig", "config_from_env", "FakeProvider",
           "OpenAIProvider", "build_provider",
           "normalize_selection", "openai_configured", "resolve_candidates"]
