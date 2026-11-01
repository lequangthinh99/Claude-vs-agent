"""Gửi báo cáo: lưu file, Telegram, Email."""
from __future__ import annotations

import json
import logging
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage
from pathlib import Path

from .config import Settings

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4000  # Telegram giới hạn 4096 ký tự / tin nhắn


def save_report(settings: Settings, filename: str, report: str) -> Path:
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    path = settings.reports_dir / filename
    path.write_text(report, encoding="utf-8")
    log.info("Đã lưu báo cáo: %s", path)
    return path


def latest_previous_report(settings: Settings, exclude: str) -> str | None:
    if not settings.reports_dir.exists():
        return None
    files = sorted(p for p in settings.reports_dir.glob("*.md") if p.name != exclude)
    return files[-1].read_text(encoding="utf-8") if files else None


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Chia theo dòng để mỗi phần ≤ limit ký tự."""
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            chunks.append(current)
            current = ""
        current += line
    if current.strip():
        chunks.append(current)
    return chunks


def send_telegram(settings: Settings, report: str) -> bool:
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    parts = split_message(report)
    for i, part in enumerate(parts, 1):
        body = json.dumps(
            {
                "chat_id": settings.telegram_chat_id,
                "text": part if len(parts) == 1 else f"({i}/{len(parts)})\n{part}",
                "disable_web_page_preview": True,
            }
        ).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Telegram trả về HTTP {resp.status}")
    log.info("Đã gửi Telegram (%d phần)", len(parts))
    return True


def send_email(settings: Settings, subject: str, report: str) -> bool:
    if not (settings.smtp_host and settings.email_to):
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_user or settings.email_to
    msg["To"] = settings.email_to
    msg.set_content(report)
    context = ssl.create_default_context()
    if settings.smtp_port == 465:
        server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context, timeout=60)
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=60)
        server.starttls(context=context)
    with server:
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)
    log.info("Đã gửi email tới %s", settings.email_to)
    return True


def deliver(settings: Settings, subject: str, report: str) -> list[str]:
    """Gửi qua mọi kênh đã cấu hình. Lỗi một kênh không chặn kênh khác."""
    sent: list[str] = []
    for name, fn in (
        ("telegram", lambda: send_telegram(settings, report)),
        ("email", lambda: send_email(settings, subject, report)),
    ):
        try:
            if fn():
                sent.append(name)
        except Exception:  # noqa: BLE001 - ghi log và thử kênh tiếp theo
            log.exception("Gửi qua %s thất bại", name)
    if not sent:
        log.warning("Chưa cấu hình/gửi được kênh nào; báo cáo chỉ được lưu file.")
    return sent
