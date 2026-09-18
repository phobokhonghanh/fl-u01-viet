"""Listings, enhances, and variants retrieval and normalization for Fotello Engine."""
from __future__ import annotations

from typing import Any, Callable

from core.fotello.auth import get_tokens
from core.fotello.constants import (
    FLD_ENHANCES,
    FLD_LISTINGS,
    FLD_SV,
    FLD_VARIANTS,
    RENDITION_EDITED,
    RENDITION_EDITED_UPSIZED,
)
from core.fotello.firestore import decode_firestore_value, run_query
from core.fotello.renditions import (
    extract_enhance_candidates,
    extract_variant_candidates,
    select_best_rendition,
)


class ListingsList(list):
    """Danh sách listing mở rộng với siêu dữ liệu trạng thái phân trang."""
    def __init__(
        self,
        items: Sequence[dict[str, Any]] | None = None,
        *,
        complete: bool = True,
        total: int = 0,
        incomplete_reason: str | None = None,
    ):
        super().__init__(items or [])
        self.complete = complete
        self.total = total or len(self)
        self.incomplete_reason = incomplete_reason


def list_listings(
    limit: int | None = None,
    log_fn: Callable[[str, str], None] | None = None,
    stop_event: Any | None = None,
    paginate: bool = True,
) -> ListingsList:
    """Liệt kê toàn bộ các listing khả dụng trên tài khoản và team Fotello.

    Quy tắc:
    - Bắt buộc kiểm tra đăng nhập và team_id: nếu thiếu phải raise lỗi ngay lập tức,
      không trả về danh sách rỗng hoặc bỏ lọc team.
    - Dùng phân trang cursor từ Firestore (paginate=True) để lấy trọn vẹn toàn bộ listing.
    - Khử trùng lặp theo listing ID.
    - Hỗ trợ hủy qua stop_event và ghi log số lượng listing đã lấy.
    - Nếu đạt giới hạn cấu hình hoặc lỗi giữa chừng, trả danh sách kèm trạng thái complete=False và lý do.
    - Chuẩn hóa các trường: listing ID, tên, ngày tạo, trạng thái. Trường nào server
      không cung cấp thì hiển thị 'Chưa có thông tin', không suy đoán.
    """
    tokens = get_tokens()
    if not tokens:
        raise RuntimeError("Chưa đăng nhập Fotello. Vui lòng đăng nhập trước khi lấy danh sách listing.")
    access_token = tokens.get("access_token") or tokens.get("id_token")
    if not access_token:
        raise RuntimeError("Phiên đăng nhập Fotello không hợp lệ (thiếu access token / id token).")

    team_id = tokens.get("team_id")
    if not team_id:
        raise RuntimeError("Không tìm thấy thông tin team_id trong phiên đăng nhập Fotello. Bắt buộc phải có team_id để lọc listing.")

    query: dict[str, Any] = {
        "from": [{"collectionId": FLD_LISTINGS}],
        "where": {
            "fieldFilter": {
                "field": {"fieldPath": "teamId"},
                "op": "EQUAL",
                "value": {FLD_SV: str(team_id)},
            }
        },
    }
    if limit is not None and not paginate:
        query["limit"] = limit

    if log_fn:
        log_fn(f"[Fotello][Listings] Bắt đầu lấy danh sách listing cho team '{team_id}'...", "info")

    complete = True
    incomplete_reason = None
    try:
        rows = run_query(access_token, query, log_fn=log_fn, stop_event=stop_event, paginate=paginate)
    except Exception as exc:
        complete = False
        incomplete_reason = f"Lỗi trong quá trình truy vấn phân trang: {exc}"
        if log_fn:
            log_fn(f"[Fotello][Listings] {incomplete_reason}", "warn")
        rows = []

    listings: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for row in rows:
        doc = row.get("document", {})
        if not doc:
            continue
        lid = doc.get("name", "").split("/")[-1]
        if not lid or lid in seen_ids:
            continue
        seen_ids.add(lid)

        fields = doc.get("fields", {})
        name = (
            fields.get("address", {}).get(FLD_SV)
            or fields.get("name", {}).get(FLD_SV)
            or fields.get("listingName", {}).get(FLD_SV)
            or "Chưa có thông tin"
        )
        created_val = (
            fields.get("createdAt", {}).get(FLD_SV)
            or fields.get("createdAt", {}).get("timestampValue")
            or "Chưa có thông tin"
        )
        status_val = fields.get("status", {}).get(FLD_SV) or "Chưa có thông tin"

        listings.append({
            "id": lid,
            "listing_id": lid,
            "name": name,
            "createdAt": created_val,
            "status": status_val,
        })

    if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
        complete = False
        incomplete_reason = "Quá trình lấy listing bị dừng bởi người dùng."

    if log_fn:
        status_msg = f"Đã lấy {len(listings)} listing"
        if not complete:
            status_msg += f" (Chưa đầy đủ: {incomplete_reason})"
        log_fn(f"[Fotello][Listings] {status_msg}.", "info" if complete else "warn")

    return ListingsList(listings, complete=complete, total=len(listings), incomplete_reason=incomplete_reason)


def fetch_enhances_for_listing(
    listing_id: str,
    log_fn: Callable[[str, str], None] | None = None,
    stop_event: Any | None = None,
    access_token: str | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Truy vấn toàn bộ enhances của listing với cơ chế phân trang cursor đầy đủ."""
    tok = access_token
    if not tok:
        tokens = get_tokens()
        if not tokens:
            return []
        tok = tokens.get("access_token") or tokens.get("id_token")
    if not tok:
        return []

    query = {
        "from": [{"collectionId": FLD_ENHANCES}],
        "where": {
            "fieldFilter": {
                "field": {"fieldPath": "listingId"},
                "op": "EQUAL",
                "value": {FLD_SV: listing_id},
            }
        },
    }
    rows = run_query(tok, query, log_fn=log_fn, stop_event=stop_event, paginate=True)
    enhances = []

    for row in rows:
        doc = row.get("document", {})
        if not doc:
            continue
        enh_id = doc.get("name", "").split("/")[-1]
        raw_fields = doc.get("fields", {})
        decoded_fields = decode_firestore_value(raw_fields)

        input_filenames = decoded_fields.get("inputFilenames") or decoded_fields.get("sourceFilenames") or []
        fname = input_filenames[0] if input_filenames else f"{enh_id}.jpg"
        in_w = int(decoded_fields.get("inputWidth") or 0)
        in_h = int(decoded_fields.get("inputHeight") or 0)
        status = str(decoded_fields.get("status") or "unknown")

        enhances.append({
            "id": enh_id,
            "enhance_id": enh_id,
            "listing_id": listing_id,
            "filename": fname,
            "input_filenames": input_filenames,
            "inputWidth": in_w,
            "inputHeight": in_h,
            "status": status,
            "raw_fields": raw_fields,
            "decoded_fields": decoded_fields,
        })

    return enhances


def fetch_variants_for_listing(
    listing_id: str,
    log_fn: Callable[[str, str], None] | None = None,
    stop_event: Any | None = None,
    access_token: str | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Truy vấn variants của listing (chỉ truy vấn theo listingId, không thử parentId)."""
    tok = access_token
    if not tok:
        tokens = get_tokens()
        if not tokens:
            return []
        tok = tokens.get("access_token") or tokens.get("id_token")
    if not tok:
        return []

    query = {
        "from": [{"collectionId": FLD_VARIANTS}],
        "where": {
            "fieldFilter": {
                "field": {"fieldPath": "listingId"},
                "op": "EQUAL",
                "value": {FLD_SV: listing_id},
            }
        },
    }
    rows = run_query(tok, query, log_fn=log_fn, stop_event=stop_event, paginate=True)
    variants = []

    for row in rows:
        doc = row.get("document", {})
        if not doc:
            continue
        v_id = doc.get("name", "").split("/")[-1]
        decoded = decode_firestore_value(doc.get("fields", {}))
        decoded["id"] = v_id
        variants.append(decoded)

    return variants


def list_enhances(
    listing_id: str,
    prioritize_upsized: bool = True,
    all_renditions: bool = False,
    log_fn: Callable[[str, str], None] | None = None,
    stop_event: Any | None = None,
    access_token: str | None = None,
    config: Any | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Liệt kê và phân tích các rendition khả dụng cho listing."""
    enhances = fetch_enhances_for_listing(listing_id, log_fn=log_fn, stop_event=stop_event, access_token=access_token)
    if not enhances:
        return []

    enhance_meta_map = {e["id"]: e for e in enhances}
    variants = fetch_variants_for_listing(listing_id, log_fn=log_fn, stop_event=stop_event, access_token=access_token)

    resolved_items = []

    if all_renditions:
        # Tải tất cả: thu thập từng rendition độc lập (cao xuống thấp)
        for enh in enhances:
            candidates = extract_enhance_candidates(enh)
            for c in candidates:
                resolved_items.append({
                    "id": c.enhance_id,
                    "enhance_id": c.enhance_id,
                    "listing_id": listing_id,
                    "status": c.status,
                    "has_image": bool(c.image_uri),
                    "rendition": c.rendition,
                    "field_name": c.field_name,
                    "image_uri": c.image_uri,
                    "filename": c.original_filename,
                    "inputWidth": c.input_width,
                    "inputHeight": c.input_height,
                    "is_upsized": c.is_upsized,
                    "variant_id": c.variant_id,
                    "render_id": c.render_id,
                    "is_variant": False,
                    "requested_rendition": c.rendition,
                    "fallback_reason": None,
                })

        for var in variants:
            var_candidates = extract_variant_candidates(var, enhance_meta_map)
            for vc in var_candidates:
                resolved_items.append({
                    "id": vc.enhance_id,
                    "enhance_id": vc.enhance_id,
                    "listing_id": listing_id,
                    "status": vc.status,
                    "has_image": bool(vc.image_uri),
                    "rendition": vc.rendition,
                    "field_name": vc.field_name,
                    "image_uri": vc.image_uri,
                    "filename": vc.original_filename,
                    "inputWidth": vc.input_width,
                    "inputHeight": vc.input_height,
                    "is_upsized": vc.is_upsized,
                    "variant_id": vc.variant_id,
                    "render_id": vc.render_id,
                    "is_variant": True,
                    "requested_rendition": vc.rendition,
                    "fallback_reason": None,
                })

    else:
        # Chế độ đơn: chọn 1 rendition tốt nhất cho mỗi enhance doc
        preferred = RENDITION_EDITED_UPSIZED if prioritize_upsized else RENDITION_EDITED
        for enh in enhances:
            candidates = extract_enhance_candidates(enh)
            best_cand, fallback_reason = select_best_rendition(candidates, preferred=preferred, log_fn=log_fn)
            if best_cand:
                resolved_items.append({
                    "id": best_cand.enhance_id,
                    "enhance_id": best_cand.enhance_id,
                    "listing_id": listing_id,
                    "status": best_cand.status,
                    "has_image": bool(best_cand.image_uri),
                    "rendition": best_cand.rendition,
                    "field_name": best_cand.field_name,
                    "image_uri": best_cand.image_uri,
                    "filename": best_cand.original_filename,
                    "inputWidth": best_cand.input_width,
                    "inputHeight": best_cand.input_height,
                    "is_upsized": best_cand.is_upsized,
                    "variant_id": best_cand.variant_id,
                    "render_id": best_cand.render_id,
                    "is_variant": False,
                    "requested_rendition": preferred,
                    "fallback_reason": fallback_reason,
                })

    return resolved_items
