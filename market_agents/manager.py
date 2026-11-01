"""Agent quản lý: lập kế hoạch, giao việc cho 3 agent nhân viên, kiểm duyệt và viết báo cáo cuối."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

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

Báo cáo cuối (Markdown, tiếng Việt, súc tích, dễ đọc trên điện thoại) theo đúng cấu trúc:
# {tiêu đề do người dùng cung cấp}
## 1. Tóm tắt nhanh
5–7 gạch đầu dòng quan trọng nhất.
## 2. Bảng giá tham chiếu
## 3. Tin tức chính & mức độ ảnh hưởng
Các tin quan trọng nhất kèm mức độ (1–5) và tài sản bị ảnh hưởng; giữ nguồn.
## 4. Phân tích diễn biến
## 5. Nhận định: USD · Vàng · BTC · Hàng hoá
Bảng sức mạnh, rồi nhận định ngắn gọn từng tài sản (xu hướng, ngưỡng giá, kịch bản chính).
## 6. Lịch sự kiện cần theo dõi
## 7. Rủi ro & lưu ý
Kết thúc bằng dòng: _Báo cáo do hệ thống AI tạo, chỉ mang tính tham khảo, không phải khuyến nghị đầu tư._

Không bịa số liệu: chỉ dùng thông tin từ các agent nhân viên.
"""

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


@dataclass
class Session:
    kind: str  # "morning" | "evening"
    now: datetime
    previous_report: str | None = None
    market_data: str | None = None  # giá + tin RSS lấy miễn phí bằng code

    @property
    def label(self) -> str:
        return "Bản tin sáng" if self.kind == "morning" else "Bản tin chiều"

    @property
    def slot(self) -> str:
        """Giờ gửi danh nghĩa của phiên."""
        return "07:00" if self.kind == "morning" else "18:00"

    @property
    def title(self) -> str:
        return f"{self.label} {self.slot} — ngày {self.now:%d/%m/%Y}"

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


class ManagerAgent:
    def __init__(self, backend: Backend):
        self.backend = backend
        self.collector = build_collector()
        self.analyst = build_analyst()
        self.strategist = build_strategist()

    def run(self, session: Session) -> str:
        ws = Workspace()

        def assign_collector(data: dict) -> str:
            feed = session.market_data or "(Không có dữ liệu tự động.)"
            prompt = (
                f"{session.context()}\n\n"
                f"<du_lieu_tu_dong>\n{feed}\n</du_lieu_tu_dong>\n\n"
                f"Chỉ dẫn từ Agent Quản lý:\n{data['instructions']}"
            )
            ws.collector = self._run_worker(self.collector, prompt, ws)
            return ws.collector

        def assign_analyst(data: dict) -> str:
            if not ws.collector:
                raise AgentError("Chưa có dữ liệu thu thập. Hãy gọi assign_collector trước.")
            prev = session.previous_report or "(Không có báo cáo kỳ trước.)"
            prompt = (
                f"{session.context()}\n\n"
                f"<du_lieu_thu_thap>\n{ws.collector}\n</du_lieu_thu_thap>\n\n"
                f"<bao_cao_ky_truoc>\n{prev}\n</bao_cao_ky_truoc>\n\n"
                f"Chỉ dẫn từ Agent Quản lý:\n{data['instructions']}"
            )
            ws.analyst = self._run_worker(self.analyst, prompt, ws)
            return ws.analyst

        def assign_strategist(data: dict) -> str:
            if not ws.collector or not ws.analyst:
                raise AgentError("Cần có kết quả Thu thập và Phân tích trước khi Nhận định.")
            prompt = (
                f"{session.context()}\n\n"
                f"<du_lieu_thu_thap>\n{ws.collector}\n</du_lieu_thu_thap>\n\n"
                f"<phan_tich_tac_dong>\n{ws.analyst}\n</phan_tich_tac_dong>\n\n"
                f"Chỉ dẫn từ Agent Quản lý:\n{data['instructions']}"
            )
            ws.strategist = self._run_worker(self.strategist, prompt, ws)
            return ws.strategist

        manager = Agent(
            name="Quản lý",
            system=MANAGER_SYSTEM,
            effort="medium",
            max_turns=20,
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

        if not (ws.collector and ws.analyst and ws.strategist):
            log.warning("Agent quản lý kết thúc khi chưa đủ 3 bước; ghép báo cáo dự phòng.")
            return self._fallback_report(session, ws, result.text)
        return result.text

    def _run_worker(self, agent: Agent, prompt: str, ws: Workspace) -> str:
        ws.log.append(agent.name)
        result = self.backend.run(agent, prompt)
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
