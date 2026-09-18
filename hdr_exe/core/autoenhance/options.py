"""Standardized Options Schema and API Payload Mapper for Autoenhance Engine.

Provides explicit, unified options definitions for CLI, config, and UI with
safe mapping to Autoenhance v3 process payloads.
"""
from __future__ import annotations

from typing import Any, Callable

from core.autoenhance.constants import PRESET_MAP

# Valid choices
VALID_AI_VERSIONS: tuple[str, ...] = ("latest", "stable", "beta")
VALID_PRESETS: tuple[str, ...] = ("warm", "vivid", "natural")
VALID_SKY_REPLACEMENTS: tuple[str, ...] = ("off", "clear", "low_cloud", "neutral", "high_cloud")
VALID_WINDOW_PULLS: tuple[str, ...] = ("off", "only_windows", "windows_with_skies")
VALID_GRASS: tuple[str, ...] = ("off", "green")
VALID_TVS: tuple[str, ...] = ("off", "black_out")
VALID_FIREPLACES: tuple[str, ...] = ("off", "alight")
VALID_PHOTOGRAPHER: tuple[str, ...] = ("off", "remove")

# Default proposed values: Latest, Vivid; perspective & lens ON; Privacy & effects OFF.
DEFAULT_AUTOENHANCE_OPTIONS: dict[str, Any] = {
    "ai_version": "latest",
    "preset": "vivid",
    "perspective_correction": True,
    "lens_correction": True,
    "auto_privacy": False,
    "sky_replacement": "off",
    "window_pull": "off",
    "grass": "off",
    "tv_blackout": "off",
    "fireplace": "off",
    "photographer": "off",
}

# Standard Options Catalog Schema for UI / CLI Contract
AUTOENHANCE_OPTIONS_SCHEMA: list[dict[str, Any]] = [
    {
        "field": "ai_version",
        "label": "Phiên bản AI",
        "type": "select",
        "default": "latest",
        "options": [
            {"value": "latest", "label": "Latest (Kênh mới nhất)"},
            {"value": "stable", "label": "Stable (Kênh ổn định)"},
            {"value": "beta", "label": "Beta (Kênh thử nghiệm)"},
        ],
        "category": "advanced",
    },
    {
        "field": "preset",
        "label": "Preset phong cách",
        "type": "select",
        "default": "vivid",
        "options": [
            {"value": "vivid", "label": "Vivid (Rực rỡ, sắc nét)"},
            {"value": "warm", "label": "Warm (Ấm áp, tự nhiên)"},
            {"value": "natural", "label": "Natural (Trung thực, cân bằng)"},
        ],
        "category": "common",
    },
    {
        "field": "perspective_correction",
        "label": "Chỉnh phối cảnh & trục đứng",
        "type": "checkbox",
        "default": True,
        "category": "common",
    },
    {
        "field": "lens_correction",
        "label": "Chỉnh méo ống kính",
        "type": "checkbox",
        "default": True,
        "category": "common",
    },
    {
        "field": "auto_privacy",
        "label": "Tự động che mờ thông tin cá nhân (Privacy)",
        "type": "checkbox",
        "default": False,
        "category": "advanced",
    },
    {
        "field": "sky_replacement",
        "label": "Thay thế bầu trời",
        "type": "select",
        "default": "off",
        "options": [
            {"value": "off", "label": "Tắt (Giữ trời gốc)"},
            {"value": "clear", "label": "Clear (Trời xanh không mây)"},
            {"value": "low_cloud", "label": "Low Cloud (Mây nhẹ tự nhiên)"},
            {"value": "neutral", "label": "Neutral (Trời trung tính)"},
            {"value": "high_cloud", "label": "High Cloud (Mây bồng bềnh)"},
        ],
        "category": "common",
    },
    {
        "field": "window_pull",
        "label": "Cân bằng ánh sáng cửa sổ (Window Pull)",
        "type": "select",
        "default": "off",
        "options": [
            {"value": "off", "label": "Tắt"},
            {"value": "only_windows", "label": "Chỉ khử chói kính (Only Windows)"},
            {"value": "windows_with_skies", "label": "Khử chói kính kèm ghép trời (Windows with Skies)"},
        ],
        "category": "advanced",
    },
    {
        "field": "grass",
        "label": "Làm xanh thảm cỏ ngoại thất",
        "type": "select",
        "default": "off",
        "options": [
            {"value": "off", "label": "Tắt (Giữ cỏ gốc)"},
            {"value": "green", "label": "Làm xanh mướt cỏ (Green)"},
        ],
        "category": "advanced",
    },
    {
        "field": "tv_blackout",
        "label": "Xử lý màn hình TV",
        "type": "select",
        "default": "off",
        "options": [
            {"value": "off", "label": "Tắt (Giữ nguyên TV)"},
            {"value": "black_out", "label": "Tắt đen màn hình TV (Black Out)"},
        ],
        "category": "advanced",
    },
    {
        "field": "fireplace",
        "label": "Thắp lửa lò sưởi",
        "type": "select",
        "default": "off",
        "options": [
            {"value": "off", "label": "Tắt (Giữ lò sưởi gốc)"},
            {"value": "alight", "label": "Thắp lửa ấm (Alight)"},
        ],
        "category": "advanced",
    },
    {
        "field": "photographer",
        "label": "Xóa bóng người chụp ảnh",
        "type": "select",
        "default": "off",
        "options": [
            {"value": "off", "label": "Tắt"},
            {"value": "remove", "label": "Tự động xóa bóng người (Remove)"},
        ],
        "category": "advanced",
    },
]


def map_user_options_to_api_payload(
    options: dict[str, Any] | None,
    log_fn: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """Ánh xạ tường minh từ tùy chọn người dùng (UI/CLI) sang payload API Autoenhance v3.

    Quy tắc:
    - Không dùng alias nhập nhằng giữa perspective_correction và vertical_correction:
      Tùy chọn công khai là `perspective_correction`, ánh xạ sang API field `vertical_correction`.
    - Phân biệt giá trị 'off' (không áp dụng) với giá trị thực thi (BLACK_OUT, REMOVE, ALIGHT).
    - Tách biệt rõ sky_replacement (bật/tắt) và cloud_type (kiểu mây).
    - Không gán cứng nhãn V5 hay chuỗi không hợp lệ vào payload.
    """
    opts = dict(DEFAULT_AUTOENHANCE_OPTIONS)
    if options:
        opts.update(options)

    # 1. AI Version
    ai_ver = str(opts.get("ai_version", "latest")).strip().lower()
    if ai_ver not in VALID_AI_VERSIONS:
        ai_ver = "latest"

    # 2. Preset mapping
    preset_raw = str(opts.get("preset", "vivid")).strip().lower()
    preset_id = opts.get("preset_id") or PRESET_MAP.get(preset_raw, PRESET_MAP["vivid"])

    # 3. Corrections & Privacy
    persp_corr = bool(opts.get("vertical_correction") if "vertical_correction" in opts else opts.get("perspective_correction", True))
    lens_corr = bool(opts.get("lens_correction", True))
    privacy_val = bool(opts.get("privacy") if "privacy" in opts else opts.get("auto_privacy", False))

    # 4. Sky Replacement & Cloud Type
    direct_cloud = opts.get("cloud_type")
    sky_raw = opts.get("sky_replacement", "off")
    if isinstance(sky_raw, bool):
        sky_replacement_api = sky_raw
        sky_val = "on" if sky_raw else "off"
        cloud_type_api = str(direct_cloud).upper() if direct_cloud else ("LOW_CLOUD" if sky_raw else None)
    else:
        sky_val = str(sky_raw).strip().lower()
        if sky_val == "off":
            sky_replacement_api = False
            cloud_type_api = None
        elif sky_val in ("clear", "low_cloud", "high_cloud"):
            sky_replacement_api = True
            cloud_type_api = sky_val.upper()
        elif sky_val == "neutral":
            sky_replacement_api = True
            cloud_type_api = "LOW_CLOUD_LOW_SAT"
        else:
            sky_replacement_api = bool(sky_raw)
            cloud_type_api = str(direct_cloud).upper() if direct_cloud else None

    # 5. Window Pull
    wp_val = str(opts.get("window_pull_type") or opts.get("window_pull", "off")).strip().upper()
    if wp_val in ("ONLY_WINDOWS", "WINDOWS_WITH_SKIES"):
        window_pull_api = wp_val
    elif wp_val == "WINDOWS_WITH_SKIES" or wp_val.lower() == "windows_with_skies":
        window_pull_api = "WINDOWS_WITH_SKIES"
    elif wp_val == "ONLY_WINDOWS" or wp_val.lower() == "only_windows":
        window_pull_api = "ONLY_WINDOWS"
    else:
        window_pull_api = "NONE"

    # 6. Specialized Effects (Grass, TV, Fireplace, Photographer)
    grass_val = str(opts.get("grass", "off")).strip().upper()
    grass_api = "GREEN" if grass_val in ("GREEN", "ON") else "AS_SHOT"

    tv_val = str(opts.get("tvs") or opts.get("tv_blackout", "off")).strip().upper()
    tv_api = "BLACK_OUT" if tv_val in ("BLACK_OUT", "ON") else "AS_SHOT"

    fire_val = str(opts.get("fire_in_fireplaces") or opts.get("fireplace", "off")).strip().upper()
    fire_api = "ALIGHT" if fire_val in ("ALIGHT", "ON") else "AS_SHOT"

    photo_val = str(opts.get("photographer", "off")).strip().upper()
    photo_api = "REMOVE" if photo_val in ("REMOVE", "ON") else "AS_SHOT"

    # Construct final API process payload
    payload: dict[str, Any] = {
        "ai_version": ai_ver,
        "enhance": True,
        "vertical_correction": persp_corr,
        "lens_correction": lens_corr,
        "privacy": privacy_val,
        "upscale": False,
        "preset_id": preset_id,
        "sky_replacement": sky_replacement_api,
        "window_pull_type": window_pull_api,
        "grass": grass_api,
        "tvs": tv_api,
        "fire_in_fireplaces": fire_api,
        "photographer": photo_api,
    }

    if cloud_type_api is not None:
        payload["cloud_type"] = cloud_type_api

    if log_fn:
        log_fn(f"[Autoenhance][Options] Ánh xạ cấu hình: AI={ai_ver}, Preset={preset_raw}, Sky={sky_val}", "info")

    return payload
