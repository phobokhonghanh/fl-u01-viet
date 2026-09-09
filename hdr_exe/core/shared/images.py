"""Shared Image Dimension Detection and Format Conversion.

Provides fast, platform-independent dimension extraction and JPEG conversion.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image


def convert_to_jpg(
    src_path: str | Path,
    dest_path: str | Path,
    quality: int = 95,
) -> bool:
    """Chuyển đổi ảnh sang JPEG chất lượng cao."""
    p = Path(src_path)
    suffix = p.suffix.lower()

    if suffix in (".cr2", ".cr3", ".nef", ".arw", ".dng", ".raw"):
        try:
            import rawpy

            with rawpy.imread(str(p)) as raw:
                rgb = raw.postprocess()
            img = Image.fromarray(rgb)
            img.save(dest_path, "JPEG", quality=quality, subsampling=0)
            return Path(dest_path).is_file()
        except Exception:
            pass

    try:
        with Image.open(p) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(dest_path, "JPEG", quality=quality, subsampling=0)
            return Path(dest_path).is_file()
    except Exception:
        return False


def get_image_dimensions(file_path: str | Path) -> tuple[int, int]:
    """Đọc kích thước ảnh (width, height) trực tiếp từ metadata ảnh."""
    try:
        with Image.open(file_path) as img:
            return img.size
    except Exception:
        return 0, 0
