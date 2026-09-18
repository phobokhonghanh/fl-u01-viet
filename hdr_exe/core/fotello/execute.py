"""Server Resource Creation (Listing & Enhances) for Fotello.

Implements Fotello REST API calls for createListing and createEnhance with strict
payload validation, credential management, and atomic resource tracking.
"""
from __future__ import annotations

import datetime
import json
import urllib.error
import urllib.request
from typing import Any, Callable, Sequence

from core.fotello.config import FotelloConfig, load_fotello_config
from core.shared.jobs.models import OutputSpec


def create_listing(
    *,
    name: str,
    num_total_brackets: int,
    filenames: list[str],
    team_id: str,
    id_token: str,
    config: FotelloConfig | None = None,
) -> str:
    """Gọi endpoint Fotello /v1/<createListing> để tạo listing mới cho Job.

    Args:
        name: Tên hiển thị của listing.
        num_total_brackets: Tổng số bracket trong listing.
        filenames: Danh sách tên tất cả các file input trong listing.
        team_id: Fotello team ID.
        id_token: Firebase ID token.
        config: Cấu hình Fotello.

    Returns:
        listing_id (chuỗi ID của listing vừa tạo).
    """
    cfg = config or load_fotello_config()
    url = cfg.endpoints.api_base_url.rstrip("/") + cfg.endpoints.create_listing_path

    payload = {
        "name": name,
        "num_total_brackets": int(num_total_brackets),
        "filenames": list(filenames),
        "isDemoListing": False,
        "teamId": str(team_id),
    }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=UTF-8",
            "Authorization": f"Bearer {id_token}",
            "Origin": "https://app.fotello.co",
            "Referer": "https://app.fotello.co/",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=cfg.network.request_timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            listing_id = data.get("id", "")
            if not listing_id:
                raise RuntimeError("Response createListing không chứa trường 'id'.")
            return listing_id
    except Exception as exc:
        raise RuntimeError(f"Lỗi khi gọi createListing ({url}): {exc}") from exc


def create_enhance(
    *,
    listing_id: str,
    upload_ids: list[str],
    preferences: dict[str, Any],
    team_id: str,
    id_token: str,
    config: FotelloConfig | None = None,
) -> str:
    """Gọi endpoint Fotello /v1/<createEnhance> để yêu cầu AI xử lý 1 bracket ảnh.

    Args:
        listing_id: ID của listing cha.
        upload_ids: Danh sách upload_id các ảnh thuộc bracket này.
        preferences: Thông số cấu hình ảnh (contrast_style, sky_replacement, ...).
        team_id: Fotello team ID.
        id_token: Firebase ID token.
        config: Cấu hình Fotello.

    Returns:
        enhance_id (chuỗi ID của enhance vừa tạo).
    """
    cfg = config or load_fotello_config()
    url = cfg.endpoints.api_base_url.rstrip("/") + cfg.endpoints.create_enhance_path

    clean_prefs = dict(preferences)
    # Nếu tắt thay trời, không để cloud_style vô tình kích hoạt lại thay trời
    if clean_prefs.get("exterior_sky_replacement") == "off":
        clean_prefs.pop("cloud_style", None)
    # custom_style_id chỉ gửi khi có giá trị hợp lệ
    if clean_prefs.get("custom_style_id") is None:
        clean_prefs.pop("custom_style_id", None)

    payload = {
        "listing_id": str(listing_id),
        "upload_ids": list(upload_ids),
        "preferences": clean_prefs,
        "teamId": str(team_id),
        "input_preview_image_id": upload_ids[0] if upload_ids else "",
        "earliest_captured_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=UTF-8",
            "Authorization": f"Bearer {id_token}",
            "Origin": "https://app.fotello.co",
            "Referer": "https://app.fotello.co/",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=cfg.network.request_timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            enhance_id = data.get("id", "")
            if not enhance_id:
                raise RuntimeError("Response createEnhance không chứa trường 'id'.")
            return enhance_id
    except Exception as exc:
        raise RuntimeError(f"Lỗi khi gọi createEnhance ({url}): {exc}") from exc


def create_job_resources(
    *,
    listing_name: str,
    outputs: Sequence[OutputSpec],
    output_upload_ids: dict[str, list[str]],
    preferences: dict[str, Any],
    team_id: str,
    id_token: str,
    config: FotelloConfig | None = None,
    log_fn: Callable[[str, str], None] | None = None,
) -> tuple[str, dict[str, str]]:
    """Tạo đầy đủ tài nguyên server cho một Job: Listing và các Enhances tương ứng.

    Returns:
        tuple (listing_id, output_to_enhance_id_map)
    """
    cfg = config or load_fotello_config()

    all_filenames: list[str] = []
    for out in outputs:
        all_filenames.extend([p.name for p in out.input_files])

    num_brackets = len(outputs)
    if log_fn:
        log_fn(f"Đang tạo listing '{listing_name}' ({num_brackets} brackets)...", "info")

    listing_id = create_listing(
        name=listing_name,
        num_total_brackets=num_brackets,
        filenames=all_filenames,
        team_id=team_id,
        id_token=id_token,
        config=cfg,
    )

    if log_fn:
        log_fn(f"Tạo listing thành công: {listing_id}", "success")

    output_enhance_map: dict[str, str] = {}
    for idx, out in enumerate(outputs):
        u_ids = output_upload_ids.get(out.output_id, [])
        if not u_ids:
            raise RuntimeError(f"Không tìm thấy upload_ids cho output '{out.output_id}'")

        if log_fn:
            log_fn(f"Đang gửi yêu cầu xử lý enhance {idx + 1}/{num_brackets}: {out.output_id}", "info")

        enh_id = create_enhance(
            listing_id=listing_id,
            upload_ids=u_ids,
            preferences=preferences,
            team_id=team_id,
            id_token=id_token,
            config=cfg,
        )
        output_enhance_map[out.output_id] = enh_id

    return listing_id, output_enhance_map
