"""Kiểm thử nguồn dữ liệu miễn phí (không gọi mạng)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from market_agents import datafeeds

VN = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 11, 2, 6, 35, tzinfo=VN)

YAHOO = {"chart": {"result": [{
    "meta": {"regularMarketPrice": 4100.0, "previousClose": 4000.0, "regularMarketTime": 1793570000},
    "indicators": {"quote": [{"close": [3990.0, 4000.0, None, 4100.0]}]},
}]}}

RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Fed holds rates</title><link>https://a.com/1</link>
<pubDate>Sun, 01 Nov 2026 22:00:00 GMT</pubDate><description>&lt;p&gt;Powell says &amp;amp; more&lt;/p&gt;</description></item>
<item><title>Old news</title><link>https://a.com/0</link><pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>BTC jumps</title><link href="https://b.com/x"/><updated>2026-11-01T23:30:00Z</updated>
<summary>Bitcoin rose 5%</summary></entry></feed>"""


def test_parse_yahoo():
    q = datafeeds.parse_yahoo_chart("Vàng", "GC=F", YAHOO)
    assert q.price == 4100.0 and round(q.change_pct, 2) == 2.5


def test_parse_feeds_and_filter(monkeypatch):
    since = datetime(2026, 11, 1, 16, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(datafeeds, "_get", lambda url: RSS)
    items = datafeeds.fetch_feed("A", "u", since)
    assert [i.title for i in items] == ["Fed holds rates"]
    assert items[0].summary == "Powell says & more"
    atom = datafeeds.parse_feed("B", ATOM)
    assert atom[0].link == "https://b.com/x" and atom[0].published.hour == 23


def test_collect_renders_and_tolerates_failures(monkeypatch):
    def fake_get(url):
        if "yahoo" in url:
            if "GC%3DF" in url or "GC=F" in url:
                return json.dumps(YAHOO).encode()
            raise OSError("blocked")
        if "coindesk" in url:
            return ATOM
        raise OSError("blocked")

    monkeypatch.setattr(datafeeds, "_get", fake_get)
    text = datafeeds.collect(NOW)
    assert "| Vàng (XAU, futures) | 4,100.00 | +2.50% |" in text
    assert "BTC jumps" in text and "https://b.com/x" in text


def test_parse_flow_and_render():
    payload = {"chart": {"result": [{"indicators": {"quote": [{
        "close": [100, 101, None, 102, 103, 104, 105, 110],
        "volume": [1000, 1000, None, 1000, 1000, 1000, 1000, 2000],
    }]}}]}}
    f = datafeeds.parse_flow("Vàng (GLD)", "GLD", "world", payload)
    assert round(f.change_1d, 2) == 4.76 and round(f.change_5d, 2) == round((110 / 101 - 1) * 100, 2)
    assert f.volume_ratio == 2.0
    weak = datafeeds.FlowStat("TLT", "TLT", "world", -0.5, -2.0, 0.8)
    lines = "\n".join(datafeeds.render_flows([weak, f]))
    assert lines.index("Vàng (GLD)") < lines.index("TLT")        # xếp theo % 5 ngày
    assert "| Vàng (GLD) | +4.8% | +8.9% | 2.0x |" in lines
    assert "Việt Nam: không lấy được dữ liệu" in lines
