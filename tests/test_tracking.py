"""Kiểm thử phần chấm lại nhận định kỳ trước (không gọi mạng, không gọi AI)."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from market_agents import tracking
from market_agents.config import Settings
from market_agents.datafeeds import MarketData, Quote

NOW = datetime(2026, 11, 3, 6, 35, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))

TABLE = """### 🎯 Bảng sức mạnh
| Tài sản | Điểm (‑5..+5) | Ngắn hạn | Trung hạn |
|---|---|---|---|
| **USD** | +2 | ↑ nhẹ | ↑ |
| 🥇 Vàng | **+3** | ↑ mạnh | ↑ |
| ₿ Bitcoin | −2 | ↓ | ↓ |
| **Hàng hoá** |  |  |  |
| - Dầu | ‑3 | ↓ mạnh | ↔︎ |
| - Đồng (copper) | 0 | ↔ | ↔ |
| - Nông sản | 0 | ↔︎ | ↔︎ |
## 🧮 Đối chiếu nhận định kỳ trước
| Bạc | +5 | +1.00% | ✅ đúng chiều |
## 💵 Bảng giá
| Bạc (futures) | 51.59 | -1.85% | 11/11 07:13 |
"""


def data(**prices):
    return MarketData(now=NOW, quotes=[Quote(s, s, p, None, None) for s, p in prices.items()])


def test_extract_scores_from_strength_table_only():
    assert tracking.extract_scores(TABLE) == {
        "DX-Y.NYB": 2, "GC=F": 3, "BTC-USD": -2, "BZ=F": -3, "HG=F": 0}


def test_judge():
    assert tracking.judge(2, 0.5) == "đúng" and tracking.judge(2, -0.5) == "sai"
    assert tracking.judge(-3, -4.0) == "đúng" and tracking.judge(-3, 0.1) == "đi ngang"
    assert tracking.judge(0, 5.0) == "trung tính"


def test_scorecard_compares_previous_scores_with_price_moves(tmp_path):
    settings = Settings(reports_dir=tmp_path, use_datafeeds=False)
    assert tracking.scorecard(settings, data(**{"GC=F": 4000.0}), "2026-11-03-morning") == ""

    tracking.save_snapshot(settings, "2026-11-02-morning", data(**{"DX-Y.NYB": 100.0, "GC=F": 4000.0}), TABLE)
    tracking.save_snapshot(settings, "2026-11-02-evening",
                           data(**{"DX-Y.NYB": 101.0, "GC=F": 3900.0, "BTC-USD": 70000.0}), TABLE)
    card = tracking.scorecard(settings, data(**{"DX-Y.NYB": 100.5, "GC=F": 3978.0, "BTC-USD": 68000.0}),
                              "2026-11-03-morning")
    assert "- **USD (DXY):** điểm +2 → giá -0.50% ❌ sai chiều" in card
    assert "- **Vàng:** điểm +3 → giá +2.00% ✅ đúng chiều" in card
    assert "- **Bitcoin:** điểm -2 → giá -2.86% ✅ đúng chiều" in card
    # Lịch sử: sáng 02/11 -> chiều 02/11 (USD đúng, vàng sai) + chiều 02/11 -> nay (USD sai, vàng đúng, BTC đúng)
    assert "3/5 (60%)" in card and "bản tin chiều 02/11" in card
    # Phiên đang chạy lại (đã có ảnh chụp của chính nó) không tự chấm mình.
    assert "chiều 02/11" not in tracking.scorecard(settings, data(**{"GC=F": 1.0, "DX-Y.NYB": 1.0}), "2026-11-02-evening")
