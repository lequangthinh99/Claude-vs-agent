"""Định nghĩa agent dùng chung, độc lập với nhà cung cấp AI.

Một `Agent` chỉ là cấu hình (vai trò, system prompt, công cụ). Việc gọi model do một
backend đảm nhận: `backends/claude.py` (Claude API) hoặc `backends/gemini.py` (Gemini API).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class AgentError(RuntimeError):
    """Agent không hoàn thành được nhiệm vụ."""


@dataclass
class CustomTool:
    """Tool chạy phía client. `handler` nhận input (dict) và trả về chuỗi kết quả."""

    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], str]

    def validate(self, data: Any) -> str | None:
        """Kiểm tra input theo schema trước khi chạy handler."""
        if not isinstance(data, dict):
            return "input phải là object JSON"
        props = self.input_schema.get("properties", {})
        for key in self.input_schema.get("required", []):
            if key not in data:
                return f"thiếu trường bắt buộc '{key}'"
        for key, value in data.items():
            if key not in props:
                return f"trường không hợp lệ '{key}'"
            if props[key].get("type") == "string" and not isinstance(value, str):
                return f"trường '{key}' phải là chuỗi"
        return None

    def call(self, data: Any) -> tuple[str, bool]:
        """Chạy tool. Trả về (kết quả, có_lỗi)."""
        problem = self.validate(data)
        if problem:
            return f"INVALID_INPUT: {problem}", True
        try:
            return self.handler(data), False
        except AgentError as exc:
            return str(exc), True


@dataclass
class AgentResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    turns: int = 0
    tool_calls: list[str] = field(default_factory=list)


@dataclass
class Agent:
    name: str
    system: str
    effort: str = "medium"          # Claude: mức suy nghĩ; Gemini bỏ qua
    web_search: int = 0             # số lượt tìm web tối đa (0 = tắt)
    web_fetch: int = 0              # số lượt đọc trang web tối đa (chỉ Claude)
    custom_tools: list[CustomTool] = field(default_factory=list)
    max_tokens: int = 64000  # với API kiểu OpenAI chỉ gửi khi <= 16000 (chế độ gọn)
    max_turns: int = 30


class Backend(Protocol):
    model: str

    def run(self, agent: Agent, prompt: str) -> AgentResult: ...
