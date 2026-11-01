"""Backend Claude API: streaming, web search/fetch phía server, custom tools, pause_turn."""
from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from ..config import Settings
from ..llm import Agent, AgentError, AgentResult

log = logging.getLogger(__name__)

# Nếu bộ lọc an toàn từ chối, API tự chạy lại trên model dự phòng do Anthropic chọn.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeBackend:
    def __init__(self, client: Any, model: str):
        self.client = client
        self.model = model

    @classmethod
    def from_settings(cls, settings: Settings) -> "ClaudeBackend":
        return cls(anthropic.Anthropic(), settings.claude_model)

    def _tools(self, agent: Agent) -> list[dict]:
        tools: list[dict] = []
        if agent.web_search:
            tools.append({"type": "web_search_20260209", "name": "web_search", "max_uses": agent.web_search})
        if agent.web_fetch:
            tools.append({"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": agent.web_fetch})
        for t in agent.custom_tools:
            tools.append({
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
                "strict": True,
                "eager_input_streaming": True,
            })
        return tools

    def run(self, agent: Agent, prompt: str) -> AgentResult:
        tools = self._tools(agent)
        handlers = {t.name: t for t in agent.custom_tools}
        messages: list[dict] = [{"role": "user", "content": prompt}]
        result = AgentResult(text="")

        for turn in range(1, agent.max_turns + 1):
            result.turns = turn
            kwargs: dict[str, Any] = dict(
                model=self.model,
                max_tokens=agent.max_tokens,
                system=agent.system,
                messages=messages,
                output_config={"effort": agent.effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
                cache_control={"type": "ephemeral"},
            )
            if tools:
                kwargs["tools"] = tools
            log.info("[%s] lượt %d (Claude)", agent.name, turn)
            with self.client.beta.messages.stream(**kwargs) as stream:
                response = stream.get_final_message()

            usage = getattr(response, "usage", None)
            if usage is not None:
                result.input_tokens += getattr(usage, "input_tokens", 0) or 0
                result.output_tokens += getattr(usage, "output_tokens", 0) or 0

            stop = response.stop_reason
            if stop == "refusal":
                details = getattr(response, "stop_details", None)
                raise AgentError(
                    f"[{agent.name}] model từ chối yêu cầu: "
                    f"{getattr(details, 'category', None)} {getattr(details, 'explanation', '')}"
                )

            messages.append({"role": "assistant", "content": response.content})

            if stop == "pause_turn":  # server tool đang chạy dở; gửi lại để tiếp tục
                continue

            if stop == "tool_use":
                tool_results = []
                for block in response.content:
                    if getattr(block, "type", None) != "tool_use":
                        continue
                    result.tool_calls.append(block.name)
                    tool_results.append(self._run_tool(handlers, block))
                if not tool_results:
                    raise AgentError(f"[{agent.name}] stop_reason=tool_use nhưng không có tool_use block")
                messages.append({"role": "user", "content": tool_results})
                continue

            text = "\n".join(
                b.text for b in response.content if getattr(b, "type", None) == "text"
            ).strip()
            if stop == "max_tokens":
                log.warning("[%s] chạm max_tokens, kết quả có thể bị cắt", agent.name)
            if not text:
                raise AgentError(f"[{agent.name}] không trả về nội dung (stop_reason={stop})")
            result.text = text
            log.info("[%s] xong sau %d lượt (in=%d, out=%d tokens)",
                     agent.name, turn, result.input_tokens, result.output_tokens)
            return result

        raise AgentError(f"[{agent.name}] vượt quá {agent.max_turns} lượt")

    @staticmethod
    def _run_tool(handlers: dict, block: Any) -> dict:
        tool = handlers.get(block.name)
        raw = block.input
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = None
        if tool is None:
            output, is_error = f"Không có tool tên '{block.name}'", True
        else:
            output, is_error = tool.call(raw)
        out = {"type": "tool_result", "tool_use_id": block.id, "content": output}
        if is_error:
            out["is_error"] = True
        return out
