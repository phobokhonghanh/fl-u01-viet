"""Bracket Grouping and Input Integrity Validation for Fotello.

Groups input images into complete bracket sets (1, 3, or 5 photos per output)
using natural sorting order. Performs strict pre-flight validation against incomplete
brackets and verifies file signatures (size, mtime, SHA-256) before upload.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Sequence

from core.shared.jobs.models import OutputSpec

from core.fotello.constants import VALID_BRACKET_SIZES, SUPPORTED_IMAGE_EXTENSIONS


class BracketValidationError(ValueError):
    """Ngoại lệ khi danh sách ảnh đầu vào không hợp lệ hoặc thiếu thành viên bracket."""
    pass


def natural_sort_key(path: Path) -> list[int | str]:
    """Tạo khóa sắp xếp tự nhiên theo tên tập tin (ví dụ: img1, img2, img10)."""
    parts = re.split(r"(\d+)", path.name)
    key: list[int | str] = []
    for p in parts:
        if p.isdigit():
            key.append(int(p))
        else:
            key.append(p.lower())
    return key


def compute_file_sha256(path: Path) -> str:
    """Tính toán SHA-256 của file ảnh đầu vào."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def build_bracket_outputs(
    input_paths: Sequence[Path | str],
    bracket_size: int = 1,
) -> list[OutputSpec]:
    """Nhóm danh sách file ảnh thành các OutputSpec logic theo kích thước bracket.

    Args:
        input_paths: Danh sách đường dẫn file hoặc thư mục.
        bracket_size: Số lượng ảnh trên mỗi bracket (1, 3, hoặc 5).

    Returns:
        Danh sách OutputSpec đã được gắn metadata và chữ ký file để kiểm tra toàn vẹn.

    Raises:
        BracketValidationError: Nếu bracket_size không hợp lệ, không có ảnh hợp lệ,
            hoặc số lượng ảnh không chia hết cho bracket_size.
    """
    if bracket_size not in VALID_BRACKET_SIZES:
        raise BracketValidationError(
            f"bracket_size không hợp lệ: {bracket_size}. Chỉ hỗ trợ {VALID_BRACKET_SIZES}"
        )

    # Thu thập toàn bộ file ảnh hợp lệ
    all_files: list[Path] = []
    for item in input_paths:
        p = Path(item).resolve()
        if p.is_dir():
            all_files.extend(
                f for f in p.iterdir()
                if f.is_file() and f.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
            )
        elif p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
            all_files.append(p)

    if not all_files:
        raise BracketValidationError("Không tìm thấy file ảnh hợp lệ nào trong đường dẫn đã chỉ định.")

    # Sắp xếp tên tự nhiên
    all_files.sort(key=natural_sort_key)

    total_images = len(all_files)
    if total_images % bracket_size != 0:
        remainder = total_images % bracket_size
        raise BracketValidationError(
            f"Tổng số ảnh ({total_images}) không chia hết cho bracket_size ({bracket_size}). "
            f"Bracket cuối cùng bị thiếu {bracket_size - remainder} ảnh. "
            f"Vui lòng kiểm tra lại thư mục input trước khi chạy."
        )

    outputs: list[OutputSpec] = []
    num_brackets = total_images // bracket_size

    for idx in range(num_brackets):
        group = all_files[idx * bracket_size : (idx + 1) * bracket_size]
        base_name = group[0].name
        output_id = f"bracket_{idx + 1:03d}_{group[0].stem}"

        signatures = []
        for f_path in group:
            st = f_path.stat()
            signatures.append({
                "path": str(f_path),
                "name": f_path.name,
                "size": st.st_size,
                "mtime_ns": st.st_mtime_ns,
                "sha256": compute_file_sha256(f_path),
            })

        spec = OutputSpec(
            output_id=output_id,
            input_files=group,
            metadata={
                "bracket_index": idx,
                "bracket_size": bracket_size,
                "base_filename": base_name,
                "input_filenames": [f.name for f in group],
                "signatures": signatures,
            },
        )
        outputs.append(spec)

    return outputs


def verify_inputs_integrity(outputs: Sequence[OutputSpec]) -> None:
    """Xác thực toàn vẹn các file đầu vào trước khi bắt đầu tải lên.

    Đảm bảo file không bị xóa, không bị thay đổi kích thước hoặc nội dung (SHA-256)
    kể từ lúc nhóm bracket.
    """
    for out in outputs:
        signatures = out.metadata.get("signatures", [])
        for sig in signatures:
            f_path = Path(sig["path"])
            if not f_path.is_file():
                raise BracketValidationError(
                    f"File đầu vào đã bị xóa hoặc không tìm thấy: '{f_path}' (thuộc {out.output_id})"
                )
            st = f_path.stat()
            if st.st_size != sig["size"]:
                raise BracketValidationError(
                    f"File đầu vào đã bị thay đổi kích thước: '{f_path}' "
                    f"(kích thước cũ {sig['size']}, mới {st.st_size})"
                )
            current_sha = compute_file_sha256(f_path)
            if current_sha != sig["sha256"]:
                raise BracketValidationError(
                    f"Nội dung file đầu vào đã bị sửa đổi (SHA-256 không khớp): '{f_path}'"
                )
