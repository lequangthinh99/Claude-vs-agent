"""Backend Groq (API tương thích OpenAI, rất nhanh). Gói miễn phí giới hạn token/phút khá thấp;
khi bị 429 backend tự chờ rồi thử lại."""
from __future__ import annotations

from ..config import Settings
from ..llm import AgentError
from .openai_compat import OpenAICompatBackend

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"


class GroqBackend(OpenAICompatBackend):
    label = "Groq"
    api_url = GROQ_API_URL
    key_hint = "GROQ_API_KEY sai hoặc đã bị thu hồi"

    def _body(self, agent, messages):
        body = super()._body(agent, messages)
        # gpt-oss là model "suy luận": phần suy nghĩ ăn vào max_tokens và giới hạn token/phút.
        if self.model.startswith("openai/gpt-oss"):
            body["reasoning_effort"] = "low"
        return body

    @classmethod
    def from_settings(cls, settings: Settings) -> "GroqBackend":
        if not settings.groq_api_key:
            raise AgentError("Thiếu GROQ_API_KEY (tạo tại console.groq.com/keys)")
        return cls(settings.groq_api_key, settings.groq_model, settings.groq_fallback_models)
