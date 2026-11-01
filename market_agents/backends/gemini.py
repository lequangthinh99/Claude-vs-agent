"""Backend Gemini API (Google AI Studio): Google Search grounding + function calling.

Dùng được với gói miễn phí của Gemini Flash. Khi bị giới hạn tốc độ (HTTP 429),
backend tự chờ rồi thử lại.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..config import Settings
from ..llm import Agent, AgentError, AgentResult

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 503}
MAX_RETRIES = 6


class GeminiBackend:
    def __init__(self, client: Any, model: str, sleep=time.sleep):
        self.client = client
        self.model = model
        self._sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings) -> "GeminiBackend":
        from google import genai

        if not settings.gemini_api_key:
            raise AgentError("Thiếu GEMINI_API_KEY (lấy miễn phí tại aistudio.google.com/apikey)")
        return cls(genai.Client(api_key=settings.gemini_api_key), settings.gemini_model)

    def _config(self, agent: Agent):
        from google.genai import types

        tools = []
        if agent.custom_tools:
            tools.append(types.Tool(function_declarations=[
                types.FunctionDeclaration(
                    name=t.name, description=t.description, parameters_json_schema=t.input_schema
                )
                for t in agent.custom_tools
            ]))
        elif agent.web_search:
            # Gemini Flash không cho kết hợp Google Search với function calling trong cùng request;
            # agent quản lý chỉ dùng function calling, các agent nhân viên chỉ dùng search.
            tools.append(types.Tool(google_search=types.GoogleSearch()))
        return types.GenerateContentConfig(
            system_instruction=agent.system,
            tools=tools or None,
            max_output_tokens=min(agent.max_tokens, 65536),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    def _generate(self, agent: Agent, contents: list, config) -> Any:
        from google.genai import errors

        delay = 15.0
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return self.client.models.generate_content(
                    model=self.model, contents=contents, config=config
                )
            except errors.APIError as exc:
                if exc.code not in RETRY_STATUS or attempt == MAX_RETRIES:
                    raise AgentError(f"[{agent.name}] Gemini lỗi {exc.code}: {exc}") from exc
                log.warning("[%s] Gemini %s, chờ %.0fs rồi thử lại (%d/%d)",
                            agent.name, exc.code, delay, attempt, MAX_RETRIES)
                self._sleep(delay)
                delay = min(delay * 2, 120.0)
        raise AssertionError("unreachable")

    def run(self, agent: Agent, prompt: str) -> AgentResult:
        from google.genai import types

        config = self._config(agent)
        handlers = {t.name: t for t in agent.custom_tools}
        contents: list = [types.Content(role="user", parts=[types.Part(text=prompt)])]
        result = AgentResult(text="")

        for turn in range(1, agent.max_turns + 1):
            result.turns = turn
            log.info("[%s] lượt %d (Gemini)", agent.name, turn)
            response = self._generate(agent, contents, config)

            usage = getattr(response, "usage_metadata", None)
            if usage is not None:
                result.input_tokens += getattr(usage, "prompt_token_count", 0) or 0
                result.output_tokens += getattr(usage, "candidates_token_count", 0) or 0

            if not response.candidates:
                feedback = getattr(response, "prompt_feedback", None)
                raise AgentError(f"[{agent.name}] Gemini không trả lời (bị chặn: {feedback})")
            candidate = response.candidates[0]
            finish = str(getattr(candidate, "finish_reason", "") or "")
            parts = list(getattr(candidate.content, "parts", None) or []) if candidate.content else []

            calls = [p.function_call for p in parts if getattr(p, "function_call", None)]
            if calls:
                contents.append(candidate.content)
                replies = []
                for call in calls:
                    result.tool_calls.append(call.name)
                    tool = handlers.get(call.name)
                    if tool is None:
                        output, is_error = f"Không có tool tên '{call.name}'", True
                    else:
                        output, is_error = tool.call(dict(call.args or {}))
                    replies.append(types.Part.from_function_response(
                        name=call.name,
                        response={"error": output} if is_error else {"result": output},
                    ))
                contents.append(types.Content(role="user", parts=replies))
                continue

            text = "\n".join(
                p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False)
            ).strip()
            if "SAFETY" in finish or "PROHIBITED" in finish or "BLOCKLIST" in finish:
                raise AgentError(f"[{agent.name}] Gemini chặn nội dung (finish_reason={finish})")
            if "MAX_TOKENS" in finish:
                log.warning("[%s] chạm giới hạn output, kết quả có thể bị cắt", agent.name)
            if not text:
                raise AgentError(f"[{agent.name}] Gemini không trả về nội dung (finish_reason={finish})")

            sources = _grounding_sources(candidate)
            if sources:
                text += "\n\n### Nguồn tham khảo (Google Search)\n" + "\n".join(sources)
            result.text = text
            log.info("[%s] xong sau %d lượt (in=%d, out=%d tokens)",
                     agent.name, turn, result.input_tokens, result.output_tokens)
            return result

        raise AgentError(f"[{agent.name}] vượt quá {agent.max_turns} lượt")


def _grounding_sources(candidate: Any) -> list[str]:
    meta = getattr(candidate, "grounding_metadata", None)
    chunks = getattr(meta, "grounding_chunks", None) or []
    seen, lines = set(), []
    for chunk in chunks:
        web = getattr(chunk, "web", None)
        uri = getattr(web, "uri", None)
        if not uri or uri in seen:
            continue
        seen.add(uri)
        title = getattr(web, "title", None) or getattr(web, "domain", None) or uri
        lines.append(f"- [{title}]({uri})")
    return lines
