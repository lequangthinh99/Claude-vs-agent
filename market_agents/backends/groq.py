"""Backend Groq (API tương thích OpenAI, rất nhanh). Gói miễn phí giới hạn token/phút khá thấp;
khi bị 429 backend tự chờ rồi thử lại."""
from __future__ import annotations

from ..config import Settings
from ..llm import AgentError
from .openrouter import OpenRouterBackend

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"


class GroqBackend(OpenRouterBackend):
    label = "Groq"
    api_url = GROQ_API_URL
    key_hint = "GROQ_API_KEY sai hoặc đã bị thu hồi"
    client_side_fallback = True

    @classmethod
    def from_settings(cls, settings: Settings) -> "GroqBackend":
        if not settings.groq_api_key:
            raise AgentError("Thiếu GROQ_API_KEY (tạo tại console.groq.com/keys)")
        return cls(settings.groq_api_key, settings.groq_model, settings.groq_fallback_models)
