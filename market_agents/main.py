"""Điểm chạy chính.

    python -m market_agents.main                  # tự chọn phiên sáng/chiều theo giờ VN
    python -m market_agents.main --session morning
    python -m market_agents.main --hold           # tạo xong thì chờ tới đúng 07:00/18:00 mới gửi
    python -m market_agents.main --no-send        # chỉ tạo và lưu file, không gửi
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime

from . import datafeeds
from .backends import make_backend
from .config import Settings
from .delivery import deliver, latest_previous_report, save_report
from .llm import AgentError, Backend
from .manager import ManagerAgent, Session

log = logging.getLogger(__name__)


def detect_session(now: datetime) -> str:
    """Trước 12:00 → phiên sáng; từ 12:00 → phiên chiều."""
    return "morning" if now.hour < 12 else "evening"


def seconds_until_slot(session: Session, now: datetime) -> float:
    h, m = map(int, session.slot.split(":"))
    target = session.now.replace(hour=h, minute=m, second=0, microsecond=0)
    return max(0.0, (target - now).total_seconds())


def run(session_kind: str | None = None, send: bool = True, hold: bool = False,
        settings: Settings | None = None, backend: Backend | None = None) -> str:
    settings = settings or Settings()
    now = datetime.now(settings.timezone)
    kind = session_kind or detect_session(now)
    filename = f"{now:%Y-%m-%d}-{kind}.md"

    market_data = None
    if settings.use_datafeeds:
        log.info("Đang lấy giá và tin RSS miễn phí...")
        market_data = (datafeeds.collect(now, max_items=4, summary_len=140) if settings.lean
                       else datafeeds.collect(now))
    session = Session(kind=kind, now=now, previous_report=latest_previous_report(settings, filename),
                      market_data=market_data)
    backend = backend or make_backend(settings)
    log.info("Nhà cung cấp AI: %s (model %s)%s", settings.provider, backend.model,
             " — chế độ gọn" if settings.lean else "")
    manager = ManagerAgent(backend, lean=settings.lean)
    report = manager.run(session)

    save_report(settings, filename, report)
    if send:
        if hold:
            wait = seconds_until_slot(session, datetime.now(settings.timezone))
            if wait > 0:
                log.info("Báo cáo xong sớm, chờ %.0f giây tới %s để gửi", wait, session.slot)
                time.sleep(wait)
        deliver(settings, session.title, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hệ thống agent phân tích thị trường")
    parser.add_argument("--session", choices=["morning", "evening"], help="mặc định: tự chọn theo giờ")
    parser.add_argument("--no-send", action="store_true", help="không gửi Telegram/email")
    parser.add_argument("--hold", action="store_true", help="chờ tới đúng 07:00/18:00 rồi mới gửi")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        report = run(args.session, send=not args.no_send, hold=args.hold)
    except AgentError as exc:
        logging.error("%s", exc)
        return 1
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
