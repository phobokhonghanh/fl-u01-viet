from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .auth import (
    FOTELLO_STATE,
    fotello_get_status,
    fotello_get_tokens,
    fotello_is_connected,
    fotello_reconnect_saved,
)
from .browser_auth import fotello_grab_tokens_from_browser
from .client import LogFn, noop_log, print_system_exception, set_request_logger
from .license import check_level_access
from .constants import (
    EP_CREATE_ENHANCE,
    EP_CREATE_LISTING,
    IMAGE_EXTENSIONS,
    POLL_INITIAL_ATTEMPTS,
    POLL_INITIAL_INTERVAL,
    POLL_LATER_INTERVAL,
    POLL_READY_DIVISOR,
    POLL_TIMEOUT,
)
from .downloads import (
    check_download_single_enhance,
    fotello_batch_download,
    fotello_download_listing,
    fotello_list_enhances_for_listing,
    fotello_list_listings,
)
from .fotello_api import api_post, upload_image_resumable


def _setting_number(settings: dict[str, Any] | None, key: str, default: int | float) -> int | float:
    if not settings:
        return default
    try:
        value = settings.get(key, default)
        if isinstance(default, int):
            return int(value)
        return float(value)
    except (TypeError, ValueError):
        return default


def _next_poll_interval(settings: dict[str, Any] | None, attempt: int, ready_count: int) -> float:
    initial_attempts = max(0, int(_setting_number(settings, "poll_initial_attempts", POLL_INITIAL_ATTEMPTS)))
    initial_interval = max(1.0, float(_setting_number(settings, "poll_initial_interval", POLL_INITIAL_INTERVAL)))
    later_interval = max(1.0, float(_setting_number(settings, "poll_later_interval", POLL_LATER_INTERVAL)))
    divisor = max(1.0, float(_setting_number(settings, "poll_ready_divisor", POLL_READY_DIVISOR)))
    base = initial_interval if attempt <= initial_attempts else later_interval
    if ready_count >= 1 and divisor > 1:
        return max(1.0, base / divisor)
    return base


def _poll_sleep(seconds: float, is_cancelled) -> None:
    deadline = time.time() + max(0.0, seconds)
    while time.time() < deadline and not is_cancelled():
        time.sleep(min(1.0, deadline - time.time()))


def fotello_upload_and_enhance(
    input_dir: str,
    output_dir: str,
    log: LogFn = None,
    progress_fn=None,
    is_cancelled=None,
    preferences: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    count_fn=None,
    max_brackets_per_listing: int = 30,
    license_level: str = "lite",
    summary_fn=None,
) -> dict[str, Any]:
    from .downloads import download_variant
    from .watermark_workflow.models import build_groups, new_manifest
    from .watermark_workflow.cleaner import clean_output
    from .watermark_workflow.coordinator import run_auto

    log = log or noop_log
    is_cancelled = is_cancelled or (lambda: False)
    if not FOTELLO_STATE.get("connected"):
        raise RuntimeError("Chưa kết nối Fotello")
    team_id = FOTELLO_STATE.get("team_id")
    if not team_id:
        raise RuntimeError("Không tìm thấy team_id. Hãy kết nối lại.")
    input_path = Path(input_dir)
    if not input_path.is_dir():
        raise RuntimeError(f"Thư mục đầu vào không hợp lệ: {input_dir}")
    images = [p for p in input_path.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS]
    total_images = len(images)
    log(f"Step 01: Kiểm tra input - tìm thấy {total_images} ảnh hợp lệ.", "info")
    preferences = dict(preferences or {})
    defaults = {
        "bracket_size": 1, "contrast_style": "signature",
        "exterior_sky_replacement": "on", "perspective_correction": "off",
        "custom_style_id": None, "cloud_style": "full_house_puffs",
    }
    preferences = {**defaults, **preferences}
    groups = build_groups(images, int(preferences["bracket_size"]))
    if max_brackets_per_listing < 1:
        raise ValueError("Số bracket mỗi listing phải lớn hơn 0")
    plus = check_level_access(license_level, "plus")
    if not plus and len(groups) > max_brackets_per_listing:
        raise RuntimeError(
            f"Giới hạn: tối đa {max_brackets_per_listing} brackets mỗi job. "
            f"Đã chọn {len(groups)} brackets. Cần PLUS để tự chia listing."
        )
    prefix = str(preferences.get("listing_name_prefix") or "AutoHDR Upload").strip()
    manifest = new_manifest(groups, preferences, str(team_id), prefix, output_dir)
    log(f"Đã chốt {len(groups)} nhóm bracket; chuẩn bị lấy các biến thể để xóa watermark.", "info")

    def create_listing(name, selected):
        tokens = fotello_get_tokens()
        return api_post(EP_CREATE_LISTING, {
            "name": name,
            "num_total_brackets": len(selected),
            "filenames": [fname for g in selected for fname in g["input_filenames"]],
            "isDemoListing": False, "teamId": team_id,
        }, tokens["id_token"], retry_requests=False).get("id")

    def create_enhance(listing_id, upload_ids):
        return api_post(EP_CREATE_ENHANCE, {
            "upload_ids": upload_ids, "listing_id": listing_id,
            "preferences": preferences, "teamId": team_id,
        }, fotello_get_tokens()["id_token"], retry_requests=False).get("id")

    return run_auto(
        manifest,
        upload=lambda path: upload_image_resumable(path, fotello_get_tokens()["id_token"], str(team_id)),
        create_listing=create_listing,
        create_enhance=create_enhance,
        check_ready=lambda enhance_id: check_download_single_enhance(
            enhance_id, fotello_get_tokens()["access_token"], log=log, is_cancelled=is_cancelled),
        download=lambda enhance_id, directory, name, rendition: download_variant(
            enhance_id, fotello_get_tokens()["access_token"], directory, name,
            log=log, is_cancelled=is_cancelled, rendition=rendition),
        clean=clean_output, max_workers=8 if plus else 4,
        chunk_size=max_brackets_per_listing,
        poll_timeout=max(30.0, float(_setting_number(settings, "poll_timeout", POLL_TIMEOUT))),
        poll_interval=lambda attempt, ready: _next_poll_interval(settings, attempt, ready),
        sleep=lambda delay: _poll_sleep(delay, is_cancelled),
        is_cancelled=is_cancelled, log=log, progress_fn=progress_fn,
        count_fn=count_fn, summary_fn=summary_fn,
    )

__all__ = [
    "set_request_logger",
    "fotello_grab_tokens_from_browser",
    "fotello_reconnect_saved",
    "fotello_get_status",
    "fotello_list_listings",
    "fotello_batch_download",
    "fotello_download_listing",
    "fotello_upload_and_enhance",
    "fotello_is_connected",
    "fotello_list_enhances_for_listing",
]
