# Skill: Phân tích tài chính vĩ mô & liên thị trường

Bộ khung này được nạp vào system prompt của agent phân tích và agent nhận định.
Sửa file này để thay đổi "kỹ năng" của các agent mà không cần sửa code.

## 1. Kênh truyền dẫn tin tức → thị trường

Với mỗi tin, xác định nó tác động qua kênh nào (có thể nhiều kênh):

| Kênh | Câu hỏi cần trả lời | Ví dụ |
|---|---|---|
| Lãi suất / chính sách tiền tệ | Tin làm kỳ vọng lãi suất Fed (và ECB, BoJ, PBoC) thay đổi thế nào? Lợi suất thực (TIPS 10Y) đi đâu? | CPI cao hơn dự báo → kỳ vọng cắt giảm lùi lại → USD↑, vàng↓, BTC↓ |
| Tăng trưởng | Dữ liệu cho thấy kinh tế tăng tốc hay suy yếu? | PMI Trung Quốc yếu → đồng, dầu↓ |
| Thanh khoản | Bảng cân đối NHTW, M2, TGA, reverse repo, dòng tiền ETF | Fed dừng QT → tài sản rủi ro↑ |
| Khẩu vị rủi ro | VIX, chứng khoán Mỹ, chênh lệch tín dụng | Bán tháo cổ phiếu → USD, JPY, vàng↑ ngắn hạn; BTC↓ |
| Cung – cầu hàng hoá | OPEC+, tồn kho EIA, thời tiết, xuất khẩu | Tồn kho dầu giảm mạnh → dầu↑ |
| Địa chính trị | Xung đột, trừng phạt, bầu cử, thuế quan | Leo thang Trung Đông → dầu, vàng↑ |
| Quy định / cấu trúc | Luật crypto, ETF, thuế, kiểm soát vốn | ETF BTC được duyệt → BTC↑ |
| Dòng tiền NHTW | Mua vàng của NHTW, can thiệp tỷ giá | PBoC mua vàng tháng thứ N → hỗ trợ đáy vàng |

## 2. Thang điểm mức độ ảnh hưởng (1–5)

- **5 – Rất mạnh**: thay đổi xu hướng trung hạn hoặc gây biến động > 2 độ lệch chuẩn ngày (quyết định FOMC bất ngờ, NFP/CPI lệch lớn, sự kiện địa chính trị lớn).
- **4 – Mạnh**: dịch chuyển rõ rệt trong phiên, có thể kéo dài vài ngày.
- **3 – Trung bình**: tác động đáng kể nhưng có thể bị tin khác lấn át.
- **2 – Yếu**: chủ yếu ảnh hưởng tâm lý, hoặc phần lớn đã được phản ánh vào giá.
- **1 – Không đáng kể**: nhiễu.

Luôn điều chỉnh theo mức **"đã phản ánh vào giá" (priced-in)**: so sánh thực tế với dự báo đồng thuận và
phản ứng giá thực tế. Tin tốt nhưng giá không tăng → thị trường đã kỳ vọng trước, hoặc có lực bán lớn hơn.

Gắn thêm **khung thời gian** của tác động: trong phiên / vài ngày / vài tuần / trung hạn.

## 3. Động lực chính theo tài sản

**USD (DXY, EURUSD, USDJPY, USDCNH, USD/VND)**
- Chênh lệch lãi suất Mỹ – thế giới (lợi suất 2Y), kỳ vọng FOMC (FedWatch).
- Tăng trưởng Mỹ so với phần còn lại ("US exceptionalism").
- Vai trò trú ẩn khi khủng hoảng; chính sách thuế quan, thâm hụt ngân sách.

**Vàng (XAUUSD)**
- Tương quan nghịch với lợi suất thực và USD (có thể bị phá vỡ khi NHTW mua mạnh).
- Nhu cầu trú ẩn, mua ròng của NHTW, dòng tiền ETF vàng, nhu cầu vật chất Ấn Độ/Trung Quốc.
- Với Việt Nam: chênh lệch giá vàng SJC/nhẫn với giá thế giới quy đổi.

**Bitcoin (BTC)**
- Thanh khoản toàn cầu và khẩu vị rủi ro (tương quan với Nasdaq).
- Dòng tiền ETF giao ngay, funding rate, open interest, các đợt thanh lý.
- Tin quy định, dòng tiền từ doanh nghiệp/quỹ, on-chain (dự trữ sàn, hoạt động cá voi).

**Hàng hoá**
- Dầu (Brent/WTI): OPEC+, tồn kho EIA/API, địa chính trị, cầu Trung Quốc, mùa lái xe.
- Kim loại công nghiệp (đồng): PMI Trung Quốc, bất động sản Trung Quốc, chuyển đổi năng lượng.
- Nông sản (lúa mì, ngô, đậu tương, cà phê, gạo): thời tiết, báo cáo WASDE, xuất khẩu.
- Bạc: vừa là kim loại quý vừa là kim loại công nghiệp.

## 4. Thang điểm sức mạnh tài sản (−5 đến +5)

Dùng cho phần nhận định:
- **+4 / +5**: xu hướng tăng mạnh, động lực cơ bản và kỹ thuật cùng chiều.
- **+1 / +3**: thiên tăng, nhưng có yếu tố cản.
- **0**: trung tính / đi ngang.
- **−1 / −3**: thiên giảm.
- **−4 / −5**: xu hướng giảm mạnh.

Mỗi điểm số phải kèm: lý do chính (tối đa 3), các ngưỡng giá quan trọng (hỗ trợ/kháng cự),
điều kiện làm nhận định sai (invalidation), và sự kiện sắp tới có thể thay đổi bức tranh.

## 5. Phân tích kịch bản

Với mỗi tài sản, đưa ra 2–3 kịch bản (cơ sở / tích cực / tiêu cực) kèm xác suất ước lượng
(tổng = 100%) và tác nhân kích hoạt.

## 6. Nguyên tắc

- Phân biệt rõ **dữ kiện** (có nguồn, có số liệu) và **nhận định** (suy luận của bạn).
- Không bịa số liệu. Nếu không có số liệu cập nhật, nói rõ "chưa xác minh được".
- Xét tương quan liên thị trường: USD mạnh thường gây áp lực lên vàng, hàng hoá, BTC — nêu rõ khi
  có ngoại lệ.
- Ưu tiên tin có mức ảnh hưởng ≥ 3; gom các tin nhỏ cùng chủ đề.
- Đây là phân tích tham khảo, không phải khuyến nghị đầu tư.
