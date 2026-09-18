"""Vietnamese localized result formatting for watermark workflow."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_STATUS_CLEANED = "cleaned"
_STATUS_NEED_VARIANT = "need_variant"
_STATUS_NEEDS_REVIEW = "needs_review"
_STATUS_BLOCKED = "blocked"


def format_cleaner_result_vn(
    output_name: str,
    result: Mapping[str, Any],
    *,
    with_step: bool = True,
) -> tuple[str, str]:
    """Format cleaner results into friendly, specific Vietnamese log messages.

    Returns (message, level) where level is 'success', 'info', 'warn', or 'error'.
    Eliminates raw English error prose or machine status strings from user logs.
    """
    clean_name = str(output_name or "ảnh").strip()
    st = str(result.get("status") or "")
    rs = str(result.get("reason") or "").strip()
    rs_lower = rs.lower()

    if st == _STATUS_CLEANED:
        msg = f"Step 10: Ghép thành công - {clean_name}" if with_step else f"{clean_name}: Ghép thành công, đã xóa sạch watermark."
        return msg, "success"

    if st == _STATUS_NEED_VARIANT:
        comparisons = result.get("comparisons", [])
        if "trùng góc" in rs_lower or "duplicate" in rs_lower or "same" in rs_lower or any(
            isinstance(c, Mapping) and c.get("status") == "duplicate" for c in comparisons
        ):
            msg = (
                f"Step 09: Watermark trùng góc - cần lấy thêm biến thể cho {clean_name}"
                if with_step
                else f"{clean_name}: Watermark trùng góc - cần thêm ảnh biến thể khác góc để ghép."
            )
            return msg, "info"
        if "at least two" in rs_lower or "chưa đủ 2" in rs_lower:
            msg = (
                f"Step 09: Chưa đủ 2 biến thể - cần tải thêm ảnh cho {clean_name}"
                if with_step
                else f"{clean_name}: Chưa đủ 2 biến thể để so sánh watermark."
            )
            return msg, "info"
        msg = (
            f"Step 09: Chưa đủ cặp góc sạch - cần lấy thêm biến thể cho {clean_name}"
            if with_step
            else f"{clean_name}: Chưa đủ cặp góc sạch - cần thêm ảnh biến thể khác để ghép."
        )
        return msg, "info"

    if st == _STATUS_NEEDS_REVIEW:
        msg = (
            f"Step 10: Cần kiểm tra đường nối (seam) - {clean_name}: Ảnh xem trước đã tạo nhưng có đường nối/chất lượng chưa tối ưu."
            if with_step
            else f"{clean_name}: Cần kiểm tra đường nối (seam) - Đã tạo ảnh xem trước nhưng chất lượng chưa tối ưu."
        )
        return msg, "warn"

    if st == _STATUS_BLOCKED or "error" in st:
        error_type = str(result.get("error_type") or "")
        all_text = f"{error_type} {rs} " + " ".join(
            str(c.get("reason") or "") for c in result.get("comparisons", []) if isinstance(c, Mapping)
        ) + " ".join(
            str(a.get("error") or "") for a in result.get("cleaner_attempts", []) if isinstance(a, Mapping)
        )
        all_text_lower = all_text.lower()

        if "dimensionmismatch" in all_text_lower or "conflicting pixel dimensions" in all_text_lower or "kích thước" in all_text_lower:
            dims = re.findall(r"\((\d+),\s*(\d+)\)", all_text)
            if not dims:
                dims = re.findall(r"(\d{3,5})\s*[xX]\s*(\d{3,5})", all_text)
            if dims and len(dims) >= 2:
                dim_str = f"{dims[0][0]}x{dims[0][1]} vs {dims[1][0]}x{dims[1][1]}"
                detail = f"Kích thước ảnh không khớp ({dim_str})"
            else:
                detail = "Kích thước ảnh không khớp"
            msg = (
                f"Step 10: {detail} - {clean_name}: Không thể ghép ảnh."
                if with_step
                else f"{clean_name}: {detail} - Không thể ghép ảnh."
            )
            return msg, "error"

        if "sourcemismatch" in all_text_lower or "sourceimagemismatch" in all_text_lower:
            msg = (
                f"Step 10: Ảnh gốc không khớp nội dung - {clean_name}: Không thể ghép ảnh."
                if with_step
                else f"{clean_name}: Ảnh gốc không khớp nội dung - Không thể ghép ảnh."
            )
            return msg, "error"

        if "corrupted" in all_text_lower or "decode" in all_text_lower:
            msg = (
                f"Step 10: Tệp ảnh bị lỗi hoặc không đọc được - {clean_name}: Không thể xử lý."
                if with_step
                else f"{clean_name}: Tệp ảnh bị lỗi hoặc không đọc được - Không thể xử lý."
            )
            return msg, "error"

        if "cancel" in all_text_lower or "hủy" in all_text_lower:
            msg = f"Step 10: Quá trình ghép bị dừng - {clean_name}." if with_step else f"{clean_name}: Quá trình ghép bị dừng."
            return msg, "warn"

        if "filesizedeltaexceeded" in all_text_lower or "file size delta" in all_text_lower:
            msg = (
                f"Step 10: Chênh lệch dung lượng tệp vượt ngưỡng - {clean_name}: Không thể ghép ảnh."
                if with_step
                else f"{clean_name}: Chênh lệch dung lượng tệp vượt ngưỡng - Không thể ghép ảnh."
            )
            return msg, "error"

        if "not found" in all_text_lower or "không tìm thấy" in all_text_lower:
            msg = (
                f"Step 10: Không tìm thấy tệp ảnh đầu vào - {clean_name}: Không thể ghép ảnh."
                if with_step
                else f"{clean_name}: Không tìm thấy tệp ảnh đầu vào - Không thể ghép ảnh."
            )
            return msg, "error"

        if "modemismatch" in all_text_lower:
            msg = (
                f"Step 10: Hệ màu ảnh không khớp - {clean_name}: Không thể ghép ảnh."
                if with_step
                else f"{clean_name}: Hệ màu ảnh không khớp - Không thể ghép ảnh."
            )
            return msg, "error"

        # If a specific error reason is present, include concise reason snippet
        clean_detail = "lỗi dữ liệu đầu vào"
        for candidate in (rs, error_type):
            if candidate and str(candidate) != "None":
                cand_str = str(candidate).strip()
                if ":" in cand_str:
                    cand_str = cand_str.split(":")[-1].strip()
                if cand_str and len(cand_str) <= 70 and not cand_str.startswith("{"):
                    clean_detail = f"lỗi dữ liệu đầu vào: {cand_str}"
                    break

        msg = (
            f"Step 10: Không thể ghép ảnh - {clean_name} ({clean_detail})."
            if with_step
            else f"{clean_name}: Không thể ghép ảnh ({clean_detail})."
        )
        return msg, "error"

    msg = f"{clean_name}: Đang chờ xử lý..."
    return msg, "info"


__all__ = ["format_cleaner_result_vn"]
