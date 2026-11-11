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
from dataclasses import dataclass, field
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
    # Chứng khoán Việt Nam: VN-Index, khối ngoại, thanh khoản, nhóm ngành hút tiền
    # (Yahoo không có VN-Index/VN30 nên lấy các số liệu này từ tin của CafeF, Vietstock)
    ("CafeF Chứng khoán", "https://cafef.vn/thi-truong-chung-khoan.rss"),
    ("Vietstock Chứng khoán", "https://vietstock.vn/830/chung-khoan/co-phieu.rss"),
]
MAX_ITEMS_PER_FEED = 10

# Chỉ số chứng khoán Việt Nam (Yahoo không có) — API biểu đồ công khai của DNSE/Entrade.
ENTRADE_URL = "https://services.entrade.com.vn/chart-api/v2/ohlcs/index?from={start}&to={end}&symbol={symbol}&resolution=1D"
VN_INDEXES: list[tuple[str, str]] = [("VN-Index", "VNINDEX"), ("VN30", "VN30")]

# Lịch kinh tế trong tuần (ForexFactory). Chỉ lấy sự kiện của các nền kinh tế lớn.
CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CALENDAR_COUNTRIES = {"USD": "Mỹ", "EUR": "Eurozone", "CNY": "Trung Quốc", "JPY": "Nhật", "GBP": "Anh"}
CALENDAR_HOURS = 36
MAX_EVENTS = 12

TROY_OUNCES_PER_LUONG = 1.20565  # 1 lượng = 37,5 g


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
    ref: str = ""  # mã trích dẫn (T1, T2, ...) để agent ghi nguồn; code đổi lại thành link thật


@dataclass
class CalendarEvent:
    time: datetime
    country: str   # mã tiền tệ: USD, EUR, ...
    title: str
    impact: str    # "High" | "Medium"
    forecast: str
    previous: str


@dataclass
class MarketData:
    """Dữ liệu lấy bằng code cho một phiên. Bảng trong báo cáo được dựng thẳng từ đây, không qua AI."""

    now: datetime
    quotes: list[Quote] = field(default_factory=list)
    flows: list[FlowStat] = field(default_factory=list)
    news: list[NewsItem] = field(default_factory=list)
    events: list[CalendarEvent] = field(default_factory=list)
    calendar_ok: bool = False  # lấy được lịch kinh tế (kể cả khi không có sự kiện nào sắp tới)

    def price(self, symbol: str) -> float | None:
        return next((q.price for q in self.quotes if q.symbol == symbol), None)

    def links(self) -> dict[str, NewsItem]:
        return {i.ref: i for i in self.news if i.ref}


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


def parse_entrade(name: str, symbol: str, payload: dict) -> Quote | None:
    closes, times = payload.get("c") or [], payload.get("t") or []
    if not closes:
        return None
    change = (closes[-1] / closes[-2] - 1) * 100 if len(closes) >= 2 and closes[-2] else None
    when = datetime.fromtimestamp(times[-1], tz=timezone.utc) if len(times) == len(closes) else None
    return Quote(name, symbol, float(closes[-1]), change, when)


def fetch_vn_index(name: str, symbol: str, now: datetime) -> Quote | None:
    end = int(now.timestamp())
    try:
        url = ENTRADE_URL.format(start=end - 14 * 86400, end=end, symbol=symbol)
        return parse_entrade(name, symbol, json.loads(_get(url)))
    except Exception as exc:  # noqa: BLE001
        log.warning("Không lấy được chỉ số %s: %s", symbol, exc)
        return None


# ---------------------------------------------------------------- lịch kinh tế
def parse_calendar(payload: list, now: datetime, hours: int = CALENDAR_HOURS) -> list[CalendarEvent]:
    """Sự kiện quan trọng trong `hours` giờ tới: mọi sự kiện High, và Medium của Mỹ."""
    until = now + timedelta(hours=hours)
    events = []
    for row in payload:
        country, impact = row.get("country", ""), row.get("impact", "")
        if country not in CALENDAR_COUNTRIES or not (impact == "High" or (impact == "Medium" and country == "USD")):
            continue
        try:
            when = datetime.fromisoformat(row.get("date", ""))
        except ValueError:
            continue
        if when.tzinfo is None or not now <= when <= until:
            continue
        events.append(CalendarEvent(when, country, _clean(row.get("title"), 80), impact,
                                    (row.get("forecast") or "").strip(), (row.get("previous") or "").strip()))
    events.sort(key=lambda e: (e.time, e.impact != "High"))
    return events[:MAX_EVENTS]


def fetch_calendar(now: datetime) -> list[CalendarEvent] | None:
    """None = không lấy được lịch (khác với danh sách rỗng = không có sự kiện quan trọng)."""
    try:
        return parse_calendar(json.loads(_get(CALENDAR_URL)), now)
    except Exception as exc:  # noqa: BLE001
        log.warning("Không lấy được lịch kinh tế: %s", exc)
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
def _title_key(title: str) -> str:
    return re.sub(r"\W+", " ", title.lower()).strip()


def dedupe_news(news: list[NewsItem]) -> list[NewsItem]:
    """Bỏ tin trùng tiêu đề giữa các nguồn, xếp mới nhất trước và gán mã trích dẫn T1, T2, ..."""
    seen: set[str] = set()
    unique = []
    for item in sorted(news, key=lambda i: (i.published is not None, i.published or datetime.min.replace(
            tzinfo=timezone.utc)), reverse=True):
        key = _title_key(item.title)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    for n, item in enumerate(unique, 1):
        item.ref = f"T{n}"
    return unique


def collect_data(now: datetime, hours: int = 14, max_items: int = MAX_ITEMS_PER_FEED,
                 summary_len: int = 300) -> MarketData:
    """Lấy giá, dòng tiền, lịch kinh tế và tin trong `hours` giờ gần nhất."""
    since = now.astimezone(timezone.utc) - timedelta(hours=hours)
    with ThreadPoolExecutor(max_workers=12) as pool:
        quote_futs = [pool.submit(fetch_quote, n, s) for n, s in PRICE_SYMBOLS]
        quote_futs += [pool.submit(fetch_vn_index, n, s, now) for n, s in VN_INDEXES]
        flow_futs = [pool.submit(fetch_flow, n, s, r) for n, s, r in FLOW_SYMBOLS]
        news_futs = [pool.submit(fetch_feed, n, u, since) for n, u in NEWS_FEEDS]
        calendar_fut = pool.submit(fetch_calendar, now)
        quotes = [q for f in quote_futs if (q := f.result())]
        flows = [x for f in flow_futs if (x := f.result())]
        news = [i for f in news_futs for i in f.result()[:max_items]]
        events = calendar_fut.result()
    for item in news:
        if len(item.summary) > summary_len:
            item.summary = item.summary[: summary_len - 1] + "…"
    return MarketData(now=now, quotes=quotes, flows=flows, news=dedupe_news(news),
                      events=events or [], calendar_ok=events is not None)


def collect(now: datetime, hours: int = 14, max_items: int = MAX_ITEMS_PER_FEED,
            summary_len: int = 300) -> str:
    """Như `collect_data` nhưng trả về luôn Markdown cho agent đọc."""
    return render_data(collect_data(now, hours, max_items, summary_len))


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


YIELD_SYMBOLS = {"^TNX"}


def _change(q: Quote) -> str:
    """Thay đổi so với phiên trước. Lợi suất ghi theo điểm % (4,86 → 4,98 là +0,12 điểm, không phải +2,45%)."""
    if q.change_pct is None:
        return ""
    if q.symbol in YIELD_SYMBOLS:
        return f"{q.price - q.price / (1 + q.change_pct / 100):+.2f} điểm %"
    return f"{q.change_pct:+.2f}%"


def gold_vnd_per_luong(data: MarketData) -> float | None:
    """Giá vàng thế giới quy đổi ra triệu đồng/lượng (chưa gồm thuế, phí) — để so với giá trong nước."""
    gold, usdvnd = data.price("GC=F"), data.price("VND=X")
    return gold * usdvnd * TROY_OUNCES_PER_LUONG / 1e6 if gold and usdvnd else None


def render_prices(data: MarketData) -> list[str]:
    """Bảng giá Markdown — dùng cả cho agent đọc lẫn chèn thẳng vào báo cáo."""
    if not data.quotes:
        return []
    tz = data.now.tzinfo
    lines = ["| Tài sản | Giá | % so với phiên trước | Thời điểm (giờ VN) |", "|---|---|---|---|"]
    for q in data.quotes:
        change = _change(q) or "—"
        if not q.time:
            when = "—"
        elif q.symbol in dict((s, n) for n, s in VN_INDEXES):
            when = q.time.astimezone(tz).strftime("phiên %d/%m")
        else:
            when = q.time.astimezone(tz).strftime("%d/%m %H:%M")
        lines.append(f"| {q.name} | {_fmt(q.price)} | {change} | {when} |")
    converted = gold_vnd_per_luong(data)
    if converted:
        lines.append(f"| Vàng thế giới quy đổi (triệu đ/lượng, chưa thuế phí) | {converted:.1f} | — | — |")
    return lines


def render_price_list(data: MarketData) -> list[str]:
    """Bảng giá cho báo cáo: mỗi tài sản 1 dòng ngắn, đọc gọn trên điện thoại (Telegram không có bảng)."""
    if not data.quotes:
        return []
    lines = [f"_Giá lúc {data.now:%H:%M %d/%m} (giờ VN); % so với phiên trước._", ""]
    for q in data.quotes:
        change = f" ({_change(q)})" if _change(q) else ""
        lines.append(f"- **{q.name}:** {_fmt(q.price)}{change}")
    converted = gold_vnd_per_luong(data)
    if converted:
        lines.append(f"- **Vàng thế giới quy đổi:** ≈ {converted:.1f} triệu đ/lượng (chưa thuế, phí)")
    return lines


def render_calendar(data: MarketData) -> list[str]:
    """Lịch sự kiện theo giờ VN, mỗi sự kiện 1 dòng. Rỗng nếu không có sự kiện."""
    tz = data.now.tzinfo
    lines = []
    for e in data.events:
        extra = " · ".join(x for x in (f"dự báo {e.forecast}" if e.forecast else "",
                                       f"kỳ trước {e.previous}" if e.previous else "") if x)
        mark = "🔴" if e.impact == "High" else "🟠"
        lines.append(f"- **{e.time.astimezone(tz):%H:%M %d/%m}** {mark} {CALENDAR_COUNTRIES[e.country]}: {e.title}"
                     + (f" — {extra}" if extra else ""))
    return lines


def render_tables(data: MarketData) -> str:
    """Giá + dòng tiền + lịch kinh tế (không kèm tin) cho agent đọc."""
    lines = [f"Dữ liệu tự động lấy lúc {data.now:%d/%m/%Y %H:%M} (giờ VN).", "",
             "### Giá thị trường (Yahoo Finance; VN-Index/VN30: DNSE)"]
    lines += render_prices(data) or ["(Không lấy được dữ liệu giá.)"]
    lines += render_flows(data.flows)
    lines += ["", f"### Lịch kinh tế {CALENDAR_HOURS} giờ tới (ForexFactory; 🔴 quan trọng cao, 🟠 trung bình)"]
    if data.events:
        lines += render_calendar(data)
    elif data.calendar_ok:
        lines.append("(Không có sự kiện quan trọng.)")
    else:
        lines.append("(Không lấy được lịch — KHÔNG tự nêu sự kiện hay giờ công bố.)")
    return "\n".join(lines)


def render_news(data: MarketData) -> str:
    """Tin RSS cho agent đọc. Không kèm URL (đỡ tốn token): agent trích bằng mã [T..]."""
    tz = data.now.tzinfo
    lines = [f"### Tin tức RSS ({len(data.news)} tin) — trích nguồn bằng mã trong ngoặc vuông, ví dụ [T3]"]
    for i in data.news:
        when = i.published.astimezone(tz).strftime("%d/%m %H:%M") if i.published else "?"
        lines.append(f"- [{i.ref}] [{when}] **{i.title}** — {i.summary} ({i.source})")
    if not data.news:
        lines.append("(Không lấy được tin RSS.)")
    return "\n".join(lines)


def render_data(data: MarketData) -> str:
    return render_tables(data) + "\n\n" + render_news(data)


_REF = re.compile(r"\s*[\[(]+\s*(T\d+(?:\s*[,;]\s*T\d+)*)\s*[\])]+")


def resolve_refs(text: str, data: MarketData | None) -> str:
    """Đổi mã trích dẫn [T3] thành link thật ([Nguồn](URL)); mã không tồn tại thì bỏ."""
    links = data.links() if data else {}

    def swap(match: re.Match) -> str:
        out = []
        for ref in re.findall(r"T\d+", match.group(1)):
            item = links.get(ref)
            if item and item.link.startswith("http"):
                out.append(f"[{item.source}]({item.link})")
            elif item:
                out.append(item.source)
        return f" ({', '.join(out)})" if out else ""

    return _REF.sub(swap, text)


def check_sources() -> int:
    """Kiểm tra từng nguồn dữ liệu miễn phí (không gọi AI). In bảng OK/LỖI."""
    since = datetime.now(timezone.utc) - timedelta(hours=48)
    rows: list[tuple[str, str, str]] = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        q = {pool.submit(fetch_quote, n, s): (n, s) for n, s in PRICE_SYMBOLS}
        fl = {pool.submit(fetch_flow, n, s, r): (n, s) for n, s, r in FLOW_SYMBOLS}
        nf = {pool.submit(fetch_feed, n, u, since): (n, u) for n, u in NEWS_FEEDS}
        now = datetime.now(timezone.utc)
        q.update({pool.submit(fetch_vn_index, n, s, now): (n, s) for n, s in VN_INDEXES})
        cal = pool.submit(fetch_calendar, now)
        for fut, (n, s) in q.items():
            rows.append(("Giá", f"{n} ({s})", "OK" if fut.result() else "LỖI"))
        for fut, (n, s) in fl.items():
            r = fut.result()
            rows.append(("Dòng tiền", f"{n} ({s})",
                         f"OK 5 ngày {_pct(r.change_5d)}, KL {r.volume_ratio or 0:.1f}x" if r else "LỖI"))
        for fut, (n, u) in nf.items():
            items = fut.result()
            rows.append(("Tin RSS", n, f"OK {len(items)} tin/48h" if items else "LỖI hoặc không có tin"))
    events = cal.result()
    rows.append(("Lịch", "Lịch kinh tế (ForexFactory)",
                 f"OK {len(events)} sự kiện/{CALENDAR_HOURS}h" if events is not None else "LỖI"))
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
