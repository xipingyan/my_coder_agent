"""Provider registry. Add new back-ends with ``register_provider``."""

from __future__ import annotations

from typing import Callable

from ..config import AppConfig
from .base import LLMCancelled, LLMChunk, LLMError, LLMProvider, ToolCall, Usage
from .openai_compat import OpenAICompatProvider
from .think_parser import ThinkParser
from .toolparse import extract_text_tool_calls

ProviderFactory = Callable[[AppConfig], LLMProvider]

_REGISTRY: dict[str, ProviderFactory] = {}


def register_provider(name: str, factory: ProviderFactory) -> None:
    _REGISTRY[name] = factory


def create_provider(cfg: AppConfig) -> LLMProvider:
    name = cfg.server.provider
    if name not in _REGISTRY:
        raise LLMError(f"unknown provider '{name}' (available: {', '.join(sorted(_REGISTRY))})")
    return _REGISTRY[name](cfg)


def _openai_compat(cfg: AppConfig) -> LLMProvider:
    return OpenAICompatProvider(cfg.server, cfg.thinking, cfg.sampling)


register_provider("openai_compat", _openai_compat)
register_provider("ollama", _openai_compat)  # Ollama speaks the OpenAI protocol on /v1
# "ovms" is intentionally not registered yet.

__all__ = [
    "LLMCancelled",
    "LLMChunk",
    "LLMError",
    "LLMProvider",
    "OpenAICompatProvider",
    "ThinkParser",
    "ToolCall",
    "Usage",
    "create_provider",
    "extract_text_tool_calls",
    "register_provider",
]
