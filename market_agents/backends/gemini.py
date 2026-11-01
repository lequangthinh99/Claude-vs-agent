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

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 6
SEARCH_FALLBACK_AFTER = 2
REQUEST_TIMEOUT_MS = 300_000  # 5 phút / request
# Mức suy nghĩ của agent -> thinking_level của Gemini. Để thấp cho nhanh: gói miễn phí
# mất 2–5 phút mỗi lượt ở mức mặc định.
THINKING_LEVELS = {"low": "MINIMAL", "medium": "LOW", "high": "MEDIUM", "xhigh": "HIGH", "max": "HIGH"}


class GeminiBackend:
    def __init__(self, client: Any, model: str, sleep=time.sleep):
        self.client = client
        self.model = model
        self._sleep = sleep
        self.search_blocked = False   # nhớ lại khi Google Search bị từ chối, đỡ phí lượt thử
        self.thinking_supported = True

    @classmethod
    def from_settings(cls, settings: Settings) -> "GeminiBackend":
        from google import genai
        from google.genai import types

        if not settings.gemini_api_key:
            raise AgentError("Thiếu GEMINI_API_KEY (lấy miễn phí tại aistudio.google.com/apikey)")
        client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
        )
        return cls(client, settings.gemini_model)

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
        elif agent.web_search and not self.search_blocked:
            # Gemini Flash không cho kết hợp Google Search với function calling trong cùng request;
            # agent quản lý chỉ dùng function calling, các agent nhân viên chỉ dùng search.
            tools.append(types.Tool(google_search=types.GoogleSearch()))
        return types.GenerateContentConfig(
            system_instruction=agent.system,
            tools=tools or None,
            max_output_tokens=min(agent.max_tokens, 65536),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=(
                types.ThinkingConfig(thinking_level=THINKING_LEVELS.get(agent.effort, "LOW"))
                if self.thinking_supported else None
            ),
        )

    @staticmethod
    def _has_search(config) -> bool:
        return any(getattr(t, "google_search", None) is not None for t in (config.tools or []))

    def _generate(self, agent: Agent, contents: list, config) -> tuple[Any, Any]:
        """Gọi Gemini, tự thử lại khi bị giới hạn. Trả về (response, config đang dùng).

        Gói miễn phí có thể không cho dùng Google Search (luôn trả 429). Khi request có search
        bị 429 liên tiếp SEARCH_FALLBACK_AFTER lần, bỏ công cụ search và tiếp tục với dữ liệu
        đã được cung cấp trong prompt.
        """
        from google.genai import errors

        delay = 15.0
        search_429 = 0
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model, contents=contents, config=config
                )
                return response, config
            except errors.APIError as exc:
                message = getattr(exc, "message", None) or str(exc)
                if exc.code == 429 and self._has_search(config):
                    search_429 += 1
                    if search_429 >= SEARCH_FALLBACK_AFTER:
                        log.warning("[%s] Google Search bị giới hạn (%s) — tiếp tục KHÔNG dùng search.",
                                    agent.name, message[:300])
                        self.search_blocked = True
                        config = config.model_copy(update={"tools": None})
                        continue
                if exc.code == 400 and config.thinking_config is not None and "think" in message.lower():
                    log.warning("[%s] Model không hỗ trợ thinking_level, bỏ cấu hình này.", agent.name)
                    self.thinking_supported = False
                    config = config.model_copy(update={"thinking_config": None})
                    continue
                if exc.code not in RETRY_STATUS or attempt == MAX_RETRIES:
                    raise AgentError(f"[{agent.name}] Gemini lỗi {exc.code}: {message}") from exc
                log.warning("[%s] Gemini %s (%s), chờ %.0fs rồi thử lại (%d/%d)",
                            agent.name, exc.code, message[:200], delay, attempt, MAX_RETRIES)
                self._sleep(delay)
                delay = min(delay * 2, 120.0)
            except Exception as exc:  # noqa: BLE001 - timeout mạng: thử lại
                if "timeout" not in type(exc).__name__.lower() or attempt == MAX_RETRIES:
                    raise
                log.warning("[%s] Gemini quá thời gian chờ, thử lại (%d/%d)", agent.name, attempt, MAX_RETRIES)
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
            response, config = self._generate(agent, contents, config)

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
