# Hệ thống Agent AI phân tích thị trường

Đội 4 agent AI (chạy trên Claude API) tự động gửi bản tin thị trường lúc **07:00** và **18:00** (giờ Việt Nam) mỗi ngày.

```
                        ┌──────────────────────────┐
                        │   AGENT QUẢN LÝ          │  lập kế hoạch, giao việc,
                        │   (manager.py)           │  kiểm duyệt, viết báo cáo cuối
                        └──────┬──────┬──────┬─────┘
          assign_collector     │      │      │    assign_strategist
                  ┌────────────┘      │      └──────────────┐
                  ▼                   ▼ assign_analyst      ▼
   ┌──────────────────────┐ ┌──────────────────────┐ ┌──────────────────────────┐
   │ 1. THU THẬP          │ │ 2. PHÂN TÍCH         │ │ 3. NHẬN ĐỊNH             │
   │ web search/fetch:    │→│ skill tài chính:     │→│ độ mạnh USD, Vàng, BTC,  │
   │ tin tức, giá, lịch   │ │ diễn biến, mức độ    │ │ hàng hoá (−5..+5),       │
   │ sự kiện              │ │ ảnh hưởng (1–5)      │ │ ngưỡng giá, kịch bản     │
   └──────────────────────┘ └──────────────────────┘ └──────────────────────────┘
                                        │
                                        ▼
                       Telegram · Email · reports/YYYY-MM-DD-<phiên>.md
```

| Agent | File | Nhiệm vụ |
|---|---|---|
| Quản lý | `market_agents/manager.py` | Giao việc theo thứ tự, kiểm tra chất lượng, yêu cầu làm lại nếu thiếu (tối đa 1 lần/agent), tổng hợp báo cáo |
| Thu thập | `market_agents/workers.py` | Tìm tin vĩ mô, NHTW, USD, vàng (cả SJC/nhẫn), BTC, dầu, kim loại, nông sản, lịch sự kiện — kèm nguồn |
| Phân tích | `market_agents/workers.py` | Kênh truyền dẫn, mức độ ảnh hưởng 1–5, đã phản ánh vào giá chưa, so sánh với báo cáo kỳ trước |
| Nhận định | `market_agents/workers.py` | Điểm sức mạnh −5..+5, xu hướng ngắn/trung hạn, hỗ trợ/kháng cự, kịch bản có xác suất |

**Skill tài chính** nằm ở `market_agents/skills/financial_analysis.md` (kênh truyền dẫn, thang điểm, động lực từng tài sản). Sửa file này để thay đổi cách agent phân tích — không cần sửa code.

Mỗi bản tin được lưu vào `reports/` và bản gần nhất được đưa cho agent Phân tích làm bối cảnh, để báo cáo theo dõi được **diễn biến** qua các phiên.

## Cấu trúc báo cáo

1. Tóm tắt nhanh · 2. Bảng giá tham chiếu · 3. Tin chính & mức độ ảnh hưởng · 4. Phân tích diễn biến ·
5. Nhận định USD · Vàng · BTC · Hàng hoá · 6. Lịch sự kiện · 7. Rủi ro & lưu ý

## Cách 1 — Chạy tự động bằng GitHub Actions (khuyên dùng, không cần máy chủ)

1. Vào **Settings → Secrets and variables → Actions → New repository secret** và thêm:
   - `ANTHROPIC_API_KEY` (bắt buộc — lấy tại console.anthropic.com)
   - Telegram: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
   - và/hoặc Email: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `REPORT_EMAIL_TO`
2. Workflow `.github/workflows/market-report.yml` chạy lúc 06:35 và 17:35 giờ VN, tạo báo cáo rồi **giữ lại tới đúng 07:00 / 18:00** mới gửi, sau đó commit báo cáo vào `reports/`.
3. Chạy thử ngay: tab **Actions → Bản tin thị trường → Run workflow**.

> Lưu ý: GitHub chỉ chạy lịch (`schedule`) trên **nhánh mặc định** — cần merge code vào nhánh mặc định. GitHub đôi khi khởi động lịch trễ 5–15 phút vào giờ cao điểm; nếu muốn đúng giờ tuyệt đối, dùng Cách 2.

### Lấy thông tin Telegram
- Nhắn `/newbot` cho **@BotFather** → nhận `TELEGRAM_BOT_TOKEN`.
- Nhắn một tin bất kỳ cho bot của bạn, rồi nhắn **@userinfobot** để lấy `TELEGRAM_CHAT_ID`.

### Email Gmail
`SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=465`, `SMTP_USER=<gmail của bạn>`, `SMTP_PASSWORD=<App Password>` (tạo ở myaccount.google.com → Bảo mật → Mật khẩu ứng dụng).

## Cách 2 — Chạy trên máy tính / VPS

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # điền API key và kênh gửi
set -a && source .env && set +a

python -m market_agents.main --no-send            # chạy thử 1 lần, chỉ lưu file
python -m market_agents.main --session morning    # chạy phiên sáng và gửi ngay
python -m market_agents.scheduler                 # chạy nền: tự gửi 07:00 và 18:00 mỗi ngày
```

Hoặc dùng cron (máy đặt giờ VN): `35 6,17 * * * cd /đường/dẫn && .venv/bin/python -m market_agents.main --hold`

## Cấu hình

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `MARKET_AGENTS_MODEL` | `claude-opus-5-5` | Model cho tất cả agent |
| `MARKET_AGENTS_TIMEZONE` | `Asia/Ho_Chi_Minh` | Múi giờ báo cáo |
| `MARKET_AGENTS_LEAD_MINUTES` | `25` | Scheduler bắt đầu sớm bao nhiêu phút trước giờ gửi |

## Chi phí ước tính

Mỗi bản tin gồm ~25–35 lượt tìm kiếm web và 4 agent. Ước tính thô **khoảng 1–3 USD/bản tin** (≈ 60–180 USD/tháng cho 2 bản/ngày) — con số chưa đo thực tế; hãy xem mục Usage trên console.anthropic.com sau vài ngày chạy. Giảm chi phí: hạ `max_uses` của web search trong `workers.py`, hoặc đặt `MARKET_AGENTS_MODEL=claude-sonnet-5-5`.

## Kiểm thử

```bash
pip install -r requirements-dev.txt
pytest -q
```
Bộ test dùng client giả, không gọi API thật.

---
_Báo cáo do AI tạo chỉ mang tính tham khảo, không phải khuyến nghị đầu tư._
