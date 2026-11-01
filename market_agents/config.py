"""Cấu hình đọc từ biến môi trường."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    model: str = field(default_factory=lambda: os.getenv("MARKET_AGENTS_MODEL", "claude-opus-5-5"))
    timezone: ZoneInfo = field(
        default_factory=lambda: ZoneInfo(os.getenv("MARKET_AGENTS_TIMEZONE", "Asia/Ho_Chi_Minh"))
    )
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
