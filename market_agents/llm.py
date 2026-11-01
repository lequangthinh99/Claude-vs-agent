"""Vòng lặp agent dùng chung cho mọi agent (quản lý và nhân viên).

Mỗi agent là một vòng lặp Messages API:
  - streaming (tránh timeout với output dài),
  - xử lý `pause_turn` của server tool (web search / web fetch),
  - chạy các custom tool do Python cung cấp (agent quản lý dùng để giao việc),
  - dừng khi model trả lời xong (`end_turn`).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import anthropic

log = logging.getLogger(__name__)

# Server-side fallback: nếu bộ lọc an toàn từ chối, API tự chạy lại trên model dự phòng
# do Anthropic chọn. Không cần tự duy trì danh sách model.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

WEB_SEARCH_TYPE = "web_search_20260209"
WEB_FETCH_TYPE = "web_fetch_20260209"


def web_search_tool(max_uses: int) -> dict:
    return {"type": WEB_SEARCH_TYPE, "name": "web_search", "max_uses": max_uses}


def web_fetch_tool(max_uses: int) -> dict:
    return {"type": WEB_FETCH_TYPE, "name": "web_fetch", "max_uses": max_uses}


class AgentError(RuntimeError):
    """Agent không hoàn thành được nhiệm vụ."""


@dataclass
class CustomTool:
    """Tool chạy phía client. `handler` nhận input (dict) và trả về chuỗi kết quả."""

    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], str]

    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "strict": True,
            "eager_input_streaming": True,
        }

    def validate(self, data: Any) -> str | None:
        """Kiểm tra input theo schema (bắt buộc khi bật eager_input_streaming)."""
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
            enum = props[key].get("enum")
            if enum and value not in enum:
                return f"trường '{key}' phải thuộc {enum}"
        return None


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
    model: str
    effort: str = "medium"
    server_tools: list[dict] = field(default_factory=list)
    custom_tools: list[CustomTool] = field(default_factory=list)
    max_tokens: int = 64000
    max_turns: int = 30

    def run(self, client: anthropic.Anthropic, prompt: str) -> AgentResult:
        tools = list(self.server_tools) + [t.definition() for t in self.custom_tools]
        handlers = {t.name: t for t in self.custom_tools}
        messages: list[dict] = [{"role": "user", "content": prompt}]
        result = AgentResult(text="")

        for turn in range(1, self.max_turns + 1):
            result.turns = turn
            kwargs: dict[str, Any] = dict(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self.system,
                messages=messages,
                output_config={"effort": self.effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
                cache_control={"type": "ephemeral"},
            )
            if tools:
                kwargs["tools"] = tools
            log.info("[%s] lượt %d", self.name, turn)
            with client.beta.messages.stream(**kwargs) as stream:
                response = stream.get_final_message()

            usage = getattr(response, "usage", None)
            if usage is not None:
                result.input_tokens += getattr(usage, "input_tokens", 0) or 0
                result.output_tokens += getattr(usage, "output_tokens", 0) or 0

            stop = response.stop_reason
            if stop == "refusal":
                details = getattr(response, "stop_details", None)
                raise AgentError(
                    f"[{self.name}] model từ chối yêu cầu: "
                    f"{getattr(details, 'category', None)} {getattr(details, 'explanation', '')}"
                )

            messages.append({"role": "assistant", "content": response.content})

            if stop == "pause_turn":
                # Server tool đang chạy dở; gửi lại để server tiếp tục.
                continue

            if stop == "tool_use":
                tool_results = []
                for block in response.content:
                    if getattr(block, "type", None) != "tool_use":
                        continue
                    result.tool_calls.append(block.name)
                    tool_results.append(self._run_tool(handlers, block))
                if not tool_results:
                    raise AgentError(f"[{self.name}] stop_reason=tool_use nhưng không có tool_use block")
                messages.append({"role": "user", "content": tool_results})
                continue

            text = "\n".join(
                b.text for b in response.content if getattr(b, "type", None) == "text"
            ).strip()
            if stop == "max_tokens":
                log.warning("[%s] chạm max_tokens, kết quả có thể bị cắt", self.name)
            if not text:
                raise AgentError(f"[{self.name}] không trả về nội dung (stop_reason={stop})")
            result.text = text
            log.info(
                "[%s] xong sau %d lượt (in=%d, out=%d tokens)",
                self.name, turn, result.input_tokens, result.output_tokens,
            )
            return result

        raise AgentError(f"[{self.name}] vượt quá {self.max_turns} lượt")

    def _run_tool(self, handlers: dict[str, CustomTool], block: Any) -> dict:
        tool = handlers.get(block.name)
        raw = block.input
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = None
        if tool is None:
            return _tool_error(block.id, f"Không có tool tên '{block.name}'")
        problem = tool.validate(raw)
        if problem:
            return _tool_error(block.id, f"INVALID_INPUT: {problem}")
        try:
            output = tool.handler(raw)
        except AgentError as exc:
            return _tool_error(block.id, str(exc))
        return {"type": "tool_result", "tool_use_id": block.id, "content": output}


def _tool_error(tool_use_id: str, message: str) -> dict:
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": message, "is_error": True}
