"""Autoenhance Order Management.

Provides functions to create new orders, retrieve paginated orders listing,
and fetch detailed image information for individual orders.
"""
from __future__ import annotations

from typing import Any, Callable, Sequence

import requests

from core.autoenhance.auth import load_api_key
from core.autoenhance.client import _api_get, _api_post


def create_order(
    api_key: str,
    order_name: str,
    image_count: int | None = None,
    log_fn: Callable[[str, str], None] | None = None,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Tạo mới một Order trên Autoenhance."""
    if log_fn:
        log_fn(f"[Autoenhance][Order] Đang tạo đơn hàng mới: {order_name}", "info")

    payload: dict[str, Any] = {}
    if order_name:
        payload["name"] = order_name
    if image_count is not None:
        payload["image_count"] = image_count

    try:
        order_res = _api_post(session, "/orders/", payload, api_key)
        if not order_res.get("order_id"):
            raise ValueError("Không nhận được order_id từ API Autoenhance.")
        return order_res
    except Exception as e:
        if log_fn:
            log_fn(f"[Autoenhance][Order] Lỗi tạo đơn hàng: {e}", "error")
        raise


def list_orders(
    api_key: str | None = None,
    log_fn: Callable[[str, str], None] | None = None,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    """Lấy danh sách tất cả các đơn hàng từ Autoenhance kèm phân trang tự động."""
    actual_key = api_key or load_api_key() or ""
    if not actual_key.strip():
        if log_fn:
            log_fn("[Autoenhance][Order] Vui lòng cấu hình API key Autoenhance trước.", "error")
        return []

    all_orders: list[dict[str, Any]] = []
    offset = None
    while True:
        endpoint = f"/orders/?per_page=15&offset={offset}" if offset is not None else "/orders/?per_page=15"
        try:
            data = _api_get(session, endpoint, actual_key)
            orders = data.get("orders") or []
            all_orders.extend(orders)

            pagination = data.get("pagination", {})
            next_offset = pagination.get("next_offset")
            if not next_offset or not orders:
                break
            offset = next_offset
        except Exception as e:
            if log_fn:
                log_fn(f"[Autoenhance][Order] Lỗi tải danh sách order ở offset {offset}: {e}", "error")
            break
    return all_orders


def get_order_details(
    order_id: str,
    api_key: str | None = None,
    log_fn: Callable[[str, str], None] | None = None,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Lấy chi tiết thông tin và danh sách ảnh của một đơn hàng."""
    actual_key = api_key or load_api_key() or ""
    if not actual_key.strip():
        if log_fn:
            log_fn("[Autoenhance][Order] API key trống, không thể tải thông tin đơn hàng.", "error")
        return {}

    try:
        return _api_get(session, f"/orders/{order_id}", actual_key)
    except Exception as e:
        if log_fn:
            log_fn(f"[Autoenhance][Order] Lỗi khi tải chi tiết Order {order_id}: {e}", "error")
        return {}


def get_order_brackets(
    order_id: str,
    api_key: str | None = None,
    log_fn: Callable[[str, str], None] | None = None,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    """Lấy danh sách brackets của order từ /orders/{order_id}/brackets."""
    actual_key = api_key or load_api_key() or ""
    if not actual_key.strip():
        return []
    try:
        data = _api_get(session, f"/orders/{order_id}/brackets", actual_key)
        if isinstance(data, dict):
            return list(data.get("brackets", []))
    except Exception as exc:
        if log_fn:
            log_fn(f"[Autoenhance][Order] Không thể đọc danh sách brackets của order {order_id}: {exc}", "warn")
    return []


def filter_final_processed_images(
    order_id: str,
    images: Sequence[dict[str, Any]],
    api_key: str | None = None,
    log_fn: Callable[[str, str], None] | None = None,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    """Lọc danh sách ảnh hoàn tất để chỉ giữ lại phiên bản xử lý cuối cùng (Step 6 / Final Output).

    Quy tắc:
    1. Ưu tiên tra cứu /orders/{order_id}/brackets: Các bracket luôn liên kết đến image_id thành phẩm cuối cùng.
       Nếu có danh sách target image_ids từ brackets, chỉ giữ lại các ảnh này.
    2. Fallback heuristic:
       - Nếu có ảnh chứa 'Manually Grouped' trong metadata (do endpoint /process sinh ra),
         chỉ giữ lại các ảnh có trường này.
       - Với các ảnh có cùng tên file (image_name / filename), chỉ giữ lại ảnh mới nhất (date_added lớn nhất
         hoặc có preset_id).
       - Nếu tất cả ảnh đều độc lập không bị trùng lặp và không có dấu vết bracket, giữ nguyên.
    """
    img_list = list(images)
    if not img_list:
        return []

    # 1. Thử lấy target image_ids từ brackets endpoint
    if order_id:
        try:
            brackets = get_order_brackets(order_id=order_id, api_key=api_key, log_fn=log_fn, session=session)
            target_ids = {str(b.get("image_id")) for b in brackets if b.get("image_id")}
            if target_ids:
                matched = [img for img in img_list if str(img.get("image_id") or img.get("id", "")) in target_ids]
                if matched:
                    if log_fn:
                        log_fn(f"[Autoenhance][Order] Đã lọc {len(matched)}/{len(img_list)} ảnh kết quả theo brackets.", "info")
                    return matched
        except Exception:
            pass

    # 2. Heuristic fallback:
    # 2a. Nếu trong order có ảnh thành phẩm do /process tạo ra (có Manually Grouped hoặc preset_id)
    processed_imgs = [
        img for img in img_list
        if (
            isinstance(img.get("metadata"), dict) and "Manually Grouped" in img["metadata"]
        ) or img.get("preset_id") is not None
    ]
    if processed_imgs:
        candidate_pool = processed_imgs
    else:
        candidate_pool = img_list

    # 2b. Khử trùng lặp theo tên: Nếu có nhiều ảnh trùng filename, chọn ảnh có date_added mới nhất
    by_name: dict[str, dict[str, Any]] = {}
    for img in candidate_pool:
        name = str(img.get("image_name") or img.get("filename") or "")
        key = name.strip().lower() if name else str(img.get("image_id") or img.get("id", ""))
        if key not in by_name:
            by_name[key] = img
        else:
            prev = by_name[key]
            prev_date = prev.get("date_added") or 0
            cur_date = img.get("date_added") or 0
            if cur_date >= prev_date:
                by_name[key] = img

    filtered = list(by_name.values())
    if len(filtered) < len(img_list) and log_fn:
        log_fn(f"[Autoenhance][Order] Đã loại bỏ {len(img_list) - len(filtered)} ảnh sơ bộ, giữ lại {len(filtered)} ảnh hoàn thành cuối cùng.", "info")
    return filtered
