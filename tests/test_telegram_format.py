from market_agents.config import Settings
from market_agents.delivery import pack_sections, send_telegram
from market_agents.telegram_format import DIVIDER, to_telegram_html

REPORT = """# Bản tin sáng 07:00 — ngày 02/11/2026

## 📌 Tóm tắt nhanh
- **USD:** mạnh lên sau CPI <cao> & bất ngờ
- **Vàng:** giảm nhẹ, giữ trên 4.000 USD ([Kitco](https://kitco.com/a?x=1&y=2))

## 🎯 Nhận định
### 🎯 Bảng sức mạnh
| Tài sản | Điểm | Ngắn hạn | Trung hạn |
|---|---|---|---|
| **USD** | +2 | ↑ | → |
| Vàng | -1 | ↓ | — |

### 💵 USD — Điểm +2
- **Lý do 1:** lợi suất 2Y tăng
  - chi tiết phụ
-
---
_Báo cáo do hệ thống AI tạo._
"""


def test_markdown_to_telegram_html():
    out = to_telegram_html(REPORT)
    assert out.startswith("<b>BẢN TIN SÁNG 07:00 — NGÀY 02/11/2026</b>")
    assert f"{DIVIDER}\n<b>📌 Tóm tắt nhanh</b>" in out
    assert "• <b>USD:</b> mạnh lên sau CPI &lt;cao&gt; &amp; bất ngờ" in out
    assert '<a href="https://kitco.com/a?x=1&amp;y=2">Kitco</a>' in out
    assert "• <b>USD</b> — Điểm: +2 · Ngắn hạn: ↑ · Trung hạn: →" in out
    assert "• <b>Vàng</b> — Điểm: -1 · Ngắn hạn: ↓" in out      # ô "—" bị bỏ
    assert "   ◦ chi tiết phụ" in out and "\n-\n" not in out and "<b><b>" not in out
    assert "<i>Báo cáo do hệ thống AI tạo.</i>" in out
    assert "|" not in out and "#" not in out and "**" not in out and "---" not in out


def test_pack_sections_keeps_sections_whole():
    sections = [f"{DIVIDER}\n<b>Phần {i}</b>\n" + "• dòng\n" * 40 for i in range(6)]
    chunks = pack_sections("TIÊU ĐỀ\n" + "\n".join(sections), limit=600)
    assert all(len(c) <= 600 for c in chunks)
    for c in chunks[1:]:
        assert c.startswith(DIVIDER)


def test_send_telegram_falls_back_to_plain_text():
    import io
    import urllib.error

    sent = []

    def post(url, payload):
        sent.append(payload)
        if payload.get("parse_mode") == "HTML":
            raise urllib.error.HTTPError(url, 400, "Bad Request", {}, io.BytesIO(b""))

    settings = Settings(telegram_bot_token="t", telegram_chat_id="1")
    assert send_telegram(settings, "## Tiêu đề\n- **a** & b", post=post)
    assert sent[1]["text"] == "━━━━━━━━━━━━━━\nTiêu đề\n• a & b" and "parse_mode" not in sent[1]
