"""Listing and Enhance queries for Fotello (from_debug)."""
from __future__ import annotations

from typing import Any, Callable

from core.fotello.from_debug.auth import get_tokens
from core.fotello.from_debug.constants import FLD_ENHANCES, FLD_SV
from core.fotello.from_debug.firestore import firestore_run_query, decode_firestore_value


def list_listings(limit: int = 50, log_fn: Callable[[str, str], None] | None = None) -> list[dict[str, Any]]:
    tokens = get_tokens()
    if not tokens:
        return []
    access_token = tokens.get("access_token") or tokens.get("id_token")
    if not access_token:
        return []

    team_id = tokens.get("team_id")
    query: dict[str, Any] = {
        "from": [{"collectionId": "listings"}],
        "limit": limit,
    }
    if team_id:
        query["where"] = {
            "fieldFilter": {
                "field": {"fieldPath": "teamId"},
                "op": "EQUAL",
                "value": {FLD_SV: team_id},
            }
        }
    docs = firestore_run_query(access_token, query, log_fn=log_fn)
    listings = []
    for d in docs:
        doc = d.get("document")
        if not doc:
            continue
        lid = doc.get("name", "").split("/")[-1]
        fields = decode_firestore_value(doc.get("fields", {}))
        name = fields.get("name") or fields.get("listingName") or lid
        listings.append({
            "id": lid,
            "listing_id": lid,
            "name": name,
            "createdAt": fields.get("createdAt", ""),
            "status": fields.get("status", ""),
        })
    return listings


ENHANCE_RENDITION_MAPPINGS: list[tuple[str, tuple[str, ...]]] = [
    ("edited_upsized", ("editedImageUpsized", "edited_upsized")),
    ("merged_upsized", ("mergedImageUpsized", "merged_upsized")),
    ("edited", ("editedImage", "edited")),
    ("merged", ("mergedImage", "merged")),
    ("output", ("outputImage", "output")),
]


def list_all_renditions_for_listing(
    listing_id: str,
    log_fn: Callable[[str, str], None] | None = None,
) -> list[dict[str, Any]]:
    """List ALL renditions for a listing from enhance metadata and variants."""
    tokens = get_tokens()
    if not tokens:
        return []
    access_token = tokens.get("access_token") or tokens.get("id_token")
    if not access_token:
        return []

    # 1. Query enhances collection
    enhance_query = {
        "from": [{"collectionId": FLD_ENHANCES}],
        "where": {
            "fieldFilter": {
                "field": {"fieldPath": "listingId"},
                "op": "EQUAL",
                "value": {FLD_SV: listing_id},
            }
        },
        "limit": 500,
    }
    docs = firestore_run_query(access_token, query=enhance_query, log_fn=log_fn)

    renditions: list[dict[str, Any]] = []
    enhance_meta: dict[str, dict[str, Any]] = {}

    for d in docs:
        doc = d.get("document")
        if not doc:
            continue
        enh_id = doc.get("name", "").split("/")[-1]
        raw_fields = doc.get("fields", {})
        fields = decode_firestore_value(raw_fields)

        source_names = fields.get("sourceFilenames") or fields.get("inputFilenames") or []
        fname = source_names[0] if source_names else f"{enh_id}.jpg"
        in_w = int(fields.get("inputWidth") or 0)
        in_h = int(fields.get("inputHeight") or 0)
        status = fields.get("status", "unknown")

        enhance_meta[enh_id] = {
            "filename": fname,
            "sourceFilenames": source_names,
            "inputWidth": in_w,
            "inputHeight": in_h,
            "status": status,
        }

        # Check all canonical enhance rendition fields
        for canonical, cand_fields in ENHANCE_RENDITION_MAPPINGS:
            for f_name in cand_fields:
                uri = fields.get(f_name)
                if uri and isinstance(uri, str) and (uri.startswith("gs://") or uri.startswith("http")):
                    renditions.append({
                        "id": enh_id,
                        "enhance_id": enh_id,
                        "listing_id": listing_id,
                        "rendition": canonical,
                        "field_name": f_name,
                        "image_uri": uri,
                        "has_image": True,
                        "upsized": "upsized" in canonical,
                        "name": fname,
                        "sourceFilenames": source_names,
                        "inputWidth": in_w,
                        "inputHeight": in_h,
                        "status": status,
                        "variant_id": None,
                        "render_id": None,
                        "is_variant": False,
                    })
                    break  # Matched canonical rendition

    # 2. Query variants collection if possible
    variant_docs = []
    try:
        variant_query = {
            "from": [{"collectionId": "variants"}],
            "where": {
                "fieldFilter": {
                    "field": {"fieldPath": "listingId"},
                    "op": "EQUAL",
                    "value": {FLD_SV: listing_id},
                }
            },
            "limit": 500,
        }
        variant_docs = firestore_run_query(access_token, query=variant_query, log_fn=log_fn)
    except Exception as e:
        if log_fn:
            log_fn(f"[Fotello][Variants] Truy vấn variants theo listingId thất bại ({e}). Thử theo parentId...", "warn")
        try:
            variant_query_parent = {
                "from": [{"collectionId": "variants"}],
                "where": {
                    "fieldFilter": {
                        "field": {"fieldPath": "parentId"},
                        "op": "EQUAL",
                        "value": {FLD_SV: listing_id},
                    }
                },
                "limit": 500,
            }
            variant_docs = firestore_run_query(access_token, query=variant_query_parent, log_fn=log_fn)
        except Exception as e2:
            if log_fn:
                log_fn(f"[Fotello][Variants] Không thể lấy variants do thiếu quyền hoặc lỗi Firestore: {e2}", "warn")
            variant_docs = []

    # Parse variant renders
    for vd in variant_docs:
        doc = vd.get("document")
        if not doc:
            continue
        v_id = doc.get("name", "").split("/")[-1]
        v_raw = doc.get("fields", {})
        v_fields = decode_firestore_value(v_raw)

        parent_enh_id = v_fields.get("parentId") or v_fields.get("enhanceId") or ""
        parent_meta = enhance_meta.get(parent_enh_id, {})
        fname = parent_meta.get("filename") or v_fields.get("sourceName") or f"{parent_enh_id or v_id}.jpg"
        in_w = parent_meta.get("inputWidth", 0)
        in_h = parent_meta.get("inputHeight", 0)

        renders = v_fields.get("renders")
        if not isinstance(renders, dict):
            continue

        for r_id, r_val in renders.items():
            if not isinstance(r_val, dict):
                continue
            r_status = r_val.get("status", "completed")

            # Check outputs in render
            outputs = r_val.get("outputs")
            seen_uris = set()

            if isinstance(outputs, list):
                for idx, out_item in enumerate(outputs):
                    if not isinstance(out_item, dict):
                        continue
                    suffix = f"_{idx}" if len(outputs) > 1 else ""
                    upsized = out_item.get("upsizedUri") or out_item.get("upsized_uri")
                    if upsized and upsized not in seen_uris and isinstance(upsized, str) and (upsized.startswith("gs://") or upsized.startswith("http")):
                        seen_uris.add(upsized)
                        renditions.append({
                            "id": parent_enh_id or v_id,
                            "enhance_id": parent_enh_id or v_id,
                            "listing_id": listing_id,
                            "rendition": f"output{suffix}_upsized",
                            "field_name": f"renders.{r_id}.outputs[{idx}].upsizedUri",
                            "image_uri": upsized,
                            "has_image": True,
                            "upsized": True,
                            "name": fname,
                            "sourceFilenames": parent_meta.get("sourceFilenames", []),
                            "inputWidth": in_w,
                            "inputHeight": in_h,
                            "status": r_status,
                            "variant_id": v_id,
                            "render_id": r_id,
                            "is_variant": True,
                        })
                    uri = out_item.get("uri") or out_item.get("outputUri") or out_item.get("url")
                    if uri and uri not in seen_uris and isinstance(uri, str) and (uri.startswith("gs://") or uri.startswith("http")):
                        seen_uris.add(uri)
                        renditions.append({
                            "id": parent_enh_id or v_id,
                            "enhance_id": parent_enh_id or v_id,
                            "listing_id": listing_id,
                            "rendition": f"output{suffix}",
                            "field_name": f"renders.{r_id}.outputs[{idx}].uri",
                            "image_uri": uri,
                            "has_image": True,
                            "upsized": False,
                            "name": fname,
                            "sourceFilenames": parent_meta.get("sourceFilenames", []),
                            "inputWidth": in_w,
                            "inputHeight": in_h,
                            "status": r_status,
                            "variant_id": v_id,
                            "render_id": r_id,
                            "is_variant": True,
                        })

            # Check selectedOutput if not added via outputs
            sel_out = r_val.get("selectedOutput")
            if isinstance(sel_out, dict):
                sel_up = sel_out.get("upsizedUri") or sel_out.get("upsized_uri")
                if sel_up and sel_up not in seen_uris and isinstance(sel_up, str) and (sel_up.startswith("gs://") or sel_up.startswith("http")):
                    seen_uris.add(sel_up)
                    renditions.append({
                        "id": parent_enh_id or v_id,
                        "enhance_id": parent_enh_id or v_id,
                        "listing_id": listing_id,
                        "rendition": "output_upsized",
                        "field_name": f"renders.{r_id}.selectedOutput.upsizedUri",
                        "image_uri": sel_up,
                        "has_image": True,
                        "upsized": True,
                        "name": fname,
                        "sourceFilenames": parent_meta.get("sourceFilenames", []),
                        "inputWidth": in_w,
                        "inputHeight": in_h,
                        "status": r_status,
                        "variant_id": v_id,
                        "render_id": r_id,
                        "is_variant": True,
                    })
                sel_uri = sel_out.get("uri") or sel_out.get("outputUri")
                if sel_uri and sel_uri not in seen_uris and isinstance(sel_uri, str) and (sel_uri.startswith("gs://") or sel_uri.startswith("http")):
                    seen_uris.add(sel_uri)
                    renditions.append({
                        "id": parent_enh_id or v_id,
                        "enhance_id": parent_enh_id or v_id,
                        "listing_id": listing_id,
                        "rendition": "output",
                        "field_name": f"renders.{r_id}.selectedOutput.uri",
                        "image_uri": sel_uri,
                        "has_image": True,
                        "upsized": False,
                        "name": fname,
                        "sourceFilenames": parent_meta.get("sourceFilenames", []),
                        "inputWidth": in_w,
                        "inputHeight": in_h,
                        "status": r_status,
                        "variant_id": v_id,
                        "render_id": r_id,
                        "is_variant": True,
                    })

    return renditions


def list_enhances_for_listing(
    listing_id: str,
    all_renditions: bool = False,
    log_fn: Callable[[str, str], None] | None = None,
) -> list[dict[str, Any]]:
    if all_renditions:
        return list_all_renditions_for_listing(listing_id, log_fn=log_fn)

    tokens = get_tokens()
    if not tokens:
        return []
    access_token = tokens.get("access_token") or tokens.get("id_token")
    if not access_token:
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
        "limit": 500,
    }
    docs = firestore_run_query(access_token, query, log_fn=log_fn)
    enhances = []
    for d in docs:
        doc = d.get("document")
        if not doc:
            continue
        enh_id = doc.get("name", "").split("/")[-1]
        raw_fields = doc.get("fields", {})
        fields = decode_firestore_value(raw_fields)
        
        # Priority for debug: editedImageUpsized first, then editedImage
        upsized_uri = fields.get("editedImageUpsized") or ""
        edited_uri = fields.get("editedImage") or ""
        chosen_uri = upsized_uri or edited_uri
        is_upsized = bool(upsized_uri)

        source_names = fields.get("sourceFilenames") or fields.get("inputFilenames") or []
        fname = source_names[0] if source_names else f"{enh_id}.jpg"

        enhances.append({
            "id": enh_id,
            "enhance_id": enh_id,
            "listing_id": listing_id,
            "status": fields.get("status", "unknown"),
            "has_image": bool(chosen_uri),
            "upsized": is_upsized,
            "image_uri": chosen_uri,
            "edited_uri": edited_uri,
            "upsized_uri": upsized_uri,
            "name": fname,
            "sourceFilenames": source_names,
            "inputWidth": int(fields.get("inputWidth") or 0),
            "inputHeight": int(fields.get("inputHeight") or 0),
        })
    return enhances
