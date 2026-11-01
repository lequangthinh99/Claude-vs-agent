"""Kiểm tra nhanh từng model Groq: mỗi model đúng 1 request nhỏ, không thử lại.

    python -m market_agents.check_models              # liệt kê model Groq rồi thử từng cái
    python -m market_agents.check_models model/a model/b

Kết quả cho mỗi model:
  OK        trả lời được VÀ gọi được tool (dùng được cho cả Agent Quản lý)
  NO_TOOLS  trả lời được nhưng không gọi tool (chỉ dùng được cho agent nhân viên)
  FAIL      lỗi (kèm lý do) -> nên loại
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

from .backends.groq import GROQ_API_URL, GROQ_MODELS_URL
from .backends.openai_compat import USER_AGENT, _http_post
from .config import Settings

# Model Groq không phải dạng chat (âm thanh, lọc nội dung, ...) -> bỏ qua khi kiểm tra
SKIP_WORDS = ("whisper", "tts", "guard", "embed", "orpheus", "playai", "distil")

PING_TOOL = {
    "type": "function",
    "function": {
        "name": "ping",
        "description": "Gửi lại một từ để kiểm tra kết nối.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    },
}
PROMPT = "Hãy gọi tool `ping` với text = \"xin chào\". Không viết gì khác."


def list_groq_models(api_key: str) -> list[str]:
    req = urllib.request.Request(GROQ_MODELS_URL, headers={
        "Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Không lấy được danh sách model Groq: HTTP {exc.code} {exc.read()[:300]!r}")
    ids = sorted(m["id"] for m in data.get("data", []) if m.get("active", True))
    return [i for i in ids if not any(w in i.lower() for w in SKIP_WORDS)]


def check(api_key: str, model: str, post=_http_post, url: str = GROQ_API_URL) -> tuple[str, str, float]:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "X-Title": "market-agents"}
    body = {"model": model, "messages": [{"role": "user", "content": PROMPT}], "tools": [PING_TOOL]}
    start = time.monotonic()
    try:
        status, data = post(url, headers, body, 120)
    except Exception as exc:  # noqa: BLE001
        return "FAIL", f"lỗi mạng: {exc}", time.monotonic() - start
    elapsed = time.monotonic() - start
    error = data.get("error") if isinstance(data, dict) else None
    if status != 200 or error or not data.get("choices"):
        err = error if isinstance(error, dict) else {}
        meta = err.get("metadata") or {}
        detail = meta.get("raw") or ""
        return "FAIL", f"HTTP {status} {err.get('message', '')} {str(detail)[:200]}".strip(), elapsed
    message = data["choices"][0].get("message") or {}
    calls = message.get("tool_calls") or []
    if calls and (calls[0].get("function") or {}).get("name") == "ping":
        return "OK", f"gọi tool ping({calls[0]['function'].get('arguments', '')})", elapsed
    return "NO_TOOLS", f"trả lời văn bản: {(message.get('content') or '')[:80]!r}", elapsed


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--provider" in argv:  # tương thích lệnh cũ
        i = argv.index("--provider")
        del argv[i:i + 2]
    provider = "groq"
    settings = Settings()
    key, url = settings.groq_api_key, GROQ_API_URL
    if not key:
        print("Thiếu GROQ_API_KEY")
        return 1
    models = argv or list_groq_models(key)
    print(f"Model Groq sẽ kiểm tra: {models}", flush=True)

    rows = []
    for model in models:
        result, note, secs = check(key, model, url=url)
        rows.append((model, result, f"{secs:.1f}s", note))
        print(f"{result:8} {model}  ({secs:.1f}s)  {note}", flush=True)

    lines = [f"## Kiểm tra model ({provider})", "", "| Model | Kết quả | Thời gian | Ghi chú |", "|---|---|---|---|"]
    lines += [f"| `{m}` | {r} | {t} | {n.replace('|', '/')} |" for m, r, t, n in rows]
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    print(json.dumps({m: r for m, r, _, _ in rows}, ensure_ascii=False))
    return 0 if any(r == "OK" for _, r, _, _ in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
