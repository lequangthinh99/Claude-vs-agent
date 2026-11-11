"""Chấm lại nhận định kỳ trước bằng giá thực tế (không dùng AI).

Mỗi phiên lưu một ảnh chụp `reports/data/<ngày>-<phiên>.json` gồm giá lúc tạo bản tin và điểm sức mạnh
(−5..+5) mà Agent Nhận định đã chấm. Phiên sau so chiều của điểm với biến động giá thực tế từ đó tới nay.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .datafeeds import MarketData

log = logging.getLogger(__name__)

# (tên hiển thị, mã giá dùng để chấm, từ khoá nhận diện dòng trong bảng sức mạnh)
ASSETS: list[tuple[str, str, tuple[str, ...]]] = [
    ("USD (DXY)", "DX-Y.NYB", ("usd", "dxy")),
    ("Vàng", "GC=F", ("vàng", "xau")),
    ("Bitcoin", "BTC-USD", ("bitcoin", "btc")),
    ("Dầu Brent", "BZ=F", ("dầu",)),
    ("Đồng", "HG=F", ("đồng", "copper")),
    ("Bạc", "SI=F", ("bạc",)),
]
FLAT_PCT = 0.15      # biến động nhỏ hơn mức này coi là đi ngang, không tính đúng/sai
HISTORY = 30         # số phiên gần nhất dùng để tính tỷ lệ đúng
_MINUS = str.maketrans({"−": "-", "‑": "-", "–": "-", "—": "-", "＋": "+"})


def extract_scores(text: str) -> dict[str, int]:
    """Đọc điểm từng tài sản từ bảng sức mạnh trong báo cáo. Trả về {mã giá: điểm}."""
    scores: dict[str, int] = {}
    for line in text.splitlines():
        if not line.strip().startswith("|") or any(mark in line for mark in "✅❌➖"):
            continue  # bỏ qua dòng không phải bảng và bảng đối chiếu kỳ trước (điểm cũ)
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        match = re.fullmatch(r"\**([+-]?\d)\**", cells[1].translate(_MINUS).replace(" ", ""))
        if not match:
            continue
        name = cells[0].lower()
        for _, symbol, keys in ASSETS:
            if symbol not in scores and any(k in name for k in keys):
                scores[symbol] = int(match.group(1))
                break
    return scores


def _dir(settings: Settings) -> Path:
    return settings.reports_dir / "data"


def save_snapshot(settings: Settings, stem: str, data: MarketData, report: str) -> Path | None:
    scores = extract_scores(report)
    prices = {symbol: p for _, symbol, _ in ASSETS if (p := data.price(symbol)) is not None}
    if not prices:
        return None
    _dir(settings).mkdir(parents=True, exist_ok=True)
    path = _dir(settings) / f"{stem}.json"
    path.write_text(json.dumps({"time": data.now.isoformat(), "prices": prices, "scores": scores},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _order(path: Path) -> tuple[str, int]:
    day, _, kind = path.stem.rpartition("-")
    return (day, {"morning": 0, "evening": 1}.get(kind, 2))


def load_snapshots(settings: Settings, exclude_stem: str) -> list[dict]:
    """Các ảnh chụp trước phiên hiện tại, cũ nhất trước."""
    if not _dir(settings).exists():
        return []
    out = []
    for path in sorted((p for p in _dir(settings).glob("*.json") if p.stem != exclude_stem), key=_order):
        try:
            snap = json.loads(path.read_text(encoding="utf-8"))
            snap["stem"] = path.stem
            out.append(snap)
        except (ValueError, OSError):
            log.warning("Bỏ qua ảnh chụp hỏng: %s", path)
    return out


@dataclass
class Verdict:
    name: str
    score: int
    change_pct: float
    result: str  # "đúng" | "sai" | "đi ngang" | "trung tính"


def judge(score: int, change_pct: float) -> str:
    if score == 0:
        return "trung tính"
    if abs(change_pct) < FLAT_PCT:
        return "đi ngang"
    return "đúng" if (score > 0) == (change_pct > 0) else "sai"


def evaluate(snapshot: dict, prices_now: dict[str, float]) -> list[Verdict]:
    verdicts = []
    for name, symbol, _ in ASSETS:
        score, then, now = snapshot.get("scores", {}).get(symbol), snapshot.get("prices", {}).get(symbol), \
            prices_now.get(symbol)
        if score is None or not then or not now:
            continue
        change = (now / then - 1) * 100
        verdicts.append(Verdict(name, score, change, judge(score, change)))
    return verdicts


def scorecard(settings: Settings, data: MarketData, exclude_stem: str) -> str:
    """Mục "Đối chiếu nhận định kỳ trước" (Markdown, không có tiêu đề). Rỗng nếu chưa có gì để chấm."""
    snaps = load_snapshots(settings, exclude_stem)[-HISTORY:]
    if not snaps:
        return ""
    prices_now = {symbol: p for _, symbol, _ in ASSETS if (p := data.price(symbol)) is not None}
    latest = evaluate(snaps[-1], prices_now)
    if not latest:
        return ""
    # Lịch sử: mỗi ảnh chụp được chấm bằng giá của ảnh chụp kế tiếp; ảnh mới nhất chấm bằng giá hiện tại.
    history = [v for a, b in zip(snaps, snaps[1:]) for v in evaluate(a, b.get("prices", {}))] + latest
    hits = sum(v.result == "đúng" for v in history)
    counted = sum(v.result in ("đúng", "sai") for v in history)
    lines = []
    marks = {"đúng": "✅ đúng chiều", "sai": "❌ sai chiều", "đi ngang": "➖ đi ngang", "trung tính": "➖ trung tính"}
    for v in latest:
        lines.append(f"- **{v.name}:** điểm {v.score:+d} → giá {v.change_pct:+.2f}% {marks[v.result]}")
    if counted:
        lines += [f"- **Tỷ lệ đúng chiều ({len(snaps)} phiên gần nhất):** {hits}/{counted} ({hits / counted:.0%})"]
    day, _, kind = snaps[-1]["stem"].rpartition("-")
    label = f"{'sáng' if kind == 'morning' else 'chiều'} {day[8:10]}/{day[5:7]}"
    lines.append(f"- **Cách chấm:** so chiều của điểm ở bản tin {label} với biến động giá từ đó tới nay; "
                 f"dưới {FLAT_PCT}% coi là đi ngang.")
    return "\n".join(lines)
