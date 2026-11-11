"""Gửi báo cáo: lưu file, Telegram, Email."""
from __future__ import annotations

import html
import json
import logging
import smtplib
import ssl
import re
import urllib.error
import urllib.request
from email.message import EmailMessage
from pathlib import Path

from .config import Settings
from .telegram_format import DIVIDER, to_telegram_html

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
    files = sorted((p for p in settings.reports_dir.glob("*.md") if p.name != exclude), key=_report_order)
    return files[-1].read_text(encoding="utf-8") if files else None


def _report_order(path: Path) -> tuple[str, int]:
    """Sắp theo ngày rồi tới phiên (sáng trước chiều) — tên file thì "evening" lại đứng trước "morning"."""
    day, _, kind = path.stem.rpartition("-")
    return (day, {"morning": 0, "evening": 1}.get(kind, 2))


def configured_channels(settings: Settings) -> list[str]:
    channels = []
    if settings.telegram_bot_token and settings.telegram_chat_id:
        channels.append("telegram")
    if settings.smtp_host and settings.email_to:
        channels.append("email")
    return channels


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


def pack_sections(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Gom các phần (bắt đầu bằng đường kẻ) vào tin nhắn ≤ limit, ưu tiên không cắt giữa một phần."""
    sections: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        if line == DIVIDER and current:
            sections.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        sections.append("\n".join(current))
    chunks: list[str] = []
    buf = ""
    for sec in sections:
        candidate = f"{buf}\n\n{sec}" if buf else sec
        if len(candidate) <= limit:
            buf = candidate
            continue
        if buf:
            chunks.append(buf)
        if len(sec) <= limit:
            buf = sec
        else:  # một phần quá dài: cắt theo dòng
            pieces = split_message(sec, limit)
            chunks.extend(p.rstrip("\n") for p in pieces[:-1])
            buf = pieces[-1].rstrip("\n")
    if buf.strip():
        chunks.append(buf)
    return chunks


def _telegram_post(url: str, payload: dict) -> None:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Telegram trả về HTTP {resp.status}")


def send_telegram(settings: Settings, report: str, post=_telegram_post) -> bool:
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    parts = pack_sections(to_telegram_html(report))
    for i, part in enumerate(parts, 1):
        text = part if len(parts) == 1 else f"<i>Phần {i}/{len(parts)}</i>\n{part}"
        payload = {"chat_id": settings.telegram_chat_id, "text": text,
                   "parse_mode": "HTML", "disable_web_page_preview": True}
        try:
            post(url, payload)
        except urllib.error.HTTPError as exc:
            if exc.code != 400:
                raise
            # Telegram không đọc được định dạng: gửi lại dạng chữ thường
            log.warning("Telegram từ chối HTML (phần %d), gửi lại dạng chữ thường", i)
            plain = html.unescape(re.sub(r"<[^>]+>", "", text))
            post(url, {"chat_id": settings.telegram_chat_id, "text": plain,
                       "disable_web_page_preview": True})
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
