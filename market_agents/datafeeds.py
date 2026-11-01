"""Nguồn dữ liệu miễn phí (không cần AI): giá thị trường + tin tức RSS.

Dữ liệu này được đưa cho Agent Thu thập làm nền. Nhờ vậy hệ thống vẫn chạy được khi
công cụ tìm kiếm web của nhà cung cấp AI không khả dụng (ví dụ gói Gemini miễn phí),
và giảm số lượt tìm kiếm cần dùng.
"""
from __future__ import annotations

import json
import logging
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
        news_futs = [pool.submit(fetch_feed, n, u, since) for n, u in NEWS_FEEDS]
        quotes = [q for f in quote_futs if (q := f.result())]
        news = [i for f in news_futs for i in f.result()[:max_items]]
    for item in news:
        if len(item.summary) > summary_len:
            item.summary = item.summary[: summary_len - 1] + "…"
    return render(quotes, news, now)


def _fmt(price: float) -> str:
    if abs(price) >= 1000:
        return f"{price:,.2f}"
    if abs(price) >= 1:
        return f"{price:.4f}".rstrip("0").rstrip(".")
    return f"{price:.6f}"


def render(quotes: list[Quote], news: list[NewsItem], now: datetime) -> str:
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
    lines += ["", f"### Tin tức RSS ({len(news)} tin)"]
    for i in sorted(news, key=lambda i: i.published or now, reverse=True):
        when = i.published.astimezone(tz).strftime("%d/%m %H:%M") if i.published else "?"
        lines.append(f"- [{when}] **{i.title}** — {i.summary} ({i.source}, {i.link})")
    if not news:
        lines.append("(Không lấy được tin RSS.)")
    return "\n".join(lines)
