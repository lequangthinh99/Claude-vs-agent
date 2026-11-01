"""Kiểm thử backend Gemini bằng client giả dùng kiểu dữ liệu thật của google-genai."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

genai_types = pytest.importorskip("google.genai.types")
from google.genai import errors  # noqa: E402

from market_agents.backends import make_backend  # noqa: E402
from market_agents.backends.gemini import GeminiBackend  # noqa: E402
from market_agents.config import Settings  # noqa: E402
from market_agents.llm import Agent, AgentError  # noqa: E402
from market_agents.manager import ManagerAgent, Session  # noqa: E402

t = genai_types
MGR, COL, ANA, STR = (
    "**Agent Quản lý**",
    "**Agent Thu thập Thông tin Thị trường**",
    "**Agent Phân tích Tài chính**",
    "**Agent Nhận định Thị trường**",
)


def reply(*parts, finish="STOP", sources=()):
    meta = None
    if sources:
        meta = t.GroundingMetadata(grounding_chunks=[
            t.GroundingChunk(web=t.GroundingChunkWeb(uri=u, title=title)) for title, u in sources
        ])
    return t.GenerateContentResponse(candidates=[t.Candidate(
        content=t.Content(role="model", parts=list(parts)), finish_reason=finish, grounding_metadata=meta,
    )])


def call(name, instructions):
    return t.Part(function_call=t.FunctionCall(name=name, args={"instructions": instructions}))


class FakeGenai:
    def __init__(self, scripts):
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.calls = []
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, model, contents, config):
        self.calls.append(SimpleNamespace(model=model, contents=list(contents), config=config))
        for key, queue in self.scripts.items():
            if key in config.system_instruction:
                item = queue.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item
        raise AssertionError("unexpected agent")


def scripts():
    return {
        MGR: [
            reply(call("assign_collector", "thu thập")),
            reply(call("assign_analyst", "phân tích")),
            reply(call("assign_strategist", "nhận định")),
            reply(t.Part(text="# BÁO CÁO GEMINI")),
        ],
        COL: [
            errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}),
            reply(t.Part(text="nghĩ...", thought=True), t.Part(text="DỮ LIỆU"),
                  sources=[("Reuters", "https://reuters.com/a")]),
        ],
        ANA: [reply(t.Part(text="PHÂN TÍCH"))],
        STR: [reply(t.Part(text="NHẬN ĐỊNH"))],
    }


def session():
    return Session(kind="evening", now=datetime(2026, 11, 2, 17, 35, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")))


def test_gemini_full_pipeline():
    client = FakeGenai(scripts())
    sleeps = []
    report = ManagerAgent(GeminiBackend(client, "gemini-3.8-flash", sleep=sleeps.append)).run(session())
    assert report == "# BÁO CÁO GEMINI"
    assert sleeps == [15.0]  # đã chờ rồi thử lại sau lỗi 429

    # Manager chỉ có function calling; collector chỉ có Google Search.
    mgr_cfg = next(c.config for c in client.calls if MGR in c.config.system_instruction)
    assert mgr_cfg.tools[0].function_declarations and mgr_cfg.tools[0].google_search is None
    col_cfg = next(c.config for c in client.calls if COL in c.config.system_instruction)
    assert col_cfg.tools[0].google_search is not None

    # Analyst nhận dữ liệu thu thập kèm nguồn grounding, không lẫn phần "thought".
    ana = next(c for c in client.calls if ANA in c.config.system_instruction)
    prompt = ana.contents[0].parts[0].text
    assert "DỮ LIỆU" in prompt and "https://reuters.com/a" in prompt and "nghĩ..." not in prompt

    # Kết quả nhân viên trả về manager dưới dạng function_response.
    last_mgr = [c for c in client.calls if MGR in c.config.system_instruction][-1]
    fr = last_mgr.contents[-1].parts[0].function_response
    assert fr.name == "assign_strategist" and fr.response == {"result": "NHẬN ĐỊNH"}


def test_gemini_tool_order_error_is_reported_to_manager():
    s = scripts()
    s[MGR].insert(0, reply(call("assign_strategist", "sớm")))
    client = FakeGenai(s)
    ManagerAgent(GeminiBackend(client, "m", sleep=lambda _: None)).run(session())
    second = [c for c in client.calls if MGR in c.config.system_instruction][1]
    assert "error" in second.contents[-1].parts[0].function_response.response


def test_gemini_safety_block_raises():
    client = FakeGenai({"X": [reply(t.Part(text=""), finish="SAFETY")]})
    with pytest.raises(AgentError):
        GeminiBackend(client, "m").run(Agent(name="x", system="X"), "hi")


def test_gemini_non_retryable_error_raises():
    client = FakeGenai({"X": [errors.ClientError(400, {"error": {"code": 400, "message": "bad"}})]})
    with pytest.raises(AgentError):
        GeminiBackend(client, "m").run(Agent(name="x", system="X"), "hi")


def test_provider_auto_selection(monkeypatch):
    monkeypatch.delenv("MARKET_AGENTS_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert Settings().provider == "gemini"
    assert isinstance(make_backend(Settings()), GeminiBackend)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert Settings().provider == "claude"
    monkeypatch.setenv("MARKET_AGENTS_PROVIDER", "gemini")
    assert Settings().provider == "gemini"
