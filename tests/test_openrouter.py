"""Kiểm thử backend OpenRouter với HTTP giả."""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from market_agents.backends import make_backend
from market_agents.backends.openrouter import OpenRouterBackend
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
    return 200, {"model": "google/gemma-4-31b-it:free", "choices": [
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
            text("# BÁO CÁO OPENROUTER"),
        ],
        COL: [(429, {"error": {"code": 429, "message": "rate limited"}}), text("DỮ LIỆU")],
        ANA: [text("PHÂN TÍCH")],
        STR: [text("NHẬN ĐỊNH")],
    }


def test_openrouter_pipeline():
    http, sleeps = FakeHTTP(scripts()), []
    be = OpenRouterBackend("k", "google/gemma-4-31b-it:free", ["nvidia/x:free"], post=http, sleep=sleeps.append)
    assert ManagerAgent(be).run(session()) == "# BÁO CÁO OPENROUTER"
    assert sleeps == [10.0]

    first = http.calls[0]
    assert first["model"] == "google/gemma-4-31b-it:free"
    assert first["models"] == ["google/gemma-4-31b-it:free", "nvidia/x:free"]
    assert [t["function"]["name"] for t in first["tools"]] == [
        "assign_collector", "assign_analyst", "assign_strategist"]
    col = next(c for c in http.calls if COL in c["messages"][0]["content"])
    assert "tools" not in col and "GIÁ VÀNG 4100" in col["messages"][1]["content"]

    last_mgr = [c for c in http.calls if MGR in c["messages"][0]["content"]][-1]
    assert last_mgr["messages"][-1] == {"role": "tool", "tool_call_id": "c3", "content": "NHẬN ĐỊNH"}
    assert last_mgr["messages"][-2]["tool_calls"][0]["id"] == "c3"


def test_openrouter_errors():
    http = FakeHTTP({"X": [(401, {"error": {"code": 401, "message": "No auth"}})]})
    with pytest.raises(AgentError, match="OPENROUTER_API_KEY"):
        OpenRouterBackend("k", "m", post=http).run(Agent(name="x", system="X"), "hi")

    http = FakeHTTP({"X": [(200, {"error": {"code": 502, "message": "upstream"}}), text("OK")]})
    out = OpenRouterBackend("k", "m", post=http, sleep=lambda _: None).run(Agent(name="x", system="X"), "hi")
    assert out.text == "OK"


def test_openrouter_selected_and_default_model(monkeypatch):
    for var in ("MARKET_AGENTS_PROVIDER", "ANTHROPIC_API_KEY", "OPENROUTER_MODEL", "OPENROUTER_FALLBACK_MODELS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    s = Settings()
    assert s.provider == "openrouter" and s.openrouter_model == "google/gemma-4-31b-it:free"
    assert s.openrouter_fallback_models == ["nvidia/nemotron-3-ultra-550b-a55b:free", "nvidia/nemotron-3.5-super-vl:free"]
    assert isinstance(make_backend(s), OpenRouterBackend)
    monkeypatch.setenv("OPENROUTER_FALLBACK_MODELS", "a:free, b:free")
    assert Settings().openrouter_fallback_models == ["a:free", "b:free"]


def test_groq_backend_uses_groq_endpoint(monkeypatch):
    from market_agents.backends.groq import GROQ_API_URL, GroqBackend

    seen = []

    def post(url, headers, body, timeout):
        seen.append((url, body))
        return text("OK")

    be = GroqBackend("k", "some-model", post=post)
    assert be.run(Agent(name="x", system="X"), "hi").text == "OK"
    assert seen[0][0] == GROQ_API_URL and "models" not in seen[0][1]

    for var in ("MARKET_AGENTS_PROVIDER", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
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
