from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    FLD_BV,
    FLD_ENHANCES,
    FLD_IS_WM,
    IMAGE_EXTENSIONS,
    POLL_INITIAL_ATTEMPTS,
    POLL_INITIAL_INTERVAL,
    POLL_LATER_INTERVAL,
    POLL_READY_DIVISOR,
    POLL_TIMEOUT,
)
from .downloads import (
    check_download_single_enhance,
    download_single_enhance,
    fotello_batch_download,
    fotello_download_listing,
    fotello_list_enhances_for_listing,
    fotello_list_listings,
)
from .firestore import firestore_patch
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


def _natural_sort_key(path: Path) -> list[int | str]:
    return [int(text) if text.isdigit() else text.casefold() for text in re.split(r'(\d+)', path.name)]


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
) -> list[str]:
    log = log or noop_log
    progress_fn = progress_fn or (lambda cur, total: None)
    is_cancelled = is_cancelled or (lambda: False)
    count_fn = count_fn or (lambda uploaded=None, downloaded=None: None)
    if not FOTELLO_STATE.get("connected"):
        raise RuntimeError("Chưa kết nối Fotello")

    tokens = fotello_get_tokens()
    id_token = tokens["id_token"]
    access_token = tokens["access_token"]
    team_id = FOTELLO_STATE.get("team_id")
    if not team_id:
        raise RuntimeError("Lỗi không tìm thấy team_id. Hãy kết nối lại.")

    input_path = Path(input_dir)
    if not input_path.exists() or not input_path.is_dir():
        raise RuntimeError(f"Thư mục đầu vào không hợp lệ: {input_dir}")
    images = sorted(
        (p for p in input_path.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS),
        key=_natural_sort_key
    )
    if not images:
        raise RuntimeError(f"Không tìm thấy ảnh hợp lệ (JPG, RAW...) trong {input_dir}")

    preferences = preferences or {
        "bracket_size": 1,
        "contrast_style": "signature",
        "exterior_sky_replacement": "on",
        "perspective_correction": "off",
        "custom_style_id": None,
        "cloud_style": "full_house_puffs",
    }
    total_images = len(images)
    # log(f"📂 Đã tìm thấy {total_images} ảnh trong thư mục. Bắt đầu upload...", "info")
    log(f"Step 01: Kiểm tra input - tìm thấy {total_images} ảnh hợp lệ.", "info")

    listing_prefix = str(preferences.get("listing_name_prefix") or "").strip() or "AutoHDR Upload"
    listing_name = listing_prefix + " - " + time.strftime("%d %m, %Y %H:%M")
    bracket_size = int(preferences.get("bracket_size") or 1)
    brackets = [images[i : i + bracket_size] for i in range(0, len(images), bracket_size)]
    total_work = total_images + len(brackets)

    # Split brackets into chunks of at most max_brackets_per_listing brackets
    bracket_chunks = [brackets[i : i + max_brackets_per_listing] for i in range(0, len(brackets), max_brackets_per_listing)]
    max_workers = 8
    if not check_level_access(license_level, "plus"):
        time.sleep(2)
        max_workers = 3
        if len(bracket_chunks) > 1:
            raise RuntimeError(
                f"Giới hạn: 1 job xử lý tối đa {max_brackets_per_listing} brackets.\n"
                f"Tổng {total_images} images. Cần tạo {len(bracket_chunks)} jobs - {len(brackets)} brackets\n"
                f"----- Hãy nâng cấp lên PLUS để xử lý tự động -----"
            )

    uploaded: dict[Path, str] = {}

    def _do_upload(img_path: Path) -> tuple[Path, str]:
        if is_cancelled():
            return img_path, ""
        # log(f"  ↑ Đang tải lên {img_path.name}...", "info")
        log(f"Step 02: Đang tải lên - {img_path.name}", "info")
        if not check_level_access(license_level, "plus"):
            time.sleep(1)
        upload_id = upload_image_resumable(img_path, id_token, str(team_id))
        return img_path, upload_id

    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_do_upload, p) for p in images]
        for future in as_completed(futures):
            img_path, upload_id = future.result()
            if upload_id:
                uploaded[img_path] = upload_id
                count_fn(uploaded=len(uploaded), downloaded=0)
            done += 1
            progress_fn(done, total_work)
    # log(f"  ✔ Đã upload xong {len(uploaded)} ảnh.", "success")
    count_fn(uploaded=len(uploaded), downloaded=0)
    log(f"Step 03: Hoàn tất upload - {len(uploaded)}/{total_images} ảnh.", "success")
    if is_cancelled():
        return []

    enhance_ids: list[str] = []
    listing_ids: list[str] = []
    
    log(f"Chia làm {len(bracket_chunks)} đợt xử lý (Tối đa {max_brackets_per_listing} brackets/đợt)...", "info")
    
    for chunk_idx, chunk in enumerate(bracket_chunks, 1):
        if is_cancelled():
            return []
            
        # Get all images belonging to this chunk
        chunk_images = [img for bracket in chunk for img in bracket]
        
        # Format naming: [Part X] - [prefix] - [datetime] if multiple parts, else keep original
        if len(bracket_chunks) > 1:
            chunk_listing_name = f"[Part {chunk_idx}] - {listing_prefix} - {time.strftime('%d %m, %Y %H:%M')}"
        else:
            chunk_listing_name = f"{listing_prefix} - {time.strftime('%d %m, %Y %H:%M')}"
            
        log(f"Step 04: Tạo listing đợt {chunk_idx}/{len(bracket_chunks)}: {chunk_listing_name}", "info")
        listing_result = api_post(
            EP_CREATE_LISTING,
            {
                "name": chunk_listing_name,
                "num_total_brackets": len(chunk),
                "filenames": [p.name for p in chunk_images],
                "isDemoListing": False,
                "teamId": team_id,
            },
            id_token,
        )
        chunk_listing_id = listing_result["id"]
        listing_ids.append(chunk_listing_id)
        log(f"Step 05: Tạo listing đợt {chunk_idx} thành công - {chunk_listing_id[:8]} / {len(chunk)} brackets.", "success")
        
        log(f"Step 06: Kích hoạt xử lý đợt {chunk_idx}...", "info")
        for bracket in chunk:
            if is_cancelled():
                return []
            upload_ids = [uploaded[p] for p in bracket if p in uploaded]
            if not upload_ids:
                continue
            enhance_result = api_post(
                EP_CREATE_ENHANCE,
                {
                    "upload_ids": upload_ids,
                    "listing_id": chunk_listing_id,
                    "preferences": preferences,
                    "teamId": team_id,
                },
                id_token,
            )
            enhance_id = enhance_result.get("id")
            if enhance_id:
                enhance_ids.append(enhance_id)
                firestore_patch(
                    f"{FLD_ENHANCES}/{enhance_id}",
                    {FLD_IS_WM: {FLD_BV: False}},
                    access_token,
                    [FLD_IS_WM],
                    log=log,
                )
                names = ", ".join(p.name for p in bracket)
                log(f"Step 06: [{names}]", "success")
            done += 1
            progress_fn(done, total_work)

    count_download = 0
    poll_timeout = max(30.0, float(_setting_number(settings, "poll_timeout", POLL_TIMEOUT)))
    deadline = time.time() + poll_timeout
    pending = set(enhance_ids)
    failed_items: set[str] = set()
    poll_attempt = 0

    log("Step 07: Kiểm tra trạng thái ảnh...", "info")
    while pending and time.time() < deadline and not is_cancelled():
        poll_attempt += 1
        ready_count = 0
        for enhance_id in list(pending):
            try:
                check = check_download_single_enhance(
                    enhance_id,
                    access_token,
                    log=log,
                    is_cancelled=is_cancelled,
                )
            except Exception as exc:
                print_system_exception(f"service.fotello_upload_and_enhance download enhance={enhance_id}", exc)
                failed_items.add(enhance_id)
                check = False
            if check is True:
                count_download += 1
                pending.discard(enhance_id)
                failed_items.discard(enhance_id)
                ready_count += 1
        if pending:
            interval = _next_poll_interval(settings, poll_attempt, ready_count)
            log(
                f"Step 07: Kiểm tra lần {poll_attempt} - ready={ready_count}/{len(enhance_ids)}, "
                f"pending={len(pending)}, chờ {int(interval)}s.",
                "info",
            )
            _poll_sleep(interval, is_cancelled)
        elif ready_count:
            log(f"Step 07: Trạng thái kiểm tra - ready={count_download}/{len(enhance_ids)}.", "success")

    total_downloaded = 0
    downloaded_files = []
    for chunk_idx, l_id in enumerate(listing_ids, 1):
        if is_cancelled():
            break
        log(f"Đang tải kết quả của listing đợt {chunk_idx}/{len(listing_ids)} (ID: {l_id[:8]})...", "info")
        try:
            downloaded = fotello_download_listing(enhance_ids=enhance_ids,listing_id=l_id, output_dir=str(output_dir), log=log, is_cancelled=is_cancelled)
            downloaded_files.extend(downloaded)
        except Exception as exc:
            log(f"Không thể tải listing đợt {chunk_idx}: {exc}", "error")
            print_system_exception(f"service.fotello_upload_and_enhance download listing={l_id}", exc)
            
    total_downloaded = len(downloaded_files)
    count_fn(downloaded=total_downloaded)
    if total_downloaded >= count_download:
        log(f"Hoàn tất với {total_downloaded} ảnh được tải về.", "info")
    else :
        error_count = len(enhance_ids) - total_downloaded
        if error_count > 0:
            log(f"Lỗi {error_count}/{len(enhance_ids)} mục.", "error")

    return total_downloaded


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
