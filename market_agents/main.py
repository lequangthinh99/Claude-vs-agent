"""Điểm chạy chính.

    python -m market_agents.main                  # tự chọn phiên sáng/chiều theo giờ VN
    python -m market_agents.main --session morning
    python -m market_agents.main --hold           # tạo xong thì chờ tới đúng 07:00/18:00 mới gửi
    python -m market_agents.main --no-send        # chỉ tạo và lưu file, không gửi
    python -m market_agents.main --scheduled      # cho lịch tự động: tự chọn phiên theo khung giờ,
                                                  # bỏ qua nếu quá sớm / quá trễ / đã gửi
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta

from . import datafeeds, tracking
from .backends import make_backend
from .config import Settings
from .delivery import configured_channels, deliver, latest_previous_report, save_report
from .llm import AgentError, Backend
from .manager import SLOTS, ManagerAgent, Session

log = logging.getLogger(__name__)


def detect_session(now: datetime) -> str:
    """Trước 12:00 → phiên sáng; từ 12:00 → phiên chiều."""
    return "morning" if now.hour < 12 else "evening"


# Chế độ gọn cần ít nhất chừng này mã giá lấy được thì mới tạo bản tin.
MIN_QUOTES = 8
# Chỉ giữ bản tin tối đa chừng này phút để chờ đúng giờ gửi. Lịch GitHub có thể khởi động
# lệch nhiều giờ; chờ lâu hơn sẽ chạm timeout của job và mất luôn bản tin.
MAX_HOLD_MINUTES = int(os.getenv("MARKET_AGENTS_MAX_HOLD_MINUTES", "75"))
# Lần chạy theo lịch tới trễ hơn chừng này giờ so với giờ gửi thì bỏ qua (bản tin đã cũ).
MAX_LATE_HOURS = float(os.getenv("MARKET_AGENTS_MAX_LATE_HOURS", "6"))


class DeliveryError(AgentError):
    """Đã cấu hình kênh gửi nhưng không gửi được qua kênh nào."""


def slot_time(kind: str, now: datetime) -> datetime:
    """Giờ gửi danh nghĩa của phiên `kind` trong ngày của `now`."""
    h, m = SLOTS[kind]
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


def seconds_until_slot(session: Session, now: datetime) -> float:
    return max(0.0, (slot_time(session.kind, session.now) - now).total_seconds())


def in_window(kind: str, now: datetime) -> bool:
    """`now` có nằm trong khung được phép tạo bản tin của phiên `kind` không?

    Khung = [giờ gửi − MAX_HOLD_MINUTES, giờ gửi + MAX_LATE_HOURS]. Với mặc định:
    sáng 05:45–13:00, chiều 16:45–24:00 — hai khung không chồng nhau."""
    slot = slot_time(kind, now)
    return slot - timedelta(minutes=MAX_HOLD_MINUTES) <= now <= slot + timedelta(hours=MAX_LATE_HOURS)


def scheduled_session(now: datetime, requested: str | None = None) -> str | None:
    """Phiên mà một lần chạy theo lịch nên làm lúc `now`; None = không có gì để làm.

    Không dựa vào việc lịch khởi động đúng giờ: lần chạy tới lúc nào thì xét khung giờ lúc đó."""
    for kind in ([requested] if requested else list(SLOTS)):
        if in_window(kind, now):
            return kind
    return None


def report_filename(now: datetime, kind: str) -> str:
    return f"{now:%Y-%m-%d}-{kind}.md"


def already_done(settings: Settings, session_kind: str | None = None) -> bool:
    """Bản tin của phiên hôm nay đã được tạo (và lưu vào repo) chưa?"""
    now = datetime.now(settings.timezone)
    kind = session_kind or detect_session(now)
    return (settings.reports_dir / report_filename(now, kind)).exists()


def run(session_kind: str | None = None, send: bool = True, hold: bool = False,
        settings: Settings | None = None, backend: Backend | None = None) -> str:
    settings = settings or Settings()
    now = datetime.now(settings.timezone)
    kind = session_kind or detect_session(now)
    filename = report_filename(now, kind)

    market_data = None
    if settings.use_datafeeds:
        log.info("Đang lấy giá và tin RSS miễn phí...")
        market_data = (datafeeds.collect_data(now, max_items=4, summary_len=140) if settings.lean
                       else datafeeds.collect_data(now))
        if settings.lean and len(market_data.quotes) < MIN_QUOTES:
            # Chế độ gọn không có tìm kiếm web: thiếu bảng giá thì model chỉ còn cách đoán số.
            raise AgentError(f"Chỉ lấy được {len(market_data.quotes)} mã giá (cần ≥ {MIN_QUOTES}) — "
                             "không tạo bản tin để tránh số liệu bịa.")
    stem = filename.removesuffix(".md")
    session = Session(kind=kind, now=now, previous_report=latest_previous_report(settings, filename),
                      market_data=datafeeds.render_data(market_data) if market_data else None,
                      data=market_data,
                      scorecard=tracking.scorecard(settings, market_data, stem) if market_data else "")
    backend = backend or make_backend(settings)
    log.info("Nhà cung cấp AI: %s (model %s)%s", settings.provider, backend.model,
             " — chế độ gọn" if settings.lean else "")
    manager = ManagerAgent(backend, lean=settings.lean)
    report = manager.run(session)

    path = save_report(settings, filename, report)
    if market_data:
        tracking.save_snapshot(settings, stem, market_data, report)
    if send:
        if hold:
            wait = seconds_until_slot(session, datetime.now(settings.timezone))
            if wait > MAX_HOLD_MINUTES * 60:
                log.warning("Còn %.0f phút mới tới %s — quá giới hạn chờ %d phút, gửi ngay.",
                            wait / 60, session.slot, MAX_HOLD_MINUTES)
            elif wait > 0:
                log.info("Báo cáo xong sớm, chờ %.0f giây tới %s để gửi", wait, session.slot)
                time.sleep(wait)
        sent = deliver(settings, session.title, report)
        if not sent and configured_channels(settings):
            # Xoá file để lần chạy dự phòng (--skip-if-done) không tưởng là đã gửi xong.
            path.unlink(missing_ok=True)
            raise DeliveryError("Đã tạo bản tin nhưng không gửi được qua kênh nào "
                                f"({', '.join(configured_channels(settings))}).")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hệ thống agent phân tích thị trường")
    parser.add_argument("--session", choices=["morning", "evening"], help="mặc định: tự chọn theo giờ")
    parser.add_argument("--no-send", action="store_true", help="không gửi Telegram/email")
    parser.add_argument("--hold", action="store_true", help="chờ tới đúng 07:00/18:00 rồi mới gửi")
    parser.add_argument("--skip-if-done", action="store_true",
                        help="bỏ qua nếu bản tin của phiên này hôm nay đã có (dùng cho các lần chạy dự phòng)")
    parser.add_argument("--scheduled", action="store_true",
                        help="dùng cho lịch tự động: tự chọn phiên theo khung giờ hiện tại, bỏ qua nếu "
                             "quá sớm / quá trễ / đã gửi; bao gồm --hold và --skip-if-done")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings()
    session_kind, hold, skip_if_done = args.session, args.hold, args.skip_if_done
    if args.scheduled:
        now = datetime.now(settings.timezone)
        session_kind = scheduled_session(now, args.session)
        if session_kind is None:
            logging.info("%s: ngoài khung giờ tạo bản tin%s — không làm gì.", f"{now:%H:%M}",
                         f" phiên {args.session}" if args.session else "")
            return 0
        hold = skip_if_done = True
    if skip_if_done and already_done(settings, session_kind):
        logging.info("Bản tin của phiên này hôm nay đã được gửi — bỏ qua lần chạy dự phòng.")
        return 0
    try:
        report = run(session_kind, send=not args.no_send, hold=hold, settings=settings)
    except AgentError as exc:
        logging.error("%s", exc)
        return 1
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
