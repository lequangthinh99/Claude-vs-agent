"""Kiểm thử backend kiểu OpenAI (Groq) với HTTP giả."""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from market_agents.backends import make_backend
from market_agents.backends.groq import GroqBackend
from market_agents.config import Settings
from market_agents.llm import Agent, AgentError
from market_agents.manager import ManagerAgent, Session

MGR, COL, ANA, STR = (
    "**Agent Quản lý**",
    "**Agent Thu thập Thông tin Thị trường**",
    "**Agent Phân tích Tài chính**",
    "**Agent Nhận định Thị trường**",
)


def text(t, finish="stop"):
    return 200, {"model": "qwen/qwen3.8-27b", "choices": [
        {"message": {"role": "assistant", "content": t}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3}}


def call(id_, name, args):
    return 200, {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": id_, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]},
        "finish_reason": "tool_calls"}]}


class FakeHTTP:
    def __init__(self, scripts):
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append(json.loads(json.dumps(body)))
        system = body["messages"][0]["content"]
        for key, queue in self.scripts.items():
            if key in system:
                return queue.pop(0)
        raise AssertionError("unexpected agent")


def session():
    return Session(kind="morning", now=datetime(2026, 11, 2, 6, 5, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
                   market_data="GIÁ VÀNG 4100")


def scripts():
    return {
        MGR: [
            call("c1", "assign_collector", {"instructions": "thu thập"}),
            call("c2", "assign_analyst", {"instructions": "phân tích"}),
            call("c3", "assign_strategist", {"instructions": "nhận định"}),
            text("# BÁO CÁO GROQ"),
        ],
        COL: [(429, {"error": {"code": 429, "message": "rate limited"}}), text("DỮ LIỆU")],
        ANA: [text("PHÂN TÍCH")],
        STR: [text("NHẬN ĐỊNH")],
    }


def test_pipeline():
    http, sleeps = FakeHTTP(scripts()), []
    be = GroqBackend("k", "qwen/qwen3.8-27b", post=http, sleep=sleeps.append)
    assert ManagerAgent(be).run(session()) == "# BÁO CÁO GROQ"
    assert sleeps == [10.0]

    first = http.calls[0]
    assert first["model"] == "qwen/qwen3.8-27b"
    assert "models" not in first
    assert [t["function"]["name"] for t in first["tools"]] == [
        "assign_collector", "assign_analyst", "assign_strategist"]
    col = next(c for c in http.calls if COL in c["messages"][0]["content"])
    assert "tools" not in col and "GIÁ VÀNG 4100" in col["messages"][1]["content"]

    last_mgr = [c for c in http.calls if MGR in c["messages"][0]["content"]][-1]
    assert last_mgr["messages"][-1] == {"role": "tool", "tool_call_id": "c3", "content": "NHẬN ĐỊNH"}
    assert last_mgr["messages"][-2]["tool_calls"][0]["id"] == "c3"


def test_errors():
    http = FakeHTTP({"X": [(401, {"error": {"code": 401, "message": "No auth"}})]})
    with pytest.raises(AgentError, match="GROQ_API_KEY"):
        GroqBackend("k", "m", post=http).run(Agent(name="x", system="X"), "hi")

    http = FakeHTTP({"X": [(200, {"error": {"code": 502, "message": "upstream"}}), text("OK")]})
    out = GroqBackend("k", "m", post=http, sleep=lambda _: None).run(Agent(name="x", system="X"), "hi")
    assert out.text == "OK"




def test_groq_backend_uses_groq_endpoint(monkeypatch):
    from market_agents.backends.groq import GROQ_API_URL, GroqBackend

    seen = []

    def post(url, headers, body, timeout):
        seen.append((url, body))
        return text("OK")

    be = GroqBackend("k", "some-model", post=post)
    assert be.run(Agent(name="x", system="X"), "hi").text == "OK"
    assert seen[0][0] == GROQ_API_URL and "models" not in seen[0][1]

    for var in ("MARKET_AGENTS_PROVIDER", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setenv("GROQ_MODEL", "m1")
    assert Settings().provider == "groq"
    assert isinstance(make_backend(Settings()), GroqBackend)


def test_groq_client_side_fallback():
    from market_agents.backends.groq import GroqBackend

    models = []

    def post(url, headers, body, timeout):
        assert "models" not in body  # Groq không nhận tham số này
        models.append(body["model"])
        if body["model"] == "big":
            return 413, {"error": {"message": "Request too large"}}
        return text("OK từ " + body["model"])

    be = GroqBackend("k", "big", ["small"], post=post, sleep=lambda _: None)
    assert be.run(Agent(name="x", system="X"), "hi").text == "OK từ small"
    assert models == ["big", "small"]


def test_lean_mode_keeps_manager_context_small_and_assembles_report():
    long = "X" * 5000
    s = scripts()
    s[COL] = [text("DỮ LIỆU " + long)]
    s[ANA] = [text("PHÂN TÍCH " + long)]
    s[STR] = [text("NHẬN ĐỊNH " + long)]
    s[MGR][-1] = text("## Tóm tắt nhanh\n- ý chính\n_Báo cáo do hệ thống AI tạo, chỉ mang tính tham khảo, không phải khuyến nghị đầu tư._")
    http = FakeHTTP(s)
    be = GroqBackend("k", "m", post=http, sleep=lambda _: None)
    sess = session()
    sess.previous_report = "P" * 9000
    report = ManagerAgent(be, lean=True).run(sess)
    assert report.count("không phải khuyến nghị đầu tư") == 1

    assert report.startswith("# Bản tin sáng 07:00") and "- ý chính" in report
    assert report.count("X" * 5000) == 3            # bản đầy đủ được ghép vào báo cáo
    last_mgr = [c for c in http.calls if MGR in c["messages"][0]["content"]][-1]
    assert len(json.dumps(last_mgr, ensure_ascii=False)) < 12000   # Quản lý chỉ thấy bản rút gọn
    assert last_mgr["max_tokens"] == 1500
    ana = next(c for c in http.calls if ANA in c["messages"][0]["content"])
    assert ana["max_tokens"] == 1600 and "P" * 1600 not in ana["messages"][1]["content"]


def test_wait_uses_retry_hint():
    sleeps = []
    http = FakeHTTP({"X": [(429, {"error": {"code": "rate_limit_exceeded",
                                            "message": "Please try again in 7.5s. Need more"}}), text("OK")]})
    GroqBackend("k", "m", post=http, sleep=sleeps.append).run(Agent(name="x", system="X"), "hi")
    assert sleeps == [8.5]


def test_lean_default_for_groq(monkeypatch):
    monkeypatch.delenv("MARKET_AGENTS_LEAN", raising=False)
    monkeypatch.setenv("MARKET_AGENTS_PROVIDER", "groq")
    assert Settings().lean is True
    monkeypatch.setenv("MARKET_AGENTS_PROVIDER", "claude")
    assert Settings().lean is False
    monkeypatch.setenv("MARKET_AGENTS_LEAN", "1")
    assert Settings().lean is True


def test_flow_section_is_its_own_report_section():
    from market_agents.manager import split_section

    analyst = "### 🧭 Bối cảnh chính\n- a\n### 💸 Dòng tiền đang đổ vào đâu\n**Thế giới**\n- **Đang vào:** GLD\n### 🌡️ Tâm lý\n- b"
    flows, rest = split_section(analyst, "### 💸")
    assert flows == "**Thế giới**\n- **Đang vào:** GLD"
    assert "💸" not in rest and "🧭" in rest and "🌡️" in rest

    s = scripts()
    s[ANA] = [text(analyst)]
    http = FakeHTTP(s)
    report = ManagerAgent(GroqBackend("k", "m", post=http, sleep=lambda _: None), lean=True).run(session())
    assert report.index("## 🎯 Nhận định") < report.index("## 💸 Dòng tiền") < report.index("## 🔍 Phân tích")


def lean_data():
    from market_agents.datafeeds import CalendarEvent, MarketData, NewsItem, Quote

    now = session().now
    return MarketData(
        now=now,
        quotes=[Quote("Vàng (XAU, futures)", "GC=F", 4100.0, 2.5, now)],
        news=[NewsItem("CNBC", "Fed holds", "https://cnbc.com/fed", now, "", ref="T1")],
        events=[CalendarEvent(now.replace(hour=20, minute=30), "USD", "CPI m/m", "High", "0.3%", "0.6%")],
        calendar_ok=True,
    )


def test_lean_report_tables_links_and_calendar_come_from_code():
    s = scripts()
    s[COL] = [text("### 📰 Tin chính\n**Vĩ mô & NHTW**\n- **Fed giữ lãi suất:** không đổi [T1]\n- **Tin lạ:** x [T9]")]
    s[STR] = [text("| Tài sản | Điểm |\n|---|---|\n| Vàng | +3 |\n- **Lý do:** vàng突破 4.100 [T1]")]
    http = FakeHTTP(s)
    sess = session()
    sess.data, sess.scorecard = lean_data(), "| Vàng | +3 | +2.00% | ✅ đúng chiều |"
    report = ManagerAgent(GroqBackend("k", "m", post=http, sleep=lambda _: None), lean=True).run(sess)

    assert "- **Fed giữ lãi suất:** không đổi ([CNBC](https://cnbc.com/fed))" in report
    assert "[T1]" not in report and "[T9]" not in report and "突破" not in report
    assert "## 💵 Bảng giá\n\n_Giá lúc 06:05 02/11" in report and "- **Vàng (XAU, futures):** 4,100.00 (+2.50%)" in report
    assert "## 📅 Lịch sự kiện\n\n- **20:30 02/11** 🔴 Mỹ: CPI m/m — dự báo 0.3% · kỳ trước 0.6%" in report
    assert report.count("📰 Tin chính") == 1
    order = [report.index(h) for h in ("## 🎯", "## 🧮", "## 📰", "## 💵", "## 📅")]
    assert order == sorted(order)
    # Thu thập chỉ chọn tin; Phân tích và Nhận định vẫn nhận đủ bảng giá + lịch do code dựng.
    col = next(c for c in http.calls if COL in c["messages"][0]["content"])
    assert "Nhiệm vụ DUY NHẤT" in col["messages"][0]["content"]
    for marker in (ANA, STR):
        prompt = next(c for c in http.calls if marker in c["messages"][0]["content"])["messages"][1]["content"]
        assert "| Vàng (XAU, futures) | 4,100.00 |" in prompt and "CPI m/m" in prompt and "Fed giữ lãi suất" in prompt

    sess.data.events = []          # không có sự kiện -> bỏ hẳn mục, không để model tự nghĩ ra
    report = ManagerAgent(GroqBackend("k", "m", post=FakeHTTP(scripts()), sleep=lambda _: None), lean=True).run(sess)
    assert "📅" not in report


def test_truncated_worker_is_retried_then_trimmed():
    from market_agents.manager import drop_cut_tail

    def cut(content):
        return 200, {"choices": [{"message": {"content": content}, "finish_reason": "length"}], "usage": {}}

    s = scripts()
    s[STR] = [cut("### 💵 USD\n- **Lý do:** a\n- **Kịch"), text("### 💵 USD\n- **Lý do:** gọn")]
    http = FakeHTTP(s)
    report = ManagerAgent(GroqBackend("k", "m", post=http, sleep=lambda _: None), lean=True).run(session())
    assert "- **Lý do:** gọn" in report and "**Kịch" not in report
    retry = [c for c in http.calls if STR in c["messages"][0]["content"]][1]
    assert "bị cắt giữa chừng" in retry["messages"][1]["content"]

    s = scripts()
    s[STR] = [cut("### 💵 USD\n- **Lý do:** a\n### 🥇 Vàng\n- **Kịch"), cut("### 💵 USD\n- **Lý")]
    report = ManagerAgent(GroqBackend("k", "m", post=FakeHTTP(s), sleep=lambda _: None), lean=True).run(session())
    assert "- **Lý do:** a" in report and "**Kịch" not in report and "🥇 Vàng" not in report
    assert drop_cut_tail("một dòng") == "một dòng"
