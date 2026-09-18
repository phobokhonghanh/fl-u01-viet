"""Fotello Configuration Schema, Loader, and Migration Manager.

Manages dedicated Fotello configuration strictly at ~/.hdr_exe/fotello/config.json.
Provides schema validation for jobs capacity, processing preferences, upload,
polling, endpoints, and automatic one-time migration from legacy root config.
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any
import base64

from core.shared.config import ConfigurationError, get_app_dir
from core.shared.jobs.models import JobLimits
from core.fotello.constants import (
    VALID_BRACKET_SIZES,
    VALID_CLOUD_STYLES,
    VALID_CONTRAST_STYLES,
)
_XOR_KEY = b"Ft2026Obf"
def _dec(blob: str) -> str:
    raw = base64.b64decode(blob)
    return bytes(b ^ _XOR_KEY[i % len(_XOR_KEY)] for i, b in enumerate(raw)).decode()


DEFAULT_PROJECT_ID = "real-estate-firebase-4109e"
DEFAULT_API_KEY = _dec("Bz1IUWFPDlsoCSwBYwEFHQM1IAR/QAIGPSAuPiZAcwpYeBAtB0Vd")
DEFAULT_API_BASE = "https://api.fotello.co"
DEFAULT_STORAGE_UPLOAD_URL = "https://firebasestorage.googleapis.com/v0/b/fotello-uploads/o"



@dataclass(frozen=True)
class FotelloJobLimitsConfig:
    max_outputs_per_job: int = 20
    max_jobs_per_batch: int = 3

    def to_limits(self) -> JobLimits:
        return JobLimits(
            max_outputs_per_job=self.max_outputs_per_job,
            max_jobs_per_batch=self.max_jobs_per_batch,
        )


@dataclass(frozen=True)
class FotelloPreferencesConfig:
    bracket_size: int = 1
    contrast_style: str = "signature"
    exterior_sky_replacement: str = "on"
    perspective_correction: str = "off"
    custom_style_id: str | None = None
    cloud_style: str = "full_house_puffs"
    listing_name_prefix: str = "AutoHDR Upload"


@dataclass(frozen=True)
class FotelloUploadConfig:
    max_workers: int = 4
    max_retries: int = 3
    chunk_size_bytes: int = 8 * 1024 * 1024  # 8 MB
    timeout_seconds: float = 120.0
    retry_delay_seconds: float = 1.0
    finalize_retry_delay_seconds: float = 1.5


@dataclass(frozen=True)
class FotelloPollingConfig:
    cancellation_check_seconds: float = 0.5
    interval_seconds: float = 5.0
    timeout_seconds: float = 600.0


@dataclass(frozen=True)
class FotelloEndpointsConfig:
    firebase_project_id: str = DEFAULT_PROJECT_ID
    firebase_api_key: str = DEFAULT_API_KEY
    api_base_url: str = DEFAULT_API_BASE
    storage_upload_url: str = DEFAULT_STORAGE_UPLOAD_URL
    app_url: str = "https://app.fotello.co"
    create_upload_path: str = "/v1/create-upload"
    create_listing_path: str = "/v1/create-listing"
    create_enhance_path: str = "/v1/create-enhance"
    storage_download_base_url: str = "https://firebasestorage.googleapis.com/v0"
    storage_fallback_base_url: str = "https://storage.googleapis.com/download/storage/v1"
    firestore_url: str = ""
    auth_url: str = ""

    def __post_init__(self) -> None:
        if "<createUpload>" in self.create_upload_path:
            object.__setattr__(self, "create_upload_path", "/v1/create-upload")
        if "<createListing>" in self.create_listing_path:
            object.__setattr__(self, "create_listing_path", "/v1/create-listing")
        if "<createEnhance>" in self.create_enhance_path:
            object.__setattr__(self, "create_enhance_path", "/v1/create-enhance")
        if not self.firestore_url:
            derived_fs = f"https://firestore.googleapis.com/v1/projects/{self.firebase_project_id}/databases/(default)/documents"
            object.__setattr__(self, "firestore_url", derived_fs)
        if not self.auth_url:
            derived_auth = f"https://securetoken.googleapis.com/v1/token?key={self.firebase_api_key}"
            object.__setattr__(self, "auth_url", derived_auth)


@dataclass(frozen=True)
class FotelloDownloadConfig:
    max_download_workers: int = 4
    max_file_bytes: int = 52428800  # 50 MB
    max_image_dimension: int = 12000  # 12000 px
    chunk_size_bytes: int = 65536


@dataclass(frozen=True)
class FotelloFirestoreConfig:
    page_size: int = 500
    max_query_documents: int = 5000
    request_timeout_seconds: float = 60.0


@dataclass(frozen=True)
class FotelloNetworkConfig:
    request_timeout_seconds: float = 60.0
    max_retries: int = 3
    retry_delay_seconds: float = 1.0
    retry_backoff: float = 2.0


@dataclass(frozen=True)
class FotelloAuthConfig:
    refresh_margin_seconds: float = 300.0


@dataclass(frozen=True)
class FotelloBrowserConfig:
    port: int = 9222
    login_timeout_seconds: float = 120.0
    poll_interval_seconds: float = 1.0
    startup_timeout_seconds: float = 30.0
    startup_poll_interval_seconds: float = 0.5


@dataclass(frozen=True)
class FotelloConfig:
    """Toàn bộ cấu hình Fotello độc lập."""
    jobs: FotelloJobLimitsConfig = field(default_factory=FotelloJobLimitsConfig)
    preferences: FotelloPreferencesConfig = field(default_factory=FotelloPreferencesConfig)
    upload: FotelloUploadConfig = field(default_factory=FotelloUploadConfig)
    polling: FotelloPollingConfig = field(default_factory=FotelloPollingConfig)
    endpoints: FotelloEndpointsConfig = field(default_factory=FotelloEndpointsConfig)
    download: FotelloDownloadConfig = field(default_factory=FotelloDownloadConfig)
    firestore: FotelloFirestoreConfig = field(default_factory=FotelloFirestoreConfig)

    network: FotelloNetworkConfig = field(default_factory=FotelloNetworkConfig)
    auth: FotelloAuthConfig = field(default_factory=FotelloAuthConfig)
    browser: FotelloBrowserConfig = field(default_factory=FotelloBrowserConfig)


def get_fotello_config_path(app_dir: Path | None = None) -> Path:
    """Trả về đường dẫn file cấu hình Fotello (~/.hdr_exe/fotello/config.json)."""
    base = app_dir or get_app_dir()
    return base / "fotello" / "config.json"


ALLOWED_SECTIONS = frozenset(f.name for f in fields(FotelloConfig))


def validate_fotello_config_dict(raw: dict[str, Any]) -> dict[str, Any]:
    """Kiểm tra và xác thực cấu trúc schema cấu hình Fotello nghiêm ngặt."""
    if not isinstance(raw, dict):
        raise ConfigurationError(f"Cấu hình Fotello phải là JSON object, nhận được {type(raw).__name__}")

    for sec in raw:
        if sec not in ALLOWED_SECTIONS:
            raise ConfigurationError(
                f"fotello: mục cấu hình không hợp lệ '{sec}'. Các mục hợp lệ: {sorted(ALLOWED_SECTIONS)}"
            )

    defaults = FotelloConfig()
    result: dict[str, Any] = {}
    for section in fields(FotelloConfig):
        name = section.name
        values = raw.get(name, {})
        if not isinstance(values, dict):
            raise ConfigurationError(f"fotello.{name} phải là JSON object")
        default_section = getattr(defaults, name)
        allowed = {f.name for f in fields(default_section)}
        for key, value in values.items():
            label = f"fotello.{name}.{key}"
            if key not in allowed:
                raise ConfigurationError(f"fotello.{name}: khóa không hợp lệ '{key}'")
            expected = getattr(default_section, key)
            if key == "bracket_size":
                if isinstance(value, bool) or not isinstance(value, int) or value not in VALID_BRACKET_SIZES:
                    raise ConfigurationError(f"{label} phải là một trong {VALID_BRACKET_SIZES}, nhận {value!r}")
            elif isinstance(expected, (int, float)):
                integer = isinstance(expected, int)
                valid_type = isinstance(value, int) if integer else isinstance(value, (int, float))
                zero_allowed = key == "max_retries"
                minimum = ">= 0" if zero_allowed else "> 0"
                kind = "là số nguyên " if integer else ""
                if isinstance(value, bool) or not valid_type or not math.isfinite(value) or (value < 0 if zero_allowed else value <= 0):
                    raise ConfigurationError(f"{label} phải {kind}{minimum}, nhận {value!r}")
            elif isinstance(expected, str):
                if not isinstance(value, str):
                    raise ConfigurationError(f"{label} phải là chuỗi")
                if key == "contrast_style" and value not in VALID_CONTRAST_STYLES:
                    raise ConfigurationError(f"{label} phải là một trong {VALID_CONTRAST_STYLES}, nhận {value!r}")
                elif key == "cloud_style" and value not in VALID_CLOUD_STYLES:
                    raise ConfigurationError(f"{label} phải là một trong {VALID_CLOUD_STYLES}, nhận {value!r}")
                elif key in ("exterior_sky_replacement", "perspective_correction") and value not in ("on", "off"):
                    raise ConfigurationError(f"{label} phải là 'on' hoặc 'off', nhận {value!r}")
            elif expected is None and value is not None and not isinstance(value, str):
                raise ConfigurationError(f"{label} phải là chuỗi hoặc null")
        # Missing fields use the dataclass default, defined in exactly one place.
        result[name] = type(default_section)(**values)

    if result["upload"].chunk_size_bytes < 256 * 1024:
        raise ConfigurationError("fotello.upload.chunk_size_bytes phải >= 256KB")
    if result["browser"].port > 65535:
        raise ConfigurationError("fotello.browser.port phải <= 65535")
    return result


def serialize_fotello_config(cfg: FotelloConfig) -> dict[str, Any]:
    """Chuyển đổi FotelloConfig thành dictionary JSON sạch."""
    return asdict(cfg)


def deserialize_fotello_config(raw: dict[str, Any]) -> FotelloConfig:
    """Khôi phục đối tượng FotelloConfig từ dict đã xác thực."""
    validated = validate_fotello_config_dict(raw)
    return FotelloConfig(**validated)


def _atomic_write_file(path: Path, data: dict[str, Any]) -> None:
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    temp = parent / f".tmp_{os.getpid()}_{time.time_ns()}.json"
    try:
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        if hasattr(os, "chmod"):
            try:
                os.chmod(temp, 0o600)
            except OSError:
                pass
        os.replace(temp, path)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def migrate_fotello_config_if_needed(app_dir: Path | None = None) -> None:
    """Thao tác di chuyển (migration) 1 lần từ config chung sang ~/.hdr_exe/fotello/config.json."""
    base = app_dir or get_app_dir()
    root_cfg_path = base / "config.json"
    fotello_cfg_path = base / "fotello" / "config.json"

    if not root_cfg_path.is_file():
        return

    try:
        with open(root_cfg_path, "r", encoding="utf-8") as f:
            root_data = json.load(f)
    except Exception:
        return

    if not isinstance(root_data, dict) or "fotello" not in root_data:
        return

    old_fotello = root_data.pop("fotello")
    if not isinstance(old_fotello, dict):
        old_fotello = {}

    # Nếu file fotello/config.json đã có, đọc ra để merge
    existing_fotello_data: dict[str, Any] = {}
    if fotello_cfg_path.is_file():
        try:
            with open(fotello_cfg_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    existing_fotello_data = loaded
        except Exception:
            pass

    # Ánh xạ các trường từ old_fotello phẳng sang cấu trúc phân mục mới
    mapped: dict[str, Any] = dict(existing_fotello_data)
    if "endpoints" not in mapped:
        mapped["endpoints"] = {}
    if "download" not in mapped:
        mapped["download"] = {}
    if "firestore" not in mapped:
        mapped["firestore"] = {}

    for k in ("firebase_project_id", "firebase_api_key", "firestore_url", "auth_url"):
        if k in old_fotello and k not in mapped["endpoints"]:
            mapped["endpoints"][k] = old_fotello[k]

    for k in ("max_download_workers", "max_file_bytes", "max_image_dimension"):
        if k in old_fotello and k not in mapped["download"]:
            mapped["download"][k] = old_fotello[k]

    for k in ("page_size", "max_query_documents", "request_timeout_seconds"):
        if k in old_fotello and k not in mapped["firestore"]:
            mapped["firestore"][k] = old_fotello[k]

    # Validate cấu hình mới trước khi ghi
    validated_dict = validate_fotello_config_dict(mapped)
    cfg_obj = FotelloConfig(**validated_dict)
    _atomic_write_file(fotello_cfg_path, serialize_fotello_config(cfg_obj))

    # Ghi lại root_cfg_path đã loại bỏ key "fotello"
    _atomic_write_file(root_cfg_path, root_data)


def load_fotello_config(path: Path | None = None) -> FotelloConfig:
    """Nạp cấu hình Fotello từ file ~/.hdr_exe/fotello/config.json.

    - Tự động chạy migration 1 lần nếu còn mục fotello ở config tổng.
    - Nếu file chưa có: tạo file mặc định an toàn.
    - Nếu file có nhưng sai JSON/schema: raise ConfigurationError rõ ràng.
    """
    cfg_path = path or get_fotello_config_path()
    if path is None:
        migrate_fotello_config_if_needed()

    if not cfg_path.is_file():
        default_cfg = FotelloConfig()
        _atomic_write_file(cfg_path, serialize_fotello_config(default_cfg))
        return default_cfg

    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"File cấu hình Fotello {cfg_path} không đúng định dạng JSON: {exc}") from exc
    except Exception as exc:
        raise ConfigurationError(f"Không thể đọc file cấu hình Fotello {cfg_path}: {exc}") from exc

    validated_dict = validate_fotello_config_dict(raw_data)
    return FotelloConfig(**validated_dict)
