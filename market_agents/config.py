"""Cấu hình đọc từ biến môi trường."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent


def _default_provider() -> str:
    """Tự chọn theo key có sẵn: Claude > Groq > OpenRouter > Gemini (trừ khi chỉ định MARKET_AGENTS_PROVIDER)."""
    explicit = os.getenv("MARKET_AGENTS_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    if os.getenv("ANTHROPIC_API_KEY"):
        return "claude"
    if os.getenv("GROQ_API_KEY"):
        return "groq"
    if os.getenv("OPENROUTER_API_KEY"):
        return "openrouter"
    if _gemini_key():
        return "gemini"
    return "claude"


def _list(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _gemini_key() -> str:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""


@dataclass(frozen=True)
class Settings:
    @property
    def lean(self) -> bool:
        return self.lean_mode if self.lean_mode is not None else self.provider == "groq"

    provider: str = field(default_factory=_default_provider)
    claude_model: str = field(default_factory=lambda: os.getenv("MARKET_AGENTS_MODEL", "claude-opus-5-5"))
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))
    gemini_api_key: str = field(default_factory=_gemini_key)
    groq_api_key: str = field(default_factory=lambda: os.getenv("GROQ_API_KEY", ""))
    # Đã kiểm tra (check-models, 01/11/2026): 3 model dưới đây trả lời + gọi tool được trên Groq.
    groq_model: str = field(default_factory=lambda: os.getenv("GROQ_MODEL", "") or "openai/gpt-oss-120b")
    groq_fallback_models: list[str] = field(
        default_factory=lambda: _list(os.getenv("GROQ_FALLBACK_MODELS", "") or "qwen/qwen3.8-27b,openai/gpt-oss-20b")
    )
    openrouter_api_key: str = field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""))
    openrouter_model: str = field(
        default_factory=lambda: os.getenv("OPENROUTER_MODEL", "") or "google/gemma-4-31b-it:free"
    )
    openrouter_fallback_models: list[str] = field(
        default_factory=lambda: _list(
            os.getenv("OPENROUTER_FALLBACK_MODELS", "") or "nvidia/nemotron-3-ultra-550b-a55b:free,nvidia/nemotron-3.5-super-vl:free"
        )
    )
    timezone: ZoneInfo = field(
        default_factory=lambda: ZoneInfo(os.getenv("MARKET_AGENTS_TIMEZONE", "Asia/Ho_Chi_Minh"))
    )
    use_datafeeds: bool = field(
        default_factory=lambda: os.getenv("MARKET_AGENTS_DATAFEEDS", "1").strip() not in ("0", "false", "")
    )
    # Chế độ gọn: mặc định bật với Groq (giới hạn ~8.000 token/phút ở gói free).
    lean_mode: bool | None = field(default_factory=lambda: {
        "1": True, "true": True, "0": False, "false": False,
    }.get(os.getenv("MARKET_AGENTS_LEAN", "").strip().lower()))
    reports_dir: Path = field(
        default_factory=lambda: Path(os.getenv("MARKET_AGENTS_REPORTS_DIR", ROOT / "reports"))
    )
    # Delivery
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))
    smtp_host: str = field(default_factory=lambda: os.getenv("SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: int(os.getenv("SMTP_PORT", "465") or 465))
    smtp_user: str = field(default_factory=lambda: os.getenv("SMTP_USER", ""))
    smtp_password: str = field(default_factory=lambda: os.getenv("SMTP_PASSWORD", ""))
    email_to: str = field(default_factory=lambda: os.getenv("REPORT_EMAIL_TO", ""))
