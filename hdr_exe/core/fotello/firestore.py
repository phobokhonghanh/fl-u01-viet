"""Firestore REST API communication and cursor pagination for Fotello Engine."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Callable

from core.fotello.config import load_fotello_config
from core.fotello.client import retry_request


def decode_firestore_value(val: Any) -> Any:
    """Giải mã cấu trúc giá trị định kiểu của Firestore REST API sang Python native."""
    if not isinstance(val, dict):
        return val
    if "stringValue" in val:
        return val["stringValue"]
    if "integerValue" in val:
        return int(val["integerValue"])
    if "doubleValue" in val:
        return float(val["doubleValue"])
    if "booleanValue" in val:
        return val["booleanValue"]
    if "timestampValue" in val:
        return val["timestampValue"]
    if "nullValue" in val:
        return None
    if "arrayValue" in val:
        return [decode_firestore_value(v) for v in val["arrayValue"].get("values", [])]
    if "mapValue" in val:
        return {k: decode_firestore_value(v) for k, v in val["mapValue"].get("fields", {}).items()}
    return {k: decode_firestore_value(v) for k, v in val.items()}


def get_document(path: str, access_token: str) -> dict[str, Any]:
    """Đọc một document từ Firestore REST API theo đường dẫn."""
    config = load_fotello_config()
    clean_path = path.lstrip("/")
    url = f"{config.endpoints.firestore_url}/{clean_path}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, headers=headers)

    def _do():
        with urllib.request.urlopen(req, timeout=config.firestore.request_timeout_seconds) as resp:
            return json.loads(resp.read().decode("utf-8"))

    return retry_request(_do)


def _extract_cursor_values(doc: dict[str, Any], order_by_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trích xuất giá trị cursor theo đúng các trường được khai báo trong orderBy."""
    values = []
    fields = doc.get("fields", {})
    doc_name = doc.get("name", "")

    for order_spec in order_by_list:
        field_path = order_spec.get("field", {}).get("fieldPath", "")
        if field_path == "__name__":
            values.append({"referenceValue": doc_name})
        elif field_path in fields:
            values.append(fields[field_path])
        else:
            values.append({"nullValue": None})

    return values


def run_query(
    access_token: str,
    query: dict[str, Any],
    log_fn: Callable[[str, str], None] | None = None,
    stop_event: Any | None = None,
    paginate: bool = True,
) -> list[dict[str, Any]]:
    """Chạy truy vấn StructuredQuery Firestore với cơ chế phân trang cursor startAt đầy đủ."""
    config = load_fotello_config()
    url = f"{config.endpoints.firestore_url}:runQuery"
    page_size = config.firestore.page_size
    max_docs = config.firestore.max_query_documents

    # Chuẩn bị danh sách orderBy ổn định (phải có __name__ ở cuối)
    base_order = list(query.get("orderBy", []))
    has_name_order = any(o.get("field", {}).get("fieldPath") == "__name__" for o in base_order)
    if not has_name_order:
        base_order.append({"field": {"fieldPath": "__name__"}, "direction": "ASCENDING"})

    all_rows: list[dict[str, Any]] = []
    last_cursor: list[dict[str, Any]] | None = None
    seen_cursors: set[str] = set()

    while True:
        if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
            if log_fn:
                log_fn("[Fotello][Firestore] Truy vấn bị dừng bởi người dùng.", "warn")
            break

        current_query = dict(query)
        current_query["orderBy"] = base_order
        current_query["limit"] = page_size

        if last_cursor:
            current_query["startAt"] = {
                "values": last_cursor,
                "before": False,
            }

        body = json.dumps({"structuredQuery": current_query}).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")

        def _do_post():
            with urllib.request.urlopen(req, timeout=config.firestore.request_timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data if isinstance(data, list) else []

        page_rows = retry_request(_do_post, stop_event=stop_event)
        doc_rows = [r for r in page_rows if r.get("document")]

        if not doc_rows:
            # Không còn document nào
            break

        all_rows.extend(page_rows)

        if not paginate or len(doc_rows) < page_size:
            # Đã tải hết trang dữ liệu cuối cùng
            break

        if len(all_rows) >= max_docs:
            if log_fn:
                log_fn(
                    f"[Fotello][Firestore] Đã đạt giới hạn tối đa {max_docs} document từ cấu hình.",
                    "warn",
                )
            break

        # Lấy cursor từ document cuối cùng của trang
        last_doc = doc_rows[-1]["document"]
        cursor_values = _extract_cursor_values(last_doc, base_order)
        cursor_key = json.dumps(cursor_values, sort_keys=True)

        if cursor_key in seen_cursors:
            raise RuntimeError(
                f"[Fotello][Firestore] Phát hiện vòng lặp phân trang tại cursor: {last_doc.get('name')}"
            )

        seen_cursors.add(cursor_key)
        last_cursor = cursor_values

    return all_rows
