"""Chuyển báo cáo Markdown sang HTML mà Telegram hiển thị được (parse_mode=HTML).

Telegram không hiển thị Markdown tiêu đề (#) hay bảng (|...|), nên:
- Tiêu đề  -> chữ đậm, có đường kẻ phân cách giữa các phần lớn.
- Bảng      -> mỗi dòng thành một gạch đầu dòng "• Tên — Cột: giá trị · Cột: giá trị".
- **đậm**, _nghiêng_, `code`, [link](url) -> thẻ HTML tương ứng.
Mỗi dòng đầu ra tự đóng đủ thẻ, nên có thể cắt tin nhắn ở ranh giới dòng an toàn.
"""
from __future__ import annotations

import html
import re

DIVIDER = "━━━━━━━━━━━━━━"

_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_FAKE_LINK = re.compile(r"\[([^\]]+)\]\((?!https?://)[^)]*\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])[_*](?![_*\s])(.+?)(?<![\s_*])[_*](?![\w*])")
_CODE = re.compile(r"`([^`]+)`")


def inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = _CODE.sub(r"<code>\1</code>", text)
    text = _LINK.sub(lambda m: f'<a href="{m.group(2).replace(chr(34), "%22")}">{m.group(1)}</a>', text)
    text = _FAKE_LINK.sub(r"\1", text)  # [Nguồn](#) -> Nguồn
    text = _BOLD.sub(r"<b>\1</b>", text)
    text = _ITALIC.sub(r"<i>\1</i>", text)
    return text


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _table(rows: list[str]) -> list[str]:
    rows = [r for r in rows if not re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", r)]  # bỏ dòng ---|---
    if not rows:
        return []
    header, body = _cells(rows[0]), rows[1:]
    if not body:  # bảng chỉ có 1 dòng
        return ["• " + " · ".join(inline(c) for c in header)]
    out = []
    for row in body:
        cells = _cells(row)
        name0 = cells[0].replace("**", "") if cells else ""
        first = f"<b>{inline(name0)}</b>" if name0 else ""
        rest = []
        for name, value in zip(header[1:], cells[1:]):
            if value and value not in ("—", "-"):
                rest.append(f"{inline(name)}: {inline(value)}" if name else inline(value))
        out.append("• " + first + (" — " + " · ".join(rest) if rest else ""))
    return out


def to_telegram_html(markdown: str) -> str:
    out: list[str] = []
    table: list[str] = []

    def flush_table():
        if table:
            out.extend(_table(table))
            table.clear()

    for raw in markdown.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("|"):
            table.append(stripped)
            continue
        flush_table()
        if not stripped:
            if out and out[-1] != "":
                out.append("")
            continue
        if re.fullmatch(r"[-*_]{3,}|[-*+•]", stripped):
            continue  # đường kẻ markdown hoặc gạch đầu dòng rỗng
        heading = re.match(r"(#{1,6})\s+(.*)", stripped)
        if heading:
            level, text = len(heading.group(1)), inline(heading.group(2).strip())
            if out and out[-1] != "":
                out.append("")
            if level == 1:
                out.append(f"<b>{text.upper()}</b>")
            elif level == 2:
                out += [DIVIDER, f"<b>{text}</b>"]
            else:
                out.append(f"<b>{text}</b>")
            continue
        bullet = re.match(r"(\s*)[-*+]\s+(.*)", line)
        if bullet:
            indent = len(bullet.group(1).replace("\t", "  "))
            out.append(("   ◦ " if indent >= 2 else "• ") + inline(bullet.group(2)))
            continue
        out.append(inline(stripped))
    flush_table()
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)
