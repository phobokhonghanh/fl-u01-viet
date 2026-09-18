"""Autoenhance Engine Constants & Configuration.

Defines API endpoints, presets, native extensions, worker limits,
CDP settings, timeouts, and storage locations.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.shared.config import (
    get_api_key_path,
    get_engine_config,
    get_engine_dir,
    get_metadata_dir,
)

_engine_cfg = get_engine_config("autoenhance")

# API Base endpoint
API_BASE: str = _engine_cfg.get("api_base", "https://api.autoenhance.ai/v3")

# Browser & Web service endpoints
DOMAIN: str = "autoenhance.ai"
APP_URL: str = "https://app.autoenhance.ai"
SETTINGS_URL: str = "https://app.autoenhance.ai/settings?tab=account"
VALIDATION_ENDPOINT: str = "/orders/?per_page=1"
ENHANCED_IMAGE_QUERY: str = (
    "format=png&watermark=false&preview=true&quality=90&scale=1&max_width=8192"
)

# API key regex pattern in Next.js/React state HTML (supports modern 40-char Base62 & legacy UUID)
API_KEY_REGEX: re.Pattern[str] = re.compile(
    r'(?:\\?"|")(?:apiKey|key)(?:\\?"|"):\s*(?:\\?"|")([a-zA-Z0-9_\-]{32,48})'
)

# User agents
USER_AGENT: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
CDP_USER_AGENT: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# Network & Concurrency limits
MAX_API_WORKERS: int = int(_engine_cfg.get("max_api_workers", 4))
MAX_DOWNLOAD_WORKERS: int = int(_engine_cfg.get("max_download_workers", 4))

DEFAULT_TIMEOUT: int = 30
DEFAULT_RETRY_COUNT: int = 3
DEFAULT_RETRY_DELAY: int = 2
DEFAULT_POLL_INTERVAL: int = 5
DEFAULT_MAX_WAIT_SECONDS: int = 1800
DOWNLOAD_CHUNK_SIZE: int = 65536

# Image processing specifications
JPEG_QUALITY: int = 95
JPEG_DPI: tuple[int, int] = (300, 300)
JPEG_SUBSAMPLING: int = 0
UNSHARP_MASK_RADIUS: float = 1.5
UNSHARP_MASK_PERCENT: int = 120
UNSHARP_MASK_THRESHOLD: int = 3

# Default AI processing options
DEFAULT_PROCESS_OPTIONS: dict[str, Any] = {
    "ai_version": "latest",
    "enhance": True,
    "vertical_correction": True,
    "lens_correction": True,
    "privacy": False,
    "upscale": False,
    "sky_replacement": True,
    "cloud_type": "LOW_CLOUD",
    "window_pull_type": "WINDOWS_WITH_SKIES",
    "grass": "AS_SHOT",
    "tvs": "BLACK_OUT",
    "fire_in_fireplaces": "ALIGHT",
    "photographer": "REMOVE",
}

# Native file extensions supported directly by Autoenhance
NATIVE_EXTS: frozenset[str] = frozenset({
    ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"
})

# Preset GUID mapping
PRESET_MAP: dict[str, str] = {
    "warm": "c1824178-a113-43f3-a9b5-15caa91d93df",
    "vivid": "d8a5ecac-202e-4d1f-8ed4-39669d4f81cb",
    "natural": "b02886eb-a8e8-4ffc-bcd4-e9c901dddb52",
}

# Local storage configuration paths
ENGINE_DIR: Path = get_engine_dir("autoenhance")
STORAGE_FILE: Path = get_api_key_path("autoenhance")
META_DIR: Path = get_metadata_dir("autoenhance")

# Standard Autoenhance job step definitions
JOB_STEPS: tuple[tuple[str, str], ...] = (
    ("auth", "Xác thực API"),
    ("prepare", "Chuẩn bị & Chuyển đổi"),
    ("create_order", "Tạo đơn hàng"),
    ("upload", "Tải ảnh lên S3"),
    ("execute", "Kích hoạt xử lý"),
    ("polling", "Chờ xử lý hoàn tất"),
    ("download", "Tải ảnh kết quả"),
    ("export", "Xuất file và manifest"),
)

WORKFLOW_STEPS: tuple[tuple[str, str], ...] = JOB_STEPS
