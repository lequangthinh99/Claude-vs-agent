"""Bộ lập lịch chạy liên tục trên máy/VPS của bạn (thay cho GitHub Actions).

    python -m market_agents.scheduler

Bắt đầu tạo báo cáo sớm hơn giờ gửi (mặc định 25 phút) rồi giữ lại tới đúng 07:00 / 18:00 mới gửi.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta

from .config import Settings
from .main import run

log = logging.getLogger(__name__)

DELIVERY_TIMES = {"morning": (7, 0), "evening": (18, 0)}
LEAD_MINUTES = int(os.getenv("MARKET_AGENTS_LEAD_MINUTES", "25"))


def next_run(now: datetime) -> tuple[datetime, str]:
    candidates = []
    for kind, (h, m) in DELIVERY_TIMES.items():
        start = now.replace(hour=h, minute=m, second=0, microsecond=0) - timedelta(minutes=LEAD_MINUTES)
        if start <= now:
            start += timedelta(days=1)
        candidates.append((start, kind))
    return min(candidates)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    while True:
        start, kind = next_run(datetime.now(settings.timezone))
        log.info("Phiên tiếp theo: %s, bắt đầu lúc %s", kind, start.isoformat())
        time.sleep(max(0.0, (start - datetime.now(settings.timezone)).total_seconds()))
        try:
            run(kind, send=True, hold=True, settings=settings)
        except Exception:  # noqa: BLE001 - không để daemon dừng vì một phiên lỗi
            log.exception("Phiên %s thất bại", kind)


if __name__ == "__main__":
    main()
