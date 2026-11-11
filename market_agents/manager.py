"""Agent quản lý: lập kế hoạch, giao việc cho 3 agent nhân viên, kiểm duyệt và viết báo cáo cuối."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import datafeeds
from .datafeeds import MarketData
from .llm import Agent, AgentError, Backend, CustomTool
from .workers import build_analyst, build_collector, build_strategist

log = logging.getLogger(__name__)

MANAGER_SYSTEM = """\
Bạn là **Agent Quản lý** của một đội phân tích thị trường tài chính, báo cáo trực tiếp cho chủ đầu tư.
Bạn điều phối 3 agent nhân viên thông qua các tool:

1. `assign_collector` — Agent Thu thập: tìm kiếm web, thu thập tin tức & giá thị trường mới nhất.
2. `assign_analyst` — Agent Phân tích: phân tích diễn biến và mức độ ảnh hưởng của thông tin
   (tự động nhận dữ liệu mới nhất từ Agent Thu thập và báo cáo kỳ trước).
3. `assign_strategist` — Agent Nhận định: đánh giá độ mạnh USD, vàng, BTC, hàng hoá
   (tự động nhận kết quả của Thu thập và Phân tích).

Quy trình:
- Giao việc theo thứ tự Thu thập → Phân tích → Nhận định. Trong `instructions`, nêu rõ trọng tâm của
  phiên (sáng hay chiều), khoảng thời gian cần bao phủ và những điểm cần chú ý.
- Kiểm duyệt kết quả mỗi bước. Nếu thiếu mảng quan trọng (ví dụ không có giá vàng trong nước, thiếu
  dữ liệu BTC, thiếu lịch sự kiện) hoặc có mâu thuẫn rõ ràng, giao lại cho đúng agent với yêu cầu
  bổ sung cụ thể. Mỗi agent được giao lại tối đa 1 lần.
- Khi đã đủ, viết BÁO CÁO CUỐI CÙNG làm câu trả lời của bạn (không gọi thêm tool).

Báo cáo cuối (Markdown, tiếng Việt, đọc trên điện thoại) theo đúng cấu trúc và thứ tự:
# {tiêu đề do người dùng cung cấp}
## 📌 Tóm tắt nhanh
5–7 dòng, mỗi dòng 1 ý quan trọng nhất.
## 🎯 Nhận định: USD · Vàng · BTC · Hàng hoá
Bảng sức mạnh, rồi mỗi tài sản một mục "### <biểu tượng> Tên — Điểm · Xu hướng" với các dòng
**Lý do**, **Hỗ trợ / Kháng cự**, **Kịch bản chính**, **Sai nếu**.
## 💸 Dòng tiền: Thế giới · Việt Nam
Thế giới: đang vào / đang rút / xu hướng xoay vòng. Việt Nam: khối ngoại, thanh khoản, ngành hút tiền.
## 📰 Tin chính & mức độ ảnh hưởng
Mỗi tin 1 dòng: "- **Tin (Mức độ 4/5):** ảnh hưởng tới tài sản nào [T12]" — giữ nguyên mã nguồn
[T..] hoặc link mà nhân viên đã ghi; không tự viết URL.
## 🔍 Diễn biến & tâm lý thị trường
## 💵 Bảng giá
## 📅 Lịch sự kiện
## ⚠️ Rủi ro & lưu ý
Kết thúc bằng dòng: _Báo cáo do hệ thống AI tạo, chỉ mang tính tham khảo, không phải khuyến nghị đầu tư._

QUY TẮC TRÌNH BÀY: chỉ dùng gạch đầu dòng, mỗi dòng 1 ý, tối đa khoảng 20 từ, bắt đầu bằng nhãn
in đậm; không viết đoạn văn; số liệu gọn (4.100 USD, +2,5%).

Không bịa số liệu: chỉ dùng thông tin từ các agent nhân viên. Lịch sự kiện chỉ gồm những sự kiện
nhân viên đã nêu; không tự thêm sự kiện hay giờ công bố.
"""

# Chế độ gọn (cho API giới hạn token/phút thấp như Groq free): Quản lý chỉ thấy bản tóm tắt
# kết quả của nhân viên và chỉ viết phần tóm tắt + rủi ro; các phần chi tiết được ghép tự động.
MANAGER_LEAN_ADDENDUM = """

CHẾ ĐỘ GỌN: kết quả tool bạn nhận được chỉ là phần đầu của báo cáo nhân viên (bản đầy đủ đã được lưu
và sẽ tự động ghép vào báo cáo). Vì vậy câu trả lời cuối cùng của bạn CHỈ gồm 2 phần sau, tối đa 300 từ:
## 📌 Tóm tắt nhanh
5–7 dòng, mỗi dòng 1 ý, tối đa khoảng 20 từ, bắt đầu bằng nhãn in đậm.
## ⚠️ Rủi ro & lưu ý
2–4 dòng ngắn.
Không viết tiêu đề báo cáo và không lặp lại các phần khác.
Bảng giá, lịch sự kiện và link nguồn do hệ thống tự chèn từ dữ liệu gốc: KHÔNG nhắc tới lịch sự kiện
hay ngày giờ công bố trong phần tóm tắt, không tự quy đổi đơn vị/tiền tệ, và không giao lại việc chỉ vì
kết quả không có bảng giá hay lịch. Mỗi agent chỉ được giao 1 lần."""
LEAN_PREVIEW_CHARS = 700
LEAN_PREVIOUS_REPORT_CHARS = 1500
DISCLAIMER = "_Báo cáo do hệ thống AI tạo, chỉ mang tính tham khảo, không phải khuyến nghị đầu tư._"

_INSTRUCTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "instructions": {
            "type": "string",
            "description": "Chỉ dẫn cụ thể cho agent nhân viên: trọng tâm, phạm vi thời gian, yêu cầu bổ sung.",
        }
    },
    "required": ["instructions"],
    "additionalProperties": False,
}


# Giờ gửi danh nghĩa của từng phiên (giờ, phút) theo múi giờ báo cáo.
SLOTS: dict[str, tuple[int, int]] = {"morning": (7, 0), "evening": (18, 0)}
# Tạo bản tin trễ hơn giờ gửi quá chừng này thì tiêu đề ghi giờ thực thay vì giờ danh nghĩa.
LATE_TITLE_AFTER = timedelta(minutes=20)


@dataclass
class Session:
    kind: str  # "morning" | "evening"
    now: datetime
    previous_report: str | None = None
    market_data: str | None = None  # giá + tin RSS lấy miễn phí bằng code (Markdown cho agent đọc)
    data: MarketData | None = None  # cùng dữ liệu ở dạng có cấu trúc: dựng bảng và link trong báo cáo
    scorecard: str = ""             # bảng đối chiếu nhận định kỳ trước (do code chấm)

    @property
    def label(self) -> str:
        return "Bản tin sáng" if self.kind == "morning" else "Bản tin chiều"

    @property
    def slot(self) -> str:
        """Giờ gửi danh nghĩa của phiên."""
        h, m = SLOTS[self.kind]
        return f"{h:02d}:{m:02d}"

    @property
    def title(self) -> str:
        h, m = SLOTS[self.kind]
        slot = self.now.replace(hour=h, minute=m, second=0, microsecond=0)
        shown = f"{self.now:%H:%M}" if self.now > slot + LATE_TITLE_AFTER else self.slot
        return f"{self.label} {shown} — ngày {self.now:%d/%m/%Y}"

    @property
    def focus(self) -> str:
        if self.kind == "morning":
            return (
                "Phiên SÁNG: bao phủ diễn biến từ khoảng 18:00 hôm qua đến hiện tại (giờ Việt Nam) — "
                "phiên Mỹ và châu Âu đêm qua, mở cửa châu Á sáng nay — và lịch sự kiện trong ngày hôm nay."
            )
        return (
            "Phiên CHIỀU: bao phủ diễn biến từ khoảng 07:00 sáng nay đến hiện tại (giờ Việt Nam) — "
            "phiên châu Á, mở cửa châu Âu — và các sự kiện/dữ liệu Mỹ sẽ công bố tối và đêm nay."
        )

    def context(self) -> str:
        return (
            f"Thời điểm hiện tại: {self.now:%A %d/%m/%Y %H:%M} (giờ Việt Nam, {self.now.tzname()}).\n"
            f"{self.focus}"
        )


@dataclass
class Workspace:
    """Kết quả làm việc của các agent nhân viên trong một phiên."""

    collector: str | None = None
    analyst: str | None = None
    strategist: str | None = None
    log: list[str] = field(default_factory=list)


def split_section(text: str, marker: str) -> tuple[str, str]:
    """Tách mục bắt đầu bằng `marker` (vd "### 💸") ra khỏi văn bản.

    Trả về (nội dung mục, không kèm dòng tiêu đề; phần còn lại)."""
    lines = text.split("\n")
    start = next((i for i, l in enumerate(lines) if l.strip().startswith(marker)), None)
    if start is None:
        return "", text
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("### ") or lines[i].startswith("## ")), len(lines))
    section = "\n".join(lines[start + 1:end]).strip()
    rest = "\n".join(lines[:start] + lines[end:]).strip()
    return section, rest


_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]+")
SCORECARD_HEADING = "## 🧮 Đối chiếu nhận định kỳ trước"
RETRY_SHORTER = ("\n\nLƯU Ý: câu trả lời trước của bạn bị cắt giữa chừng vì quá dài. Viết lại ngắn hơn "
                 "khoảng 30%, vẫn đủ mọi mục theo đúng khuôn, và kết thúc trọn vẹn.")


def strip_foreign_script(text: str) -> str:
    """Bỏ chữ Hán/Nhật/Hàn mà model nhỏ thỉnh thoảng chèn lẫn vào câu tiếng Việt."""
    return re.sub(r"[ \t]{2,}", " ", _CJK.sub("", text)) if _CJK.search(text) else text


def drop_cut_tail(text: str) -> str:
    """Bỏ dòng cuối bị cắt dở (khi model chạm giới hạn output)."""
    lines = text.rstrip().split("\n")
    if len(lines) > 1:
        lines.pop()
    while lines and (not lines[-1].strip() or lines[-1].lstrip().startswith(("#", "|---"))
                     or lines[-1].rstrip().endswith(":")):
        lines.pop()  # tiêu đề / dòng dẫn không còn nội dung phía sau
    return "\n".join(lines)


def clean_news(text: str, data: MarketData) -> str:
    """Giữ lại các dòng tin có mã nguồn hợp lệ, mỗi mã 1 lần; bỏ dòng tự chế (không ứng với tin RSS nào).

    Tiêu đề nhóm chuyển thành chữ đậm; nhóm không còn tin nào thì bỏ."""
    known, used = data.links(), set()
    out: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("-", "*", "•")) and not stripped.startswith("**"):
            refs = [r for r in re.findall(r"\bT\d+\b", stripped) if r in known]
            if not refs or refs[0] in used:
                continue
            used.update(refs)
            out.append(stripped)
            continue
        title = re.sub(r"^#+\s*", "", stripped).strip("* ").strip()
        if title:
            if out and out[-1].startswith("**"):
                out.pop()  # nhóm trước không có tin
            out.append(f"**{title}**")
    if out and out[-1].startswith("**"):
        out.pop()
    return "\n".join(out)


def without_heading(text: str) -> str:
    """Bỏ dòng tiêu đề Markdown ở đầu (phần ghép vào báo cáo đã có tiêu đề riêng)."""
    lines = text.strip().split("\n")
    while lines and (lines[0].startswith("#") or not lines[0].strip()):
        lines.pop(0)
    return "\n".join(lines)


class ManagerAgent:
    def __init__(self, backend: Backend, lean: bool = False):
        self.backend = backend
        self.lean = lean
        self.collector = build_collector(lean)
        self.analyst = build_analyst(lean)
        self.strategist = build_strategist(lean)

    def _evidence(self, session: Session, ws: Workspace) -> str:
        """Dữ liệu đưa cho Phân tích / Nhận định. Ở chế độ gọn có dữ liệu tự động, Thu thập chỉ
        trả về tin đã chọn lọc, nên phải kèm thêm các bảng do code dựng."""
        if self.lean and session.data:
            return f"{datafeeds.render_tables(session.data)}\n\n### Tin chính (đã chọn lọc)\n{ws.collector}"
        return ws.collector or ""

    def _for_manager(self, text: str) -> str:
        """Kết quả trả về cho Agent Quản lý (rút gọn ở chế độ gọn)."""
        if not self.lean or len(text) <= LEAN_PREVIEW_CHARS:
            return text
        return text[:LEAN_PREVIEW_CHARS] + "\n…(phần còn lại đã được lưu và sẽ ghép vào báo cáo)"

    def run(self, session: Session) -> str:
        """Tạo báo cáo rồi hậu xử lý bằng code: đổi mã nguồn thành link thật, bỏ ký tự lạ."""
        report = strip_foreign_script(datafeeds.resolve_refs(self._run(session), session.data))
        if session.scorecard and SCORECARD_HEADING not in report:  # chế độ thường: chèn trước phần cuối
            block = f"{SCORECARD_HEADING}\n\n{session.scorecard}\n\n"
            cut = report.find("## ⚠️")
            if cut < 0:
                cut = report.find(DISCLAIMER)
            report = report[:cut] + block + report[cut:] if cut >= 0 else f"{report}\n\n{block.strip()}"
        return report

    def _run(self, session: Session) -> str:
        ws = Workspace()
        self.collector = build_collector(self.lean, structured=session.data is not None)

        def once(done: str | None) -> str:
            """Chế độ gọn: Quản lý chỉ thấy đoạn đầu kết quả nên không đủ cơ sở yêu cầu làm lại;
            mỗi lần giao lại còn tốn thêm hạn mức token/phút. Việc đã xong thì không chạy lại."""
            if self.lean and done:
                return "Việc này đã hoàn thành và kết quả đã được lưu. Không giao lại; hãy làm bước tiếp theo."
            return ""

        def assign_collector(data: dict) -> str:
            feed = session.market_data or "(Không có dữ liệu tự động.)"
            prompt = (
                f"{session.context()}\n\n"
                f"<du_lieu_tu_dong>\n{feed}\n</du_lieu_tu_dong>\n\n"
                f"Chỉ dẫn từ Agent Quản lý:\n{data['instructions']}"
            )
            if once(ws.collector):
                return once(ws.collector)
            ws.collector = self._run_worker(self.collector, prompt, ws)
            if self.lean and session.data:
                ws.collector = clean_news(ws.collector, session.data) or ws.collector
            return self._for_manager(ws.collector)

        def assign_analyst(data: dict) -> str:
            if not ws.collector:
                raise AgentError("Chưa có dữ liệu thu thập. Hãy gọi assign_collector trước.")
            prev = session.previous_report or "(Không có báo cáo kỳ trước.)"
            if self.lean and len(prev) > LEAN_PREVIOUS_REPORT_CHARS:
                prev = prev[:LEAN_PREVIOUS_REPORT_CHARS] + "\n…(chỉ trích phần đầu)"
            prompt = (
                f"{session.context()}\n\n"
                f"<du_lieu_thu_thap>\n{self._evidence(session, ws)}\n</du_lieu_thu_thap>\n\n"
                f"<bao_cao_ky_truoc>\n{prev}\n</bao_cao_ky_truoc>\n\n"
                f"Chỉ dẫn từ Agent Quản lý:\n{data['instructions']}"
            )
            if once(ws.analyst):
                return once(ws.analyst)
            ws.analyst = self._run_worker(self.analyst, prompt, ws)
            return self._for_manager(ws.analyst)

        def assign_strategist(data: dict) -> str:
            if not ws.collector or not ws.analyst:
                raise AgentError("Cần có kết quả Thu thập và Phân tích trước khi Nhận định.")
            prompt = (
                f"{session.context()}\n\n"
                f"<du_lieu_thu_thap>\n{self._evidence(session, ws)}\n</du_lieu_thu_thap>\n\n"
                f"<phan_tich_tac_dong>\n{ws.analyst}\n</phan_tich_tac_dong>\n\n"
                f"Chỉ dẫn từ Agent Quản lý:\n{data['instructions']}"
            )
            if once(ws.strategist):
                return once(ws.strategist)
            ws.strategist = self._run_worker(self.strategist, prompt, ws)
            return self._for_manager(ws.strategist)

        manager = Agent(
            name="Quản lý",
            system=MANAGER_SYSTEM + (MANAGER_LEAN_ADDENDUM if self.lean else ""),
            effort="medium",
            max_turns=20,
            max_tokens=1500 if self.lean else 64000,
            custom_tools=[
                CustomTool(
                    "assign_collector",
                    "Giao việc cho Agent Thu thập (tìm kiếm web tin tức và giá thị trường). "
                    "Trả về dữ liệu đã thu thập.",
                    _INSTRUCTIONS_SCHEMA,
                    assign_collector,
                ),
                CustomTool(
                    "assign_analyst",
                    "Giao việc cho Agent Phân tích (phân tích diễn biến và mức độ ảnh hưởng). "
                    "Cần gọi assign_collector trước. Trả về bản phân tích.",
                    _INSTRUCTIONS_SCHEMA,
                    assign_analyst,
                ),
                CustomTool(
                    "assign_strategist",
                    "Giao việc cho Agent Nhận định (độ mạnh USD, vàng, BTC, hàng hoá). "
                    "Cần có kết quả Thu thập và Phân tích trước. Trả về bản nhận định.",
                    _INSTRUCTIONS_SCHEMA,
                    assign_strategist,
                ),
            ],
        )

        prompt = (
            f"{session.context()}\n\n"
            f"Tiêu đề báo cáo: {session.title}\n\n"
            "Hãy điều phối đội ngũ và hoàn thành báo cáo cho phiên này."
        )
        result = self.backend.run(manager, prompt)
        if ws.collector and ws.analyst and ws.strategist:
            return self._finish(session, ws, result.text)

        # Một số model không gọi tool đầy đủ: tự chạy các bước còn thiếu theo thứ tự,
        # rồi nhờ Agent Quản lý (không có tool) viết báo cáo tổng hợp.
        log.warning("Agent quản lý chưa giao đủ 3 việc; chạy quy trình cố định cho các bước còn thiếu.")
        default = {"instructions": "Thực hiện đầy đủ nhiệm vụ của bạn cho phiên này."}
        try:
            if not ws.collector:
                assign_collector(default)
            if not ws.analyst:
                assign_analyst(default)
            if not ws.strategist:
                assign_strategist(default)
            writer = Agent(
                name="Quản lý (tổng hợp)",
                system=MANAGER_SYSTEM + (MANAGER_LEAN_ADDENDUM if self.lean else ""),
                effort="medium", max_turns=3, max_tokens=1500 if self.lean else 64000,
            )
            final = self.backend.run(writer, (
                f"{session.context()}\n\nTiêu đề báo cáo: {session.title}\n\n"
                "Các agent nhân viên đã hoàn thành. Hãy viết BÁO CÁO CUỐI CÙNG từ kết quả dưới đây.\n\n"
                f"<du_lieu_thu_thap>\n{self._for_manager(ws.collector)}\n</du_lieu_thu_thap>\n\n"
                f"<phan_tich_tac_dong>\n{self._for_manager(ws.analyst)}\n</phan_tich_tac_dong>\n\n"
                f"<nhan_dinh>\n{self._for_manager(ws.strategist)}\n</nhan_dinh>"
            ))
            return self._finish(session, ws, final.text)
        except AgentError:
            log.exception("Quy trình cố định thất bại; ghép báo cáo dự phòng.")
            return self._fallback_report(session, ws, result.text)

    def _finish(self, session: Session, ws: Workspace, manager_text: str) -> str:
        """Chế độ thường: báo cáo do Quản lý viết. Chế độ gọn: ghép tóm tắt + các phần chi tiết."""
        if not self.lean:
            return manager_text
        summary, risks = manager_text.replace(DISCLAIMER, "").strip(), ""
        for marker in ("## ⚠️", "## Rủi ro"):
            if marker in summary:  # đưa phần rủi ro xuống cuối báo cáo
                summary, rest = summary.split(marker, 1)
                risks = marker + rest
                break
        flows, analysis = split_section(ws.analyst or "", "### 💸")
        parts = [f"# {session.title}", "", summary.strip(), ""]
        data = session.data
        sections = [
            ("## 🎯 Nhận định: USD · Vàng · BTC · Hàng hoá", ws.strategist),
            (SCORECARD_HEADING, session.scorecard),
            ("## 💸 Dòng tiền: Thế giới · Việt Nam", flows),
            ("## 🔍 Phân tích tác động", analysis),
        ]
        if data:  # bảng giá và lịch do code dựng từ dữ liệu gốc; mục nào không có dữ liệu thì bỏ hẳn
            sections += [
                ("## 📰 Tin chính", without_heading(ws.collector or "")),
                ("## 💵 Bảng giá", "\n".join(datafeeds.render_price_list(data))),
                ("## 📅 Lịch sự kiện", "\n".join(datafeeds.render_calendar(data))),
            ]
        else:
            sections.append(("## 📰 Dữ liệu & tin chính", ws.collector))
        for heading, body in sections:
            if body and body.strip():
                parts += [heading, "", body.strip(), ""]
        if risks:
            parts += [risks.strip(), ""]
        parts.append(DISCLAIMER)
        return "\n".join(parts)

    def _run_worker(self, agent: Agent, prompt: str, ws: Workspace) -> str:
        ws.log.append(agent.name)
        result = self.backend.run(agent, prompt)
        if result.truncated:  # bị cắt giữa chừng: thử lại 1 lần với yêu cầu viết ngắn hơn
            log.warning("[%s] kết quả bị cắt, thử lại với yêu cầu viết ngắn hơn", agent.name)
            retry = self.backend.run(agent, prompt + RETRY_SHORTER)
            if not retry.truncated:
                return retry.text
            longer = max(result.text, retry.text, key=len)
            return drop_cut_tail(longer)
        return result.text

    @staticmethod
    def _fallback_report(session: Session, ws: Workspace, manager_text: str) -> str:
        parts = [f"# {session.title}", "", "> Báo cáo ghép tự động từ kết quả các agent nhân viên."]
        for heading, body in (
            ("Ghi chú của Agent Quản lý", manager_text),
            ("Dữ liệu thị trường", ws.collector),
            ("Phân tích tác động", ws.analyst),
            ("Nhận định USD · Vàng · BTC · Hàng hoá", ws.strategist),
        ):
            if body:
                parts += ["", f"## {heading}", "", body]
        parts += ["", "_Báo cáo do hệ thống AI tạo, chỉ mang tính tham khảo, không phải khuyến nghị đầu tư._"]
        return "\n".join(parts)
