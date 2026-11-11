"""Kiểm thử luồng điều phối bằng client giả (không gọi API thật)."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from market_agents import main as main_mod
from market_agents.config import Settings
from market_agents.delivery import latest_previous_report, split_message
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


def test_fixed_pipeline_when_manager_skips_tools():
    scripts = manager_scripts()
    scripts[MGR] = [msg("end_turn", text("tôi không dùng tool")), msg("end_turn", text("# TỔNG HỢP"))]
    client = FakeClient(scripts)
    report = ManagerAgent(ClaudeBackend(client, "m")).run(session())
    assert report == "# TỔNG HỢP"
    writer_call = [c for c in client.calls if MGR in c["system"]][-1]
    assert "tools" not in writer_call and "NHẬN ĐỊNH" in writer_call["messages"][0]["content"]


def test_fallback_report_when_fixed_pipeline_fails():
    scripts = manager_scripts()
    scripts[MGR] = [msg("end_turn", text("xong"))]
    scripts[ANA] = [msg("refusal")]
    report = ManagerAgent(ClaudeBackend(FakeClient(scripts), "m")).run(session())
    assert "Báo cáo ghép tự động" in report and "DỮ LIỆU: vàng 4000" in report


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


def test_skip_if_done(tmp_path, monkeypatch):
    settings = Settings(reports_dir=tmp_path, use_datafeeds=False)
    assert not main_mod.already_done(settings, "morning")
    now = datetime.now(settings.timezone)
    (tmp_path / f"{now:%Y-%m-%d}-morning.md").write_text("x", encoding="utf-8")
    assert main_mod.already_done(settings, "morning")
    assert not main_mod.already_done(settings, "evening")


def test_scheduled_session_uses_time_window_not_trigger_time():
    at = lambda h, m=0: datetime(2026, 1, 1, h, m, tzinfo=VN)
    # Lịch chiều bị trễ sang 01:00 hôm sau, lịch sáng trễ tới 13:30: không làm gì, không nhận nhầm phiên.
    assert main_mod.scheduled_session(at(1)) is None
    assert main_mod.scheduled_session(at(13, 30)) is None
    assert main_mod.scheduled_session(at(5, 30)) is None      # quá sớm, phải chờ > 75 phút
    assert main_mod.scheduled_session(at(5, 50)) == "morning"
    assert main_mod.scheduled_session(at(10, 30)) == "morning"  # trễ nhưng vẫn gửi
    assert main_mod.scheduled_session(at(16, 50)) == "evening"
    assert main_mod.scheduled_session(at(23, 40)) == "evening"
    # Phiên được chỉ định (cron-job.org) vẫn phải nằm trong khung của chính nó.
    assert main_mod.scheduled_session(at(6, 5), "morning") == "morning"
    assert main_mod.scheduled_session(at(1), "morning") is None
    assert main_mod.scheduled_session(at(10), "evening") is None


def test_scheduled_run_outside_window_does_nothing(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 1, 0, tzinfo=tz)

    monkeypatch.setattr(main_mod, "datetime", Clock)
    monkeypatch.setattr(main_mod, "run", lambda *a, **k: pytest.fail("không được tạo bản tin"))
    assert main_mod.main(["--scheduled"]) == 0


def test_title_shows_real_time_when_late():
    assert Session("morning", datetime(2026, 1, 1, 6, 10, tzinfo=VN)).title.startswith("Bản tin sáng 07:00")
    assert Session("morning", datetime(2026, 1, 1, 10, 30, tzinfo=VN)).title.startswith("Bản tin sáng 10:30")
    assert Session("evening", datetime(2026, 1, 1, 18, 10, tzinfo=VN)).title.startswith("Bản tin chiều 18:00")


def test_hold_is_capped(tmp_path, monkeypatch):
    settings = Settings(reports_dir=tmp_path, use_datafeeds=False)
    slept = []
    monkeypatch.setattr(main_mod.time, "sleep", slept.append)
    monkeypatch.setattr(main_mod, "seconds_until_slot", lambda *a: 6 * 3600.0)
    monkeypatch.setattr(main_mod, "deliver", lambda *a: ["telegram"])
    main_mod.run("morning", hold=True, settings=settings, backend=ClaudeBackend(FakeClient(manager_scripts()), "m"))
    assert slept == []
    monkeypatch.setattr(main_mod, "seconds_until_slot", lambda *a: 600.0)
    main_mod.run("morning", hold=True, settings=settings, backend=ClaudeBackend(FakeClient(manager_scripts()), "m"))
    assert slept == [600.0]


def test_failed_delivery_is_an_error_and_allows_retry(tmp_path, monkeypatch):
    settings = Settings(reports_dir=tmp_path, use_datafeeds=False,
                        telegram_bot_token="t", telegram_chat_id="1", smtp_host="", email_to="")
    monkeypatch.setattr(main_mod, "deliver", lambda *a: [])
    with pytest.raises(main_mod.DeliveryError):
        main_mod.run("morning", settings=settings, backend=ClaudeBackend(FakeClient(manager_scripts()), "m"))
    assert not main_mod.already_done(settings, "morning")
    # Không cấu hình kênh nào (chạy thử trên máy): không coi là lỗi, vẫn giữ file.
    local = Settings(reports_dir=tmp_path, use_datafeeds=False, telegram_bot_token="", telegram_chat_id="",
                     smtp_host="", email_to="")
    main_mod.run("morning", settings=local, backend=ClaudeBackend(FakeClient(manager_scripts()), "m"))
    assert main_mod.already_done(local, "morning")


def test_previous_report_is_latest_session_not_latest_filename(tmp_path):
    settings = Settings(reports_dir=tmp_path, use_datafeeds=False)
    for name in ("2026-01-01-morning", "2026-01-01-evening", "2025-12-31-evening"):
        (tmp_path / f"{name}.md").write_text(name, encoding="utf-8")
    assert latest_previous_report(settings, "2026-01-02-morning.md") == "2026-01-01-evening"
    assert latest_previous_report(settings, "2026-01-01-evening.md") == "2026-01-01-morning"
