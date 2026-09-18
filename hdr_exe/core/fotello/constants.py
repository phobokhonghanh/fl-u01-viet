"""Protocol constants, Firestore fields, and step identifiers for Fotello Engine."""
from __future__ import annotations

from pathlib import Path
from core.shared.config import get_tokens_path

# Firestore Field Types
FLD_BV: str = "booleanValue"
FLD_SV: str = "stringValue"
FLD_IV: str = "integerValue"
FLD_MV: str = "mapValue"
FLD_AV: str = "arrayValue"

# Firestore Collections & Documents Fields
FLD_EDITED: str = "editedImage"
FLD_EDITED_UPSIZED: str = "editedImageUpsized"
FLD_MERGED: str = "mergedImage"
FLD_MERGED_UPSIZED: str = "mergedImageUpsized"
FLD_ENHANCES: str = "enhances"
FLD_VARIANTS: str = "variants"
FLD_LISTINGS: str = "listings"
FLD_IS_WM: str = "isWatermarked"
FLD_STATUS: str = "status"

# Pipeline Step Identifiers
DOWNLOAD_STEPS: list[tuple[str, str]] = [
    ("activation", "Activation"),
    ("auth", "Auth"),
    ("listings", "Listings"),
    ("resolve_outputs", "ResolveOutputs"),
    ("download", "Download"),
    ("validate", "Validate"),
    ("export", "Export"),
]

# Canonical Rendition Names (No obsolete aliases)
RENDITION_EDITED_UPSIZED: str = "edited_upsized"
RENDITION_MERGED_UPSIZED: str = "merged_upsized"
RENDITION_EDITED: str = "edited"
RENDITION_MERGED: str = "merged"
RENDITION_OUTPUT_UPSIZED: str = "output_upsized"
RENDITION_OUTPUT: str = "output"

# Standard Resolution Priority Order (From highest to lowest quality & resolution)
STANDARD_PRIORITY_ORDER: tuple[str, ...] = (
    RENDITION_EDITED_UPSIZED,
    RENDITION_MERGED_UPSIZED,
    RENDITION_EDITED,
    RENDITION_MERGED,
    RENDITION_OUTPUT,
)

# Canonical field mapping for enhances collection
ENHANCE_FIELD_MAP: dict[str, str] = {
    RENDITION_EDITED_UPSIZED: FLD_EDITED_UPSIZED,
    RENDITION_MERGED_UPSIZED: FLD_MERGED_UPSIZED,
    RENDITION_EDITED: FLD_EDITED,
    RENDITION_MERGED: FLD_MERGED,
    RENDITION_OUTPUT: "outputImage",
}

# Storage paths
def get_fotello_tokens_file() -> Path:
    return get_tokens_path("fotello")


VALID_BRACKET_SIZES: tuple[int, ...] = (1, 3, 5, 7)
SUPPORTED_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"})

VALID_CONTRAST_STYLES: tuple[str, ...] = ("signature", "natural", "dramatic", "twilight")
VALID_CLOUD_STYLES: tuple[str, ...] = (
    "original",
    "full_house_puffs",
    "open_house_puffs",
    "streaks_with_puffs",
    "sweep_streaks",
    "scatter_streaks",
    "crisp_streaks",
    "clear_fade",
)

# Standard Options Catalog Schema for Fotello (Contract with UI/CLI)
FOTELLO_OPTIONS_SCHEMA: list[dict[str, Any]] = [
    {
        "field": "bracket_size",
        "label": "Số ảnh mỗi bracket",
        "type": "select",
        "default": 3,
        "options": [
            {"value": 1, "label": "1 ảnh (Ảnh đơn)"},
            {"value": 3, "label": "3 ảnh (-2, 0, +2 EV)"},
            {"value": 5, "label": "5 ảnh (Dải EV rộng)"},
            {"value": 7, "label": "7 ảnh (Dải EV tối đa)"},
        ],
        "category": "common",
    },
    {
        "field": "contrast_style",
        "label": "Phong cách tương phản",
        "type": "select",
        "default": "signature",
        "options": [
            {"value": "signature", "label": "Signature (Đặc trưng Fotello)"},
            {"value": "natural", "label": "Natural (Tự nhiên, hài hòa)"},
            {"value": "dramatic", "label": "Dramatic (Tương phản cao)"},
            {"value": "twilight", "label": "Twilight (Hoàng hôn / Chạng vạng)"},
        ],
        "category": "common",
    },
    {
        "field": "exterior_sky_replacement",
        "label": "Thay thế bầu trời ngoại thất",
        "type": "select",
        "default": "on",
        "options": [
            {"value": "on", "label": "Bật (Thay trời AI)"},
            {"value": "off", "label": "Tắt (Giữ trời gốc)"},
        ],
        "category": "common",
    },
    {
        "field": "cloud_style",
        "label": "Kiểu mây trời",
        "type": "select",
        "default": "full_house_puffs",
        "options": [
            {"value": "original", "label": "Original (Giữ nguyên mây)"},
            {"value": "full_house_puffs", "label": "Full house puffs (Mây bông đầy)"},
            {"value": "open_house_puffs", "label": "Open house puffs (Mây rải rác)"},
            {"value": "streaks_with_puffs", "label": "Streaks & puffs (Vệt mây nhẹ)"},
            {"value": "sweep_streaks", "label": "Sweep streaks (Vệt quét)"},
            {"value": "scatter_streaks", "label": "Scatter streaks (Vệt rải)"},
            {"value": "crisp_streaks", "label": "Crisp streaks (Vệt sắc nét)"},
            {"value": "clear_fade", "label": "Clear fade (Chuyển tiếp trong trẻo)"},
        ],
        "enabled_if": {"field": "exterior_sky_replacement", "equals": "on"},
        "category": "advanced",
    },
    {
        "field": "perspective_correction",
        "label": "Chỉnh phối cảnh & trục đứng",
        "type": "select",
        "default": "off",
        "options": [
            {"value": "on", "label": "Bật (Cân thẳng trục đứng)"},
            {"value": "off", "label": "Tắt"},
        ],
        "category": "common",
    },
    {
        "field": "listing_name_prefix",
        "label": "Tên / Tiền tố Listing",
        "type": "text",
        "default": "AutoHDR Upload",
        "category": "common",
    },
    {
        "field": "custom_style_id",
        "label": "Style tùy chỉnh (Custom Style)",
        "type": "text",
        "default": None,
        "category": "advanced",
        "supported": False,  # Chưa hỗ trợ do chưa có API nguồn dữ liệu style tùy chỉnh
        "status_note": "Chưa hỗ trợ (cần API custom style từ Fotello)",
    },
]

JOB_STEPS = (
    ("auth", "Xác thực phiên Fotello"),
    ("prepare", "Kiểm tra toàn vẹn input"),
    ("upload", "Tải ảnh gốc lên cloud storage"),
    ("create_listing", "Tạo listing mới"),
    ("execute", "Gửi yêu cầu xử lý enhance"),
    ("polling", "Chờ hoàn tất xử lý AI"),
    ("download", "Tải ảnh thành phẩm"),
    ("export", "Xuất file và manifest"),
)

FAMILY_FALLBACKS: dict[str, list[str]] = {
    RENDITION_EDITED_UPSIZED: [RENDITION_EDITED],
    RENDITION_MERGED_UPSIZED: [RENDITION_MERGED],
    RENDITION_OUTPUT_UPSIZED: [RENDITION_OUTPUT],
}

