"""Backend OpenRouter (API tương thích OpenAI Chat Completions), dùng được với model miễn phí.

- Agent quản lý giao việc qua function calling (`tools`).
- Không dùng tìm kiếm web của OpenRouter (tính phí): các agent dựa vào giá + tin RSS miễn phí
  do `datafeeds.py` cung cấp.
- Có thể khai báo model dự phòng (OPENROUTER_FALLBACK_MODELS): OpenRouter tự chuyển khi
  model chính lỗi hoặc quá tải.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from ..config import Settings
from ..llm import Agent, AgentError, AgentResult

log = logging.getLogger(__name__)

API_URL = "https://openrouter.ai/api/v1/chat/completions"
RETRY_STATUS = {408, 429, 500, 502, 503, 504}
MAX_RETRIES = 6
REQUEST_TIMEOUT = 300  # giây


def _http_post(url: str, headers: dict, body: dict, timeout: float) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read() or b"{}")
        except ValueError:
            payload = {}
        return exc.code, payload


class OpenRouterBackend:
    def __init__(self, api_key: str, model: str, fallback_models: list[str] | None = None,
                 post: Callable[..., tuple[int, dict]] = _http_post, sleep=time.sleep):
        self.api_key = api_key
        self.model = model
        self.fallback_models = fallback_models or []
        self._post = post
        self._sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenRouterBackend":
        if not settings.openrouter_api_key:
            raise AgentError("Thiếu OPENROUTER_API_KEY (tạo tại openrouter.ai/settings/keys)")
        return cls(settings.openrouter_api_key, settings.openrouter_model, settings.openrouter_fallback_models)

    def _body(self, agent: Agent, messages: list[dict]) -> dict:
        body: dict[str, Any] = {"model": self.model, "messages": messages}
        if self.fallback_models:
            body["models"] = [self.model, *self.fallback_models]
        if agent.custom_tools:
            body["tools"] = [
                {"type": "function", "function": {
                    "name": t.name, "description": t.description, "parameters": t.input_schema,
                }}
                for t in agent.custom_tools
            ]
        return body

    def _complete(self, agent: Agent, body: dict) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": "market-agents",
        }
        delay = 10.0
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                status, data = self._post(API_URL, headers, body, REQUEST_TIMEOUT)
            except (TimeoutError, OSError) as exc:
                status, data = 0, {"error": {"message": f"lỗi mạng: {exc}"}}
            error = data.get("error") if isinstance(data, dict) else None
            if status == 200 and not error and data.get("choices"):
                return data
            code = (error or {}).get("code", status) if isinstance(error, dict) else status
            message = (error or {}).get("message", "") if isinstance(error, dict) else str(error)
            retryable = status in RETRY_STATUS or code in RETRY_STATUS or status == 0 or (
                status == 200 and not data.get("choices"))
            if not retryable or attempt == MAX_RETRIES:
                hint = ""
                if status == 404 or "data policy" in message.lower() or "privacy" in message.lower():
                    hint = " (kiểm tra cài đặt openrouter.ai/settings/privacy cho model miễn phí)"
                if status == 401:
                    hint = " (OPENROUTER_API_KEY sai hoặc đã bị thu hồi)"
                raise AgentError(f"[{agent.name}] OpenRouter lỗi {status}/{code}: {message}{hint}")
            log.warning("[%s] OpenRouter %s (%s), chờ %.0fs rồi thử lại (%d/%d)",
                        agent.name, code, message[:200], delay, attempt, MAX_RETRIES)
            self._sleep(delay)
            delay = min(delay * 2, 120.0)
        raise AssertionError("unreachable")

    def run(self, agent: Agent, prompt: str) -> AgentResult:
        handlers = {t.name: t for t in agent.custom_tools}
        messages: list[dict] = [
            {"role": "system", "content": agent.system},
            {"role": "user", "content": prompt},
        ]
        result = AgentResult(text="")

        for turn in range(1, agent.max_turns + 1):
            result.turns = turn
            log.info("[%s] lượt %d (OpenRouter)", agent.name, turn)
            data = self._complete(agent, self._body(agent, messages))
            usage = data.get("usage") or {}
            result.input_tokens += usage.get("prompt_tokens", 0) or 0
            result.output_tokens += usage.get("completion_tokens", 0) or 0

            choice = data["choices"][0]
            message = choice.get("message") or {}
            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": tool_calls,
                })
                for call in tool_calls:
                    fn = call.get("function") or {}
                    name = fn.get("name", "")
                    result.tool_calls.append(name)
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = None
                    tool = handlers.get(name)
                    if tool is None:
                        output, is_error = f"Không có tool tên '{name}'", True
                    else:
                        output, is_error = tool.call(args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": f"LỖI: {output}" if is_error else output,
                    })
                continue

            text = (message.get("content") or "").strip()
            finish = choice.get("finish_reason")
            if finish == "length":
                log.warning("[%s] chạm giới hạn output, kết quả có thể bị cắt", agent.name)
            if not text:
                raise AgentError(f"[{agent.name}] OpenRouter không trả về nội dung (finish_reason={finish})")
            result.text = text
            log.info("[%s] xong sau %d lượt (model %s, in=%d, out=%d tokens)",
                     agent.name, turn, data.get("model", self.model), result.input_tokens, result.output_tokens)
            return result

        raise AgentError(f"[{agent.name}] vượt quá {agent.max_turns} lượt")
