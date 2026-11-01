"""Kiểm thử luồng điều phối bằng client giả (không gọi API thật)."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from market_agents import main as main_mod
from market_agents.config import Settings
from market_agents.delivery import split_message
from market_agents.backends.claude import ClaudeBackend
from market_agents.llm import Agent, AgentError
from market_agents.manager import ManagerAgent, Session
from market_agents.scheduler import next_run

VN = ZoneInfo("Asia/Ho_Chi_Minh")
# Dấu hiệu nhận diện system prompt của từng agent
MGR = "**Agent Quản lý**"
COL = "**Agent Thu thập Thông tin Thị trường**"
ANA = "**Agent Phân tích Tài chính**"
STR = "**Agent Nhận định Thị trường**"


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool_use(id_, name, instructions):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input={"instructions": instructions})


def msg(stop, *content):
    return SimpleNamespace(
        stop_reason=stop, content=list(content), stop_details=None,
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


class FakeStream:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.response


class FakeClient:
    """Trả lời theo system prompt của từng agent, ghi lại các request."""

    def __init__(self, scripts):
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        kwargs = {**kwargs, "messages": list(kwargs["messages"])}  # chụp lại trạng thái lúc gọi
        self.calls.append(kwargs)
        for key, queue in self.scripts.items():
            if key in kwargs["system"]:
                return FakeStream(queue.pop(0))
        raise AssertionError("unexpected agent")


def manager_scripts(final="# BÁO CÁO CUỐI"):
    return {
        MGR: [
            msg("tool_use", tool_use("t1", "assign_collector", "thu thập phiên sáng")),
            msg("tool_use", tool_use("t2", "assign_analyst", "phân tích")),
            msg("tool_use", tool_use("t3", "assign_strategist", "nhận định")),
            msg("end_turn", text(final)),
        ],
        COL: [
            msg("pause_turn", SimpleNamespace(type="server_tool_use")),
            msg("end_turn", text("DỮ LIỆU: vàng 4000")),
        ],
        ANA: [msg("end_turn", text("PHÂN TÍCH"))],
        STR: [msg("end_turn", text("NHẬN ĐỊNH"))],
    }


def session():
    return Session(kind="morning", now=datetime(2026, 11, 2, 6, 35, tzinfo=VN), previous_report="KỲ TRƯỚC")


def test_manager_orchestrates_all_workers():
    client = FakeClient(manager_scripts())
    report = ManagerAgent(ClaudeBackend(client, "claude-opus-5-5")).run(session())
    assert report == "# BÁO CÁO CUỐI"

    # Analyst nhận dữ liệu thu thập + báo cáo kỳ trước; strategist nhận cả hai kết quả.
    analyst_call = next(c for c in client.calls if ANA in c["system"])
    assert "DỮ LIỆU: vàng 4000" in analyst_call["messages"][0]["content"]
    assert "KỲ TRƯỚC" in analyst_call["messages"][0]["content"]
    strat_call = next(c for c in client.calls if STR in c["system"])
    assert "PHÂN TÍCH" in strat_call["messages"][0]["content"]

    # Kết quả của nhân viên được trả về cho quản lý dưới dạng tool_result.
    last_manager = [c for c in client.calls if MGR in c["system"]][-1]
    tool_result = last_manager["messages"][-1]["content"][0]
    assert tool_result["tool_use_id"] == "t3" and tool_result["content"] == "NHẬN ĐỊNH"

    # Collector có web tools; mọi request bật fallback và không có tham số bị cấm.
    collector_call = next(c for c in client.calls if COL in c["system"])
    assert {t["name"] for t in collector_call["tools"]} == {"web_search", "web_fetch"}
    for c in client.calls:
        assert c["fallbacks"] == "default"
        assert "temperature" not in c and "thinking" not in c


def test_pause_turn_resends_conversation():
    client = FakeClient(manager_scripts())
    ManagerAgent(ClaudeBackend(client, "m")).run(session())
    collector_calls = [c for c in client.calls if COL in c["system"]]
    assert len(collector_calls) == 2
    assert collector_calls[1]["messages"][-1]["role"] == "assistant"


def test_out_of_order_assignment_returns_tool_error():
    scripts = manager_scripts()
    scripts[MGR].insert(0, msg("tool_use", tool_use("t0", "assign_strategist", "sớm quá")))
    client = FakeClient(scripts)
    ManagerAgent(ClaudeBackend(client, "m")).run(session())
    second = [c for c in client.calls if MGR in c["system"]][1]
    err = second["messages"][-1]["content"][0]
    assert err["is_error"] is True


def test_fallback_report_when_manager_skips_steps():
    scripts = {MGR: [msg("end_turn", text("xong"))]}
    report = ManagerAgent(ClaudeBackend(FakeClient(scripts), "m")).run(session())
    assert "Báo cáo ghép tự động" in report


def test_refusal_raises():
    client = FakeClient({"X": [msg("refusal")]})
    with pytest.raises(AgentError):
        ClaudeBackend(client, "m").run(Agent(name="x", system="X"), "hi")


def test_run_saves_report(tmp_path, monkeypatch):
    settings = Settings(reports_dir=tmp_path, use_datafeeds=False)
    (tmp_path / "2000-01-01-evening.md").write_text("KỲ TRƯỚC", encoding="utf-8")
    report = main_mod.run("morning", send=False, settings=settings, backend=ClaudeBackend(FakeClient(manager_scripts()), "m"))
    files = sorted(p.name for p in tmp_path.glob("*-morning.md"))
    assert len(files) == 1 and (tmp_path / files[0]).read_text(encoding="utf-8") == report


def test_detect_session_and_schedule():
    assert main_mod.detect_session(datetime(2026, 1, 1, 6, 35, tzinfo=VN)) == "morning"
    assert main_mod.detect_session(datetime(2026, 1, 1, 17, 35, tzinfo=VN)) == "evening"
    start, kind = next_run(datetime(2026, 1, 1, 8, 0, tzinfo=VN))
    assert kind == "evening" and (start.hour, start.minute) == (17, 5)
    start, kind = next_run(datetime(2026, 1, 1, 20, 0, tzinfo=VN))
    assert kind == "morning" and start.day == 2 and (start.hour, start.minute) == (6, 5)


def test_split_message():
    body = "\n".join(f"dòng {i} " + "x" * 50 for i in range(300))
    parts = split_message(body, limit=1000)
    assert all(len(p) <= 1000 for p in parts)
    assert "".join(parts) == body
