"""Autoenhance Image Post-Processing Policy.

Applies Autoenhance-specific processing requirements:
- Alpha channel compositing onto pure white background (255, 255, 255)
- Lanczos resampling upscaling with UnsharpMask sharpening
- High quality JPEG compression (quality 95, subsampling 0, 300 DPI)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageFilter

from core.autoenhance.constants import (
    JPEG_DPI,
    JPEG_QUALITY,
    JPEG_SUBSAMPLING,
    UNSHARP_MASK_PERCENT,
    UNSHARP_MASK_RADIUS,
    UNSHARP_MASK_THRESHOLD,
)


def _resize_and_sharpen(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Resize ảnh bằng thuật toán Lanczos và làm nét viền bằng UnsharpMask."""
    if target_w > 0 and target_h > 0 and img.size != (target_w, target_h):
        resample = getattr(Image, "Resampling", Image).LANCZOS
        img = img.resize((target_w, target_h), resample=resample)
        img = img.filter(
            ImageFilter.UnsharpMask(
                radius=UNSHARP_MASK_RADIUS,
                percent=UNSHARP_MASK_PERCENT,
                threshold=UNSHARP_MASK_THRESHOLD,
            )
        )
    return img


def _save_high_quality_jpeg(img: Image.Image, dst: Path) -> None:
    """Lưu ảnh JPEG chất lượng cao (giữ EXIF)."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    exif_data = img.info.get("exif")
    save_kwargs: dict[str, Any] = {
        "quality": JPEG_QUALITY,
        "subsampling": JPEG_SUBSAMPLING,
        "dpi": JPEG_DPI,
    }
    if exif_data:
        save_kwargs["exif"] = exif_data

    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, "JPEG", **save_kwargs)


def _upscale_to_original(
    image_path: str | Path,
    target_w: int,
    target_h: int,
    log_fn: Callable[[str, str], None] | None = None,
) -> bool:
    """Upscale image to match original input resolution and embed 300 DPI."""
    p = Path(image_path)
    if not p.is_file():
        return False

    try:
        with Image.open(p) as img:
            img = _resize_and_sharpen(img, target_w, target_h)
            _save_high_quality_jpeg(img, p)
            return True
    except Exception as e:
        if log_fn:
            log_fn(f"  Upscale failed: {e}", "warn")
        return False


def _process_downloaded_png(
    temp_png_path: str | Path,
    final_jpg_path: str | Path,
    target_dim: tuple[int, int] | None = None,
    log_fn: Callable[[str, str], None] | None = None,
) -> bool:
    """Xử lý file PNG tải về từ Autoenhance: ghép nền trắng cho kênh alpha, upscale Lanczos và ghi ra JPEG 300 DPI."""
    src = Path(temp_png_path)
    dst = Path(final_jpg_path)

    if not src.is_file():
        return False

    try:
        with Image.open(src) as img:
            # Xử lý kênh trong suốt (Alpha): ghép lên nền trắng thay vì convert trực tiếp thành nền đen
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                alpha = img.convert("RGBA").split()[3]
                bg.paste(img.convert("RGB"), mask=alpha)
                img = bg
            elif img.mode != "RGB":
                img = img.convert("RGB")

            if target_dim:
                img = _resize_and_sharpen(img, target_dim[0], target_dim[1])

            _save_high_quality_jpeg(img, dst)

        src.unlink(missing_ok=True)
        return True
    except Exception as e:
        if log_fn:
            log_fn(f"  Lỗi chuyển đổi/làm nét {src.name}: {e}", "error")
        # Tuyệt đối không đổi tên file lỗi thành .jpg để tránh tạo file hỏng giả thành công
        src.unlink(missing_ok=True)
        dst.unlink(missing_ok=True)
        return False

