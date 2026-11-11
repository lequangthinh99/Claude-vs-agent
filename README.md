# Hệ thống Agent AI phân tích thị trường

Đội 4 agent AI (mặc định chạy trên **Groq** — miễn phí; có thể chuyển sang **Gemini** hoặc **Claude API**) tự động gửi bản tin thị trường lúc **07:00** và **18:00** (giờ Việt Nam) mỗi ngày.

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
| Thu thập | `market_agents/workers.py` | Chọn lọc và tóm tắt tin vĩ mô, NHTW, USD, vàng, BTC, dầu, kim loại, nông sản, Việt Nam — kèm mã nguồn `[T..]` |
| Phân tích | `market_agents/workers.py` | Kênh truyền dẫn, mức độ ảnh hưởng 1–5, đã phản ánh vào giá chưa, so sánh với báo cáo kỳ trước |
| Nhận định | `market_agents/workers.py` | Điểm sức mạnh −5..+5, xu hướng ngắn/trung hạn, hỗ trợ/kháng cự, kịch bản có xác suất |

**Skill tài chính** nằm ở `market_agents/skills/financial_analysis.md` (kênh truyền dẫn, thang điểm, động lực từng tài sản). Sửa file này để thay đổi cách agent phân tích — không cần sửa code.

**Dữ liệu miễn phí:** trước khi các agent làm việc, `market_agents/datafeeds.py` tự lấy:

| Dữ liệu | Nguồn |
|---|---|
| Giá, chỉ báo dòng tiền (quỹ ETF) | Yahoo Finance |
| VN-Index, VN30 | DNSE (Entrade) |
| Lịch kinh tế 36 giờ tới (giờ VN, dự báo / kỳ trước) | ForexFactory |
| Tin tức | RSS: CNBC, Investing.com, CoinDesk, Cointelegraph, OilPrice, Fed, VnExpress, CafeF, Vietstock |

Thêm/bớt nguồn bằng cách sửa `PRICE_SYMBOLS`, `FLOW_SYMBOLS`, `NEWS_FEEDS`. Kiểm tra từng nguồn: Actions → Run workflow → mode `check-feeds`.

**Phần nào do code làm, phần nào do AI viết** (để hạn chế số liệu bịa):

- **Code dựng thẳng từ dữ liệu gốc:** bảng giá, lịch sự kiện, link nguồn của tin, bảng đối chiếu nhận định kỳ trước.
  Mục nào không có dữ liệu thì bỏ hẳn (ví dụ không lấy được lịch → không có mục Lịch sự kiện).
- **AI viết:** tóm tắt, chọn lọc tin, phân tích tác động, dòng tiền, nhận định và rủi ro. Agent trích nguồn bằng mã
  `[T7]`; code đổi mã thành link thật và bỏ mã không tồn tại.
- **Kiểm tra sau khi sinh:** câu trả lời bị cắt vì quá dài được chạy lại 1 lần với yêu cầu viết ngắn hơn (vẫn cắt thì
  bỏ dòng dở); chữ Hán/Nhật/Hàn lẫn vào bị loại; ở chế độ gọn, lấy được dưới 8 mã giá thì không tạo bản tin.
- **Chưa có:** giá vàng SJC/nhẫn trong nước và số liệu khối ngoại (các nguồn công khai chặn truy cập tự động).
  Bảng giá có dòng *vàng thế giới quy đổi ra triệu đồng/lượng* để tự so với giá trong nước.

**Chấm lại nhận định:** mỗi phiên lưu giá và điểm sức mạnh vào `reports/data/<ngày>-<phiên>.json`. Bản tin sau so chiều
của điểm với biến động giá thực tế và ghi tỷ lệ đúng chiều của 30 phiên gần nhất (`market_agents/tracking.py`).

Mỗi bản tin được lưu vào `reports/` và bản gần nhất được đưa cho agent Phân tích làm bối cảnh, để báo cáo theo dõi được **diễn biến** qua các phiên.

## Cấu trúc báo cáo

1. Tóm tắt nhanh · 2. Nhận định USD · Vàng · BTC · Hàng hoá · 3. Đối chiếu nhận định kỳ trước · 4. Dòng tiền ·
5. Phân tích tác động · 6. Tin chính (kèm link) · 7. Bảng giá · 8. Lịch sự kiện · 9. Rủi ro & lưu ý

## Chọn nhà cung cấp AI

| | Groq (gpt-oss-120b, mặc định) | Gemini Flash | Claude |
|---|---|---|---|
| Chi phí | Miễn phí (giới hạn ~8.000 token/phút) | Có gói miễn phí | Trả phí theo token |
| Tìm web | Không (dùng giá + tin RSS miễn phí) | Gói free thường bị chặn | Web search + đọc trang |
| Key | `GROQ_API_KEY` — console.groq.com/keys | `GEMINI_API_KEY` — aistudio.google.com/apikey | `ANTHROPIC_API_KEY` |

Với Groq, hệ thống tự bật **chế độ gọn** để mỗi request nằm trong giới hạn token/phút của gói miễn phí.
Kiểm tra model Groq nào dùng được: Actions → Run workflow → mode `check-models` (1 request nhỏ mỗi model).

Chỉ cần điền **một** key. Hệ thống tự chọn theo key có sẵn: Claude > Groq > Gemini. Muốn chỉ định rõ thì đặt `MARKET_AGENTS_PROVIDER=groq`, `gemini` hoặc `claude`.

Với Gemini miễn phí: mỗi bản tin dùng khoảng 8–12 request. Gói miễn phí của các model Gemini 3.x có thể không cho dùng Google Search (luôn báo 429); khi đó hệ thống tự bỏ search và dựa vào dữ liệu miễn phí ở trên. Khi bị giới hạn tốc độ (lỗi 429), hệ thống tự chờ rồi thử lại. Hạn mức free thực tế của tài khoản xem trong AI Studio. Lưu ý: dữ liệu gửi qua gói miễn phí có thể được Google dùng để cải thiện sản phẩm.

## Cách 1 — Chạy tự động bằng GitHub Actions (khuyên dùng, không cần máy chủ)

1. Vào **Settings → Secrets and variables → Actions → New repository secret** và thêm:
   - **Một** trong các key: `GROQ_API_KEY` (console.groq.com/keys), `GEMINI_API_KEY` (aistudio.google.com/apikey) hoặc `ANTHROPIC_API_KEY` (console.anthropic.com)
   - Telegram: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
   - và/hoặc Email: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `REPORT_EMAIL_TO`
2. Workflow `.github/workflows/market-report.yml` tạo báo cáo, **giữ lại tới đúng 07:00 / 18:00** mới gửi (nếu xong sớm), rồi commit vào `reports/`. Để gửi đúng giờ cần làm thêm mục **Lịch chạy đúng giờ** bên dưới.
3. Chạy thử ngay: tab **Actions → Bản tin thị trường → Run workflow**.

> Lưu ý: GitHub chỉ chạy lịch (`schedule`) trên **nhánh mặc định** — cần merge code vào nhánh mặc định. Lịch của GitHub có thể khởi động trễ nhiều giờ — xem mục **Lịch chạy đúng giờ**.

### Lấy thông tin Telegram
- Nhắn `/newbot` cho **@BotFather** → nhận `TELEGRAM_BOT_TOKEN`.
- Nhắn một tin bất kỳ cho bot của bạn, rồi nhắn **@userinfobot** để lấy `TELEGRAM_CHAT_ID`.

### Email Gmail
`SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=465`, `SMTP_USER=<gmail của bạn>`, `SMTP_PASSWORD=<App Password>` (tạo ở myaccount.google.com → Bảo mật → Mật khẩu ứng dụng).

### Lịch chạy đúng giờ (quan trọng)

Lịch có sẵn của GitHub (`schedule`) **không đảm bảo**: ở repo này nó đã từng khởi động trễ 3–8 tiếng.
Vì vậy lịch chính dùng **cron-job.org** (miễn phí, chạy đúng phút) để gọi workflow; lịch GitHub
(chạy mỗi giờ) chỉ là dự phòng. **Nếu chưa thiết lập cron-job.org, bản tin vẫn tới nhưng có thể trễ vài tiếng.**

Mọi lần chạy theo lịch đều dùng `--scheduled`, tự quyết định theo giờ thực lúc chạy:

| Giờ VN lúc chạy | Việc làm |
|---|---|
| 05:45–13:00 | Bản tin sáng — xong trước 07:00 thì giữ tới 07:00; sau 07:00 thì gửi ngay, tiêu đề ghi giờ thực |
| 16:45–24:00 | Bản tin chiều — tương tự với mốc 18:00 |
| Ngoài hai khung | Thoát ngay, không gọi AI |

Phiên nào đã có bản tin thì các lần chạy sau tự bỏ qua, không gửi trùng. Nếu tạo xong mà không gửi được
qua kênh nào, lần chạy được tính là lỗi (có cảnh báo) và lần dự phòng kế tiếp sẽ làm lại.
Chỉnh khung bằng `MARKET_AGENTS_MAX_HOLD_MINUTES` (mặc định 75) và `MARKET_AGENTS_MAX_LATE_HOURS` (mặc định 6).

1. Tạo mã truy cập: https://github.com/settings/personal-access-tokens/new
   - Repository access: *Only select repositories* → `Claude-vs-agent`
   - Permissions → Repository → **Actions: Read and write**
   - Hết hạn: 1 năm (nhớ gia hạn; khi hết hạn bot sẽ không chạy).
2. Trên https://cron-job.org tạo 2 job (múi giờ **Asia/Ho_Chi_Minh**), lúc **06:05** và **17:05**:
   - URL: `https://api.github.com/repos/lequangthinh99/Claude-vs-agent/actions/workflows/market-report.yml/dispatches`
   - Method: `POST`
   - Headers: `Authorization: Bearer <mã github_pat_...>` và `Accept: application/vnd.github+json`
   - Body (phiên sáng):
     `{"ref":"claude/ai-agent-market-analysis-ad55hg","inputs":{"mode":"report","session":"morning","scheduled":"yes"}}`
   - Body (phiên chiều): như trên nhưng `"session":"evening"`.
   - Kết quả đúng: cron-job.org báo HTTP **204**.
3. Nếu một lần chạy bị lỗi hoặc bị huỷ vì quá thời gian, bot Telegram nhắn cảnh báo kèm link xem chi tiết.
4. Kiểm tra cron-job.org có hoạt động không: tab **Actions** phải có lần chạy loại *workflow_dispatch* lúc 06:05 và 17:05 mỗi ngày.

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

Hoặc dùng cron (máy đặt giờ VN): `5 6,17 * * * cd /đường/dẫn && .venv/bin/python -m market_agents.main --scheduled`

## Cấu hình

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `MARKET_AGENTS_PROVIDER` | tự chọn | `groq`, `gemini` hoặc `claude` |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Model Groq (dự phòng: `qwen/qwen3.8-27b`, `openai/gpt-oss-20b` qua `GROQ_FALLBACK_MODELS`) |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Model Gemini (vd. đổi sang bản Flash mới hơn nếu tài khoản có) |
| `MARKET_AGENTS_MODEL` | `claude-opus-5-5` | Model Claude |
| `MARKET_AGENTS_TIMEZONE` | `Asia/Ho_Chi_Minh` | Múi giờ báo cáo |
| `MARKET_AGENTS_LEAN` | bật khi dùng Groq | `1` = chế độ gọn: mỗi agent viết ngắn (~1.200 token), Quản lý chỉ đọc tóm tắt; hợp với API giới hạn token/phút thấp |
| (Actions → Run workflow → mode `check-feeds`) | | Kiểm tra từng nguồn giá, dòng tiền, tin RSS miễn phí — không gọi AI |
| `MARKET_AGENTS_DATAFEEDS` | `1` | `0` để tắt lấy giá/tin RSS miễn phí |
| `MARKET_AGENTS_LEAD_MINUTES` | `55` | Scheduler bắt đầu sớm bao nhiêu phút trước giờ gửi |
| `MARKET_AGENTS_MAX_HOLD_MINUTES` | `75` | Giữ bản tin chờ giờ gửi tối đa bao nhiêu phút; `--scheduled` tới sớm hơn mức này thì thoát |
| `MARKET_AGENTS_MAX_LATE_HOURS` | `6` | `--scheduled` tới trễ hơn giờ gửi quá mức này thì bỏ qua phiên |

## Chi phí ước tính

**Gemini:** miễn phí trong hạn mức free tier. **Claude:** mỗi bản tin gồm ~25–35 lượt tìm kiếm web và 4 agent. Ước tính thô **khoảng 1–3 USD/bản tin** (≈ 60–180 USD/tháng cho 2 bản/ngày) — con số chưa đo thực tế; hãy xem mục Usage trên console.anthropic.com sau vài ngày chạy. Giảm chi phí: hạ `max_uses` của web search trong `workers.py`, đặt `MARKET_AGENTS_MODEL=claude-sonnet-5-5`, hoặc chuyển sang Gemini.

## Kiểm thử

```bash
pip install -r requirements-dev.txt
pytest -q
```
Bộ test dùng client giả, không gọi API thật.

---
_Báo cáo do AI tạo chỉ mang tính tham khảo, không phải khuyến nghị đầu tư._
