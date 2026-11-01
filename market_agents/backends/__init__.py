"""Chọn nhà cung cấp AI theo biến môi trường MARKET_AGENTS_PROVIDER (claude | openrouter | gemini)."""
from __future__ import annotations

from ..config import Settings
from ..llm import Backend


def make_backend(settings: Settings) -> Backend:
    if settings.provider == "gemini":
        from .gemini import GeminiBackend

        return GeminiBackend.from_settings(settings)
    if settings.provider == "openrouter":
        from .openrouter import OpenRouterBackend

        return OpenRouterBackend.from_settings(settings)
    if settings.provider == "claude":
        from .claude import ClaudeBackend

        return ClaudeBackend.from_settings(settings)
    raise ValueError(f"MARKET_AGENTS_PROVIDER không hợp lệ: {settings.provider!r} (claude | openrouter | gemini)")
