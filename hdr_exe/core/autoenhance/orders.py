"""Autoenhance Order Management.

Provides functions to create new orders, retrieve paginated orders listing,
and fetch detailed image information for individual orders.
"""
from __future__ import annotations

from typing import Any, Callable

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
