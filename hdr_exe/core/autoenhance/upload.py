"""Autoenhance File Preparation & S3 Upload.

Handles format normalization, dimensions recording, AWS S3 presigned PUT URL
acquisition, and chunk-based file streaming to S3.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any, Callable, Sequence

import requests

from core.autoenhance.constants import NATIVE_EXTS
from core.autoenhance.client import _api_post
from core.shared.images import (
    convert_to_jpg,
    get_image_dimensions,
)


def prepare_upload_files(
    input_dir: str | Path | Sequence[str | Path],
    temp_conv_dir: Path,
    log_fn: Callable[[str, str], None] | None = None,
    stop_event: Any = None,
) -> tuple[list[Path], dict[str, tuple[int, int]]]:
    """Thu thập ảnh nguồn và tự động chuyển đổi sang JPEG nếu định dạng không được Autoenhance hỗ trợ gốc.

    Trả về (ready_files, meta_dims).
    Đảm bảo mỗi file chuyển đổi có tên độc nhất dạng {stem}_{ext}_{counter}.jpg,
    không bao giờ bị ghi đè hoặc trỏ chung file và ánh xạ metadata kích thước chính xác.
    """
    source_files: list[Path] = []
    if isinstance(input_dir, (str, Path)):
        p_in = Path(input_dir)
        if p_in.is_file():
            source_files = [p_in]
        elif p_in.is_dir():
            for f in sorted(p_in.iterdir()):
                if f.is_file() and not f.name.startswith("."):
                    source_files.append(f)
    else:
        source_files = [Path(f) for f in input_dir if Path(f).is_file()]

    if not source_files:
        if log_fn:
            log_fn("Không có ảnh nào sẵn sàng để upload.", "warn")
        return [], {}

    ready_files: list[Path] = []
    meta_dims: dict[str, tuple[int, int]] = {}
    used_names: set[str] = set()

    for f in source_files:
        if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
            return [], {}

        ext = f.suffix.lower()
        if ext in NATIVE_EXTS:

            if f.name not in used_names:
                target_f = f
            else:
                # Trùng tên file giữa các thư mục nguồn: copy sang temp_conv_dir với tên duy nhất
                stem = f.stem
                counter = 1
                candidate = f"{stem}_{counter}{ext}"
                while candidate in used_names or (Path(temp_conv_dir) / candidate).exists():
                    counter += 1
                    candidate = f"{stem}_{counter}{ext}"
                target_f = Path(temp_conv_dir) / candidate
                shutil.copy2(f, target_f)
        else:
            if log_fn:
                log_fn(f"Định dạng {ext} không được Autoenhance hỗ trợ trực tiếp. Đang tự động chuyển đổi sang JPG...", "info")
            clean_ext = ext.lstrip(".")
            stem = f.stem
            candidate_name = f"{stem}_{clean_ext}.jpg"
            counter = 1
            conv_dst = Path(temp_conv_dir) / candidate_name
            while candidate_name in used_names or conv_dst.exists():
                candidate_name = f"{stem}_{clean_ext}_{counter}.jpg"
                conv_dst = Path(temp_conv_dir) / candidate_name
                counter += 1

            ok = convert_to_jpg(f, conv_dst)
            if ok and conv_dst.is_file():
                if log_fn:
                    log_fn(f"Chuyển đổi thành công {f.name} -> {conv_dst.name}", "success")
                target_f = conv_dst
            else:
                if log_fn:
                    log_fn(f"Không thể chuyển đổi {f.name}, bỏ qua file này.", "error")
                continue

        used_names.add(target_f.name)
        ready_files.append(target_f)
        meta_dims[target_f.name] = get_image_dimensions(f)

    return ready_files, meta_dims


def get_upload_s3_info(
    api_key: str,
    order_id: str,
    image_name: str,
    log_fn: Callable[[str, str], None] | None = None,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Xin presigned URL từ Autoenhance để upload ảnh lên AWS S3."""
    sess = session or requests.Session()
    return _api_post(
        sess,
        "/images/",
        {"image_name": image_name, "order_id": order_id},
        api_key,
    )


def _upload_one_file(
    file_path: str | Path,
    s3_info: dict[str, Any],
    stop_event: Any = None,
) -> bool:
    """Tải một file ảnh lên Amazon S3 theo URL và headers do Autoenhance cấp."""
    if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
        return False

    url = s3_info.get("s3PutObjectUrl") or s3_info.get("url")
    if not url:
        return False

    raw_headers = s3_info.get("headers", {})
    # Chuẩn hóa headers, loại bỏ host header nếu S3 tự tính
    headers = {str(k): str(v) for k, v in raw_headers.items() if str(k).lower() != "host"}
    if "content-type" not in [k.lower() for k in headers]:
        headers["Content-Type"] = "image/jpeg"

    p = Path(file_path)
    last_err = None

    for attempt in range(3):
        if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
            return False
        try:
            with open(p, "rb") as f:
                resp = requests.put(url, data=f, headers=headers, timeout=60)
                resp.raise_for_status()
                return True
        except Exception as e:
            last_err = e
            time.sleep(2)

    if last_err:
        raise last_err
    return False
