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

    @classmethod
    def from_settings(cls, settings: Settings) -> "GroqBackend":
        if not settings.groq_api_key:
            raise AgentError("Thiếu GROQ_API_KEY (tạo tại console.groq.com/keys)")
        if not settings.groq_model:
            raise AgentError("Thiếu GROQ_MODEL (chạy chế độ check-models để chọn model)")
        return cls(settings.groq_api_key, settings.groq_model)  # Groq không hỗ trợ danh sách model dự phòng
