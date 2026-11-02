"""Nguồn dữ liệu miễn phí (không cần AI): giá thị trường + tin tức RSS.

Dữ liệu này được đưa cho Agent Thu thập làm nền. Nhờ vậy hệ thống vẫn chạy được khi
công cụ tìm kiếm web của nhà cung cấp AI không khả dụng (ví dụ gói Gemini miễn phí),
và giảm số lượt tìm kiếm cần dùng.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from xml.etree import ElementTree

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; market-agents/1.0)"
TIMEOUT = 20

# (Tên hiển thị, mã Yahoo Finance)
PRICE_SYMBOLS: list[tuple[str, str]] = [
    ("DXY (chỉ số USD)", "DX-Y.NYB"),
    ("EUR/USD", "EURUSD=X"),
    ("USD/JPY", "JPY=X"),
    ("USD/CNH", "CNH=X"),
    ("USD/VND", "VND=X"),
    ("Vàng (XAU, futures)", "GC=F"),
    ("Bạc (futures)", "SI=F"),
    ("Bitcoin (BTC/USD)", "BTC-USD"),
    ("Ethereum (ETH/USD)", "ETH-USD"),
    ("Dầu Brent", "BZ=F"),
    ("Dầu WTI", "CL=F"),
    ("Khí tự nhiên", "NG=F"),
    ("Đồng (copper)", "HG=F"),
    ("Lúa mì", "ZW=F"),
    ("Cà phê Arabica", "KC=F"),
    ("S&P 500", "^GSPC"),
    ("Nasdaq", "^IXIC"),
    ("Lợi suất US 10Y (%)", "^TNX"),
    ("VIX", "^VIX"),
]

# Chỉ báo dòng tiền (gián tiếp): quỹ ETF đại diện từng nhóm tài sản.
# (Tên hiển thị, mã Yahoo Finance, khu vực)
FLOW_SYMBOLS: list[tuple[str, str, str]] = [
    ("Cổ phiếu Mỹ (SPY)", "SPY", "world"),
    ("Công nghệ Mỹ (QQQ)", "QQQ", "world"),
    ("Cổ phiếu châu Âu (VGK)", "VGK", "world"),
    ("Thị trường mới nổi (EEM)", "EEM", "world"),
    ("Trái phiếu Mỹ dài hạn (TLT)", "TLT", "world"),
    ("Trái phiếu lợi suất cao (HYG)", "HYG", "world"),
    ("Vàng (GLD)", "GLD", "world"),
    ("Bitcoin ETF (IBIT)", "IBIT", "world"),
    ("Dầu (USO)", "USO", "world"),
    ("Ngành công nghệ (XLK)", "XLK", "world"),
    ("Ngành tài chính (XLF)", "XLF", "world"),
    ("Ngành năng lượng (XLE)", "XLE", "world"),
    ("Ngành phòng thủ - tiện ích (XLU)", "XLU", "world"),
    ("Quỹ ETF Việt Nam (VNM, niêm yết Mỹ)", "VNM", "vn"),
    ("VN-Index", "^VNINDEX.VN", "vn"),
    ("VN30", "^VN30.VN", "vn"),
]

# (Tên nguồn, URL RSS/Atom). Nguồn lỗi sẽ được bỏ qua.
NEWS_FEEDS: list[tuple[str, str]] = [
    ("CNBC Economy", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("CNBC Finance", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("Investing.com Economy", "https://www.investing.com/rss/news_14.rss"),
    ("Investing.com Forex", "https://www.investing.com/rss/news_1.rss"),
    ("Investing.com Commodities", "https://www.investing.com/rss/news_11.rss"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("OilPrice", "https://oilprice.com/rss/main"),
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("VnExpress Kinh doanh", "https://vnexpress.net/rss/kinh-doanh.rss"),
    ("CafeF Tài chính quốc tế", "https://cafef.vn/tai-chinh-quoc-te.rss"),
    # Chứng khoán Việt Nam: khối ngoại, thanh khoản, nhóm ngành hút tiền
    ("CafeF Chứng khoán", "https://cafef.vn/thi-truong-chung-khoan.rss"),
    ("VnExpress Chứng khoán", "https://vnexpress.net/rss/kinh-doanh/chung-khoan.rss"),
    ("Vietstock Chứng khoán", "https://vietstock.vn/830/chung-khoan/co-phieu.rss"),
]
MAX_ITEMS_PER_FEED = 10


@dataclass
class Quote:
    name: str
    symbol: str
    price: float
    change_pct: float | None
    time: datetime | None


@dataclass
class FlowStat:
    name: str
    symbol: str
    region: str  # "world" | "vn"
    change_1d: float | None
    change_5d: float | None
    volume_ratio: float | None  # khối lượng phiên gần nhất / trung bình 20 phiên trước đó


@dataclass
class NewsItem:
    source: str
    title: str
    link: str
    published: datetime | None
    summary: str


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


# ---------------------------------------------------------------- giá
def parse_yahoo_chart(name: str, symbol: str, payload: dict) -> Quote | None:
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return None
    meta = result[0].get("meta") or {}
    price = meta.get("regularMarketPrice")
    if price is None:
        return None
    closes = [
        c for c in (((result[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
        if c is not None
    ]
    prev = meta.get("previousClose")
    if prev is None and len(closes) >= 2:
        prev = closes[-2]
    change = (price / prev - 1) * 100 if prev else None
    ts = meta.get("regularMarketTime")
    when = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
    return Quote(name, symbol, float(price), change, when)


def fetch_quote(name: str, symbol: str) -> Quote | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(symbol)}?range=5d&interval=1d"
    try:
        return parse_yahoo_chart(name, symbol, json.loads(_get(url)))
    except Exception as exc:  # noqa: BLE001 - nguồn miễn phí, lỗi thì bỏ qua
        log.warning("Không lấy được giá %s: %s", symbol, exc)
        return None


# ---------------------------------------------------------------- dòng tiền
def parse_flow(name: str, symbol: str, region: str, payload: dict) -> FlowStat | None:
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return None
    quote = ((result[0].get("indicators") or {}).get("quote") or [{}])[0]
    pairs = [(c, v) for c, v in zip(quote.get("close") or [], quote.get("volume") or []) if c is not None]
    if len(pairs) < 2:
        return None
    closes = [c for c, _ in pairs]
    volumes = [v for _, v in pairs if v]
    change_1d = (closes[-1] / closes[-2] - 1) * 100
    change_5d = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else None
    volume_ratio = None
    if len(volumes) >= 6 and pairs[-1][1]:
        base = volumes[-21:-1]
        avg = sum(base) / len(base)
        volume_ratio = pairs[-1][1] / avg if avg else None
    return FlowStat(name, symbol, region, change_1d, change_5d, volume_ratio)


def fetch_flow(name: str, symbol: str, region: str) -> FlowStat | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(symbol)}?range=2mo&interval=1d"
    try:
        return parse_flow(name, symbol, region, json.loads(_get(url)))
    except Exception as exc:  # noqa: BLE001
        log.warning("Không lấy được dữ liệu dòng tiền %s: %s", symbol, exc)
        return None


# ---------------------------------------------------------------- tin tức
_TAG = re.compile(r"<[^>]+>")


def _clean(text: str | None, limit: int = 300) -> str:
    text = unescape(_TAG.sub(" ", text or ""))
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_feed(source: str, data: bytes) -> list[NewsItem]:
    root = ElementTree.fromstring(data)
    items: list[NewsItem] = []
    for el in root.iter():
        kind = _local(el.tag)
        if kind not in ("item", "entry"):
            continue
        fields: dict[str, str] = {}
        for child in el:
            name = _local(child.tag)
            if name == "link" and child.get("href"):
                fields.setdefault("link", child.get("href", ""))
            elif child.text:
                fields.setdefault(name, child.text)
        title = _clean(fields.get("title"), 200)
        if not title:
            continue
        items.append(NewsItem(
            source=source,
            title=title,
            link=(fields.get("link") or "").strip(),
            published=_parse_date(fields.get("pubDate") or fields.get("published") or fields.get("updated")
                                  or fields.get("date")),
            summary=_clean(fields.get("description") or fields.get("summary") or fields.get("encoded")),
        ))
    return items


def fetch_feed(source: str, url: str, since: datetime) -> list[NewsItem]:
    try:
        items = parse_feed(source, _get(url))
    except Exception as exc:  # noqa: BLE001
        log.warning("Không đọc được RSS %s: %s", source, exc)
        return []
    fresh = [i for i in items if i.published is None or i.published >= since]
    fresh.sort(key=lambda i: i.published or since, reverse=True)
    return fresh[:MAX_ITEMS_PER_FEED]


# ---------------------------------------------------------------- tổng hợp
def collect(now: datetime, hours: int = 14, max_items: int = MAX_ITEMS_PER_FEED,
            summary_len: int = 300) -> str:
    """Lấy giá + tin trong `hours` giờ gần nhất, trả về Markdown cho agent đọc."""
    since = now.astimezone(timezone.utc) - timedelta(hours=hours)
    with ThreadPoolExecutor(max_workers=12) as pool:
        quote_futs = [pool.submit(fetch_quote, n, s) for n, s in PRICE_SYMBOLS]
        flow_futs = [pool.submit(fetch_flow, n, s, r) for n, s, r in FLOW_SYMBOLS]
        news_futs = [pool.submit(fetch_feed, n, u, since) for n, u in NEWS_FEEDS]
        quotes = [q for f in quote_futs if (q := f.result())]
        flows = [x for f in flow_futs if (x := f.result())]
        news = [i for f in news_futs for i in f.result()[:max_items]]
    for item in news:
        if len(item.summary) > summary_len:
            item.summary = item.summary[: summary_len - 1] + "…"
    return render(quotes, news, now, flows)


def _fmt(price: float) -> str:
    if abs(price) >= 1000:
        return f"{price:,.2f}"
    if abs(price) >= 1:
        return f"{price:.4f}".rstrip("0").rstrip(".")
    return f"{price:.6f}"


def _pct(value: float | None) -> str:
    return f"{value:+.1f}%" if value is not None else "—"


def render_flows(flows: list[FlowStat]) -> list[str]:
    lines = ["", "### Chỉ báo dòng tiền (gián tiếp, qua quỹ ETF / chỉ số — Yahoo Finance)",
             "Giá tăng kèm khối lượng cao hơn trung bình thường cho thấy tiền đang vào; giảm kèm "
             "khối lượng cao: tiền đang rút. Đây KHÔNG phải số liệu dòng vốn thực."]
    for region, title in (("world", "Thế giới"), ("vn", "Việt Nam")):
        rows = [f for f in flows if f.region == region]
        if not rows:
            lines.append(f"- {title}: không lấy được dữ liệu.")
            continue
        rows.sort(key=lambda f: f.change_5d if f.change_5d is not None else -999, reverse=True)
        lines += [f"**{title}** (xếp theo % 5 ngày)", "| Nhóm | 1 ngày | 5 ngày | KL / TB20 |", "|---|---|---|---|"]
        for f in rows:
            vol = f"{f.volume_ratio:.1f}x" if f.volume_ratio is not None else "—"
            lines.append(f"| {f.name} | {_pct(f.change_1d)} | {_pct(f.change_5d)} | {vol} |")
    return lines


def render(quotes: list[Quote], news: list[NewsItem], now: datetime,
           flows: list[FlowStat] | None = None) -> str:
    tz = now.tzinfo
    lines = [f"Dữ liệu tự động lấy lúc {now:%d/%m/%Y %H:%M} (giờ VN).", "", "### Giá thị trường (Yahoo Finance)"]
    if quotes:
        lines += ["| Tài sản | Giá | % so với phiên trước | Thời điểm (giờ VN) |", "|---|---|---|---|"]
        for q in quotes:
            change = f"{q.change_pct:+.2f}%" if q.change_pct is not None else "—"
            when = q.time.astimezone(tz).strftime("%d/%m %H:%M") if q.time else "—"
            lines.append(f"| {q.name} | {_fmt(q.price)} | {change} | {when} |")
    else:
        lines.append("(Không lấy được dữ liệu giá.)")
    if flows is not None:
        lines += render_flows(flows)
    lines += ["", f"### Tin tức RSS ({len(news)} tin)"]
    for i in sorted(news, key=lambda i: i.published or now, reverse=True):
        when = i.published.astimezone(tz).strftime("%d/%m %H:%M") if i.published else "?"
        lines.append(f"- [{when}] **{i.title}** — {i.summary} ({i.source}, {i.link})")
    if not news:
        lines.append("(Không lấy được tin RSS.)")
    return "\n".join(lines)


def check_sources() -> int:
    """Kiểm tra từng nguồn dữ liệu miễn phí (không gọi AI). In bảng OK/LỖI."""
    since = datetime.now(timezone.utc) - timedelta(hours=48)
    rows: list[tuple[str, str, str]] = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        q = {pool.submit(fetch_quote, n, s): (n, s) for n, s in PRICE_SYMBOLS}
        fl = {pool.submit(fetch_flow, n, s, r): (n, s) for n, s, r in FLOW_SYMBOLS}
        nf = {pool.submit(fetch_feed, n, u, since): (n, u) for n, u in NEWS_FEEDS}
        for fut, (n, s) in q.items():
            rows.append(("Giá", f"{n} ({s})", "OK" if fut.result() else "LỖI"))
        for fut, (n, s) in fl.items():
            r = fut.result()
            rows.append(("Dòng tiền", f"{n} ({s})",
                         f"OK 5 ngày {_pct(r.change_5d)}, KL {r.volume_ratio or 0:.1f}x" if r else "LỖI"))
        for fut, (n, u) in nf.items():
            items = fut.result()
            rows.append(("Tin RSS", n, f"OK {len(items)} tin/48h" if items else "LỖI hoặc không có tin"))
    for kind, name, status in rows:
        print(f"{status[:3]:4} {kind:10} {name}  {status}", flush=True)
    lines = ["## Kiểm tra nguồn dữ liệu miễn phí", "", "| Loại | Nguồn | Kết quả |", "|---|---|---|"]
    lines += [f"| {k} | {n} | {s} |" for k, n, s in rows]
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    raise SystemExit(check_sources())
