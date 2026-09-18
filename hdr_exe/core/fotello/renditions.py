"""Rendition analysis, mapping, and resolution selection for Fotello Engine."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from core.fotello.constants import (
    ENHANCE_FIELD_MAP,
    RENDITION_EDITED_UPSIZED,
    RENDITION_OUTPUT,
    STANDARD_PRIORITY_ORDER,
)


@dataclass
class RenditionCandidate:
    """Ứng viên rendition phát hiện được từ metadata."""
    enhance_id: str
    rendition: str
    image_uri: str
    field_name: str
    is_upsized: bool
    original_filename: str
    input_width: int
    input_height: int
    status: str
    variant_id: str | None = None
    render_id: str | None = None
    is_variant: bool = False


# Quy tắc fallback hợp lệ cùng nhóm nghiệp vụ (không đổi chéo giữa edited và merged)
from core.fotello.constants import FAMILY_FALLBACKS


def extract_enhance_candidates(enhance: dict[str, Any]) -> list[RenditionCandidate]:
    """Trích xuất toàn bộ các rendition khả dụng của một enhance doc theo thứ tự ưu tiên."""
    candidates = []
    fields = enhance.get("raw_fields", {})
    enh_id = enhance.get("id") or enhance.get("enhance_id", "")
    fname = enhance.get("filename") or f"{enh_id}.jpg"
    in_w = enhance.get("inputWidth", 0)
    in_h = enhance.get("inputHeight", 0)
    status = enhance.get("status", "completed")

    for canonical in STANDARD_PRIORITY_ORDER:
        field_name = ENHANCE_FIELD_MAP.get(canonical, canonical)
        val = fields.get(field_name)
        uri = ""
        if isinstance(val, dict):
            uri = val.get("stringValue") or val.get("value") or ""
        elif isinstance(val, str):
            uri = val

        if uri and (uri.startswith("gs://") or uri.startswith("http")):
            candidates.append(
                RenditionCandidate(
                    enhance_id=enh_id,
                    rendition=canonical,
                    image_uri=uri,
                    field_name=field_name,
                    is_upsized="upsized" in canonical,
                    original_filename=fname,
                    input_width=in_w,
                    input_height=in_h,
                    status=status,
                    is_variant=False,
                )
            )

    return candidates


def extract_variant_candidates(
    variant: dict[str, Any],
    enhance_meta_map: dict[str, dict[str, Any]],
) -> list[RenditionCandidate]:
    """Trích xuất toàn bộ rendition từ các renders của variant doc."""
    candidates = []
    v_id = variant.get("id", "")
    parent_enh_id = variant.get("parentId") or variant.get("enhanceId") or ""
    parent_meta = enhance_meta_map.get(parent_enh_id, {})
    fname = parent_meta.get("filename") or variant.get("sourceName") or f"{parent_enh_id or v_id}.jpg"
    in_w = parent_meta.get("inputWidth", 0)
    in_h = parent_meta.get("inputHeight", 0)

    renders = variant.get("renders")
    if not isinstance(renders, dict):
        return []

    for r_id, r_val in renders.items():
        if not isinstance(r_val, dict):
            continue
        r_status = r_val.get("status", "completed")
        outputs = r_val.get("outputs")
        seen_uris: set[str] = set()

        if isinstance(outputs, list):
            for idx, out_item in enumerate(outputs):
                if not isinstance(out_item, dict):
                    continue
                suffix = f"_{idx}" if len(outputs) > 1 else ""

                # 1. Upsized version first (cao xuống thấp)
                upsized = out_item.get("upsizedUri") or out_item.get("upsized_uri")
                if upsized and upsized not in seen_uris and isinstance(upsized, str) and (upsized.startswith("gs://") or upsized.startswith("http")):
                    seen_uris.add(upsized)
                    candidates.append(
                        RenditionCandidate(
                            enhance_id=parent_enh_id or v_id,
                            rendition=f"{RENDITION_OUTPUT}{suffix}_upsized",
                            image_uri=upsized,
                            field_name=f"renders.{r_id}.outputs[{idx}].upsizedUri",
                            is_upsized=True,
                            original_filename=fname,
                            input_width=in_w,
                            input_height=in_h,
                            status=r_status,
                            variant_id=v_id,
                            render_id=r_id,
                            is_variant=True,
                        )
                    )

                # 2. Standard version second
                uri = out_item.get("uri") or out_item.get("outputUri") or out_item.get("url")
                if uri and uri not in seen_uris and isinstance(uri, str) and (uri.startswith("gs://") or uri.startswith("http")):
                    seen_uris.add(uri)
                    candidates.append(
                        RenditionCandidate(
                            enhance_id=parent_enh_id or v_id,
                            rendition=f"{RENDITION_OUTPUT}{suffix}",
                            image_uri=uri,
                            field_name=f"renders.{r_id}.outputs[{idx}].uri",
                            is_upsized=False,
                            original_filename=fname,
                            input_width=in_w,
                            input_height=in_h,
                            status=r_status,
                            variant_id=v_id,
                            render_id=r_id,
                            is_variant=True,
                        )
                    )

        # Selected output fallback
        sel_out = r_val.get("selectedOutput")
        if isinstance(sel_out, dict):
            sel_up = sel_out.get("upsizedUri") or sel_out.get("upsized_uri")
            if sel_up and sel_up not in seen_uris and isinstance(sel_up, str) and (sel_up.startswith("gs://") or sel_up.startswith("http")):
                seen_uris.add(sel_up)
                candidates.append(
                    RenditionCandidate(
                        enhance_id=parent_enh_id or v_id,
                        rendition=f"{RENDITION_OUTPUT}_upsized",
                        field_name=f"renders.{r_id}.selectedOutput.upsizedUri",
                        image_uri=sel_up,
                        is_upsized=True,
                        original_filename=fname,
                        input_width=in_w,
                        input_height=in_h,
                        status=r_status,
                        variant_id=v_id,
                        render_id=r_id,
                        is_variant=True,
                    )
                )

            sel_uri = sel_out.get("uri") or sel_out.get("outputUri")
            if sel_uri and sel_uri not in seen_uris and isinstance(sel_uri, str) and (sel_uri.startswith("gs://") or sel_uri.startswith("http")):
                seen_uris.add(sel_uri)
                candidates.append(
                    RenditionCandidate(
                        enhance_id=parent_enh_id or v_id,
                        rendition=RENDITION_OUTPUT,
                        field_name=f"renders.{r_id}.selectedOutput.uri",
                        image_uri=sel_uri,
                        is_upsized=False,
                        original_filename=fname,
                        input_width=in_w,
                        input_height=in_h,
                        status=r_status,
                        variant_id=v_id,
                        render_id=r_id,
                        is_variant=True,
                    )
                )

    return candidates


def select_best_rendition(
    candidates: list[RenditionCandidate],
    preferred: str = RENDITION_EDITED_UPSIZED,
    log_fn: Callable[[str, str], None] | None = None,
) -> tuple[RenditionCandidate | None, str | None]:
    """Chọn rendition tối ưu nhất cho một enhance doc theo yêu cầu hoặc fallback cùng nhóm nghiệp vụ.
    
    Trả về: (chosen_candidate, fallback_reason)
    """
    if not candidates:
        return None, None

    cand_map = {c.rendition: c for c in candidates}

    # 1. Khớp chính xác yêu cầu
    if preferred in cand_map:
        return cand_map[preferred], None

    # 2. Thử fallback trong cùng nhóm nghiệp vụ (ví dụ: edited_upsized -> edited)
    allowed_fallbacks = FAMILY_FALLBACKS.get(preferred, [])
    for fb in allowed_fallbacks:
        if fb in cand_map:
            reason = f"Không có {preferred}; chuyển sang {fb} cùng nhóm nghiệp vụ."
            if log_fn:
                log_fn(f"[Fotello][Renditions] {reason}", "warn")
            return cand_map[fb], reason

    # 3. Nếu không có fallback cùng nhóm, chọn candidate đầu tiên theo thứ tự cao nhất
    top_cand = candidates[0]
    reason = f"Không có {preferred}; chuyển sang {top_cand.rendition} khả dụng."
    if log_fn:
        log_fn(f"[Fotello][Renditions] {reason}", "warn")
    return top_cand, reason
