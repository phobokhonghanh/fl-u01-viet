"""HTTP Client for Online License Verification.

Communicates with the licensing backend at /api/key/active with strict
schema validation, timeout handling, and payload isolation.
"""
from __future__ import annotations

import json
from urllib.parse import urlparse

import requests

from core.shared.config import load_app_config
from core.shared.licensing.models import (
    CODE_INVALID_ENGINE,
    CODE_INVALID_RESPONSE,
    CODE_KEY_INVALID,
    CODE_MACHINE_MISMATCH,
    CODE_NETWORK_ERROR,
    CODE_NETWORK_TIMEOUT,
    CODE_OK,
    CODE_SERVER_ERROR,
    CODE_VERSION_OUTDATED,
    LicenseResult,
)

DEFAULT_SERVER_URL: str = "https://u01-viet-backend.up.railway.app"
DEFAULT_TIMEOUT_SECONDS: float = 10.0
CLIENT_VERSION: str = "4.0"


def get_licensing_endpoint_config() -> tuple[str, float]:
    """Đọc cấu hình URL server và timeout từ mục licensing trong config tổng."""
    app_cfg = load_app_config()
    lic_cfg = app_cfg.get("licensing", {})
    server_url = str(lic_cfg.get("server_url", DEFAULT_SERVER_URL)).rstrip("/")
    timeout = float(lic_cfg.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    return server_url, timeout


def verify_license_remotely(
    engine: str,
    key: str,
    machine_id: str,
    *,
    server_url: str | None = None,
    timeout_seconds: float | None = None,
    session: requests.Session | None = None,
) -> LicenseResult:
    """Gửi yêu cầu xác thực key tới server kích hoạt (/api/key/active).
    
    Yêu cầu:
    - Gửi rõ product, key, machine_id, client_version, client_name.
    - Không log raw key ra màn hình hoặc hệ thống log.
    - Bắt buộc HTTPS cho server_url (cho phép localhost khi kiểm thử).
    - Chỉ chấp nhận phản hồi hợp lệ với valid kiểu boolean và level thuộc ('lite', 'plus').
    """
    # if key.strip().lower() in ("dev", "a"):
    #     return LicenseResult(
    #         valid=True,
    #         engine=engine,
    #         level="plus",
    #         code=CODE_OK,
    #         message="Bản quyền chế độ phát triển hợp lệ.",
    #         pricing={},
    #     )
    configured_url, configured_timeout = get_licensing_endpoint_config()
    base_url = (server_url or configured_url).rstrip("/")
    timeout = timeout_seconds if timeout_seconds is not None else configured_timeout

    parsed = urlparse(base_url)
    is_localhost = (
        parsed.hostname in ("localhost", "127.0.0.1", "::1")
        or (parsed.netloc and parsed.netloc.startswith("testserver"))
    )
    if parsed.scheme != "https" and not is_localhost:
        raise ValueError(f"Licensing server URL bắt buộc phải sử dụng giao thức HTTPS: {base_url}")

    endpoint = f"{base_url}/api/key/active"

    payload = {
        "product": engine.strip().lower(),
        "key": key.strip(),
        "machine_id": machine_id.strip(),
        "client_version": CLIENT_VERSION,
        "client_name": "hdr_exe",
    }

    http_client = session or requests

    try:
        response = http_client.post(endpoint, json=payload, timeout=timeout, allow_redirects=False)
    except requests.exceptions.Timeout:
        return LicenseResult(
            valid=False,
            engine=engine,
            code=CODE_NETWORK_TIMEOUT,
            message="Hết thời gian chờ kết nối đến server kích hoạt (Timeout).",
        )
    except requests.exceptions.ConnectionError:
        return LicenseResult(
            valid=False,
            engine=engine,
            code=CODE_NETWORK_ERROR,
            message="Không thể kết nối đến server kích hoạt bản quyền. Vui lòng kiểm tra mạng.",
        )
    except requests.exceptions.RequestException as exc:
        return LicenseResult(
            valid=False,
            engine=engine,
            code=CODE_NETWORK_ERROR,
            message=f"Lỗi mạng khi kết nối server kích hoạt: {exc}",
        )

    if response.status_code == 400:
        return LicenseResult(
            valid=False,
            engine=engine,
            code=CODE_INVALID_ENGINE,
            message=f"Engine '{engine}' không được server kích hoạt hỗ trợ.",
        )

    if response.status_code == 403:
        detail = "Key không hợp lệ, hết hạn, hoặc đã được sử dụng trên thiết bị khác."
        try:
            err_data = response.json()
            if isinstance(err_data, dict) and "detail" in err_data:
                detail = str(err_data["detail"])
        except Exception:
            pass
        return LicenseResult(
            valid=False,
            engine=engine,
            code=CODE_KEY_INVALID,
            message=detail,
        )

    if response.status_code >= 500:
        return LicenseResult(
            valid=False,
            engine=engine,
            code=CODE_SERVER_ERROR,
            message=f"Contact Admin (Code HTTP {response.status_code}).",
        )

    if response.status_code != 200:
        return LicenseResult(
            valid=False,
            engine=engine,
            code=CODE_KEY_INVALID,
            message=f"Server kích hoạt trả mã HTTP không mong đợi: {response.status_code}",
        )

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError):
        return LicenseResult(
            valid=False,
            engine=engine,
            code=CODE_INVALID_RESPONSE,
            message="Server kích hoạt trả phản hồi không phải định dạng JSON hợp lệ.",
        )

    if not isinstance(data, dict):
        return LicenseResult(
            valid=False,
            engine=engine,
            code=CODE_INVALID_RESPONSE,
            message="Server kích hoạt trả phản hồi không phải JSON object.",
        )

    raw_valid = data.get("valid")
    if not isinstance(raw_valid, bool):
        return LicenseResult(
            valid=False,
            engine=engine,
            code=CODE_INVALID_RESPONSE,
            message="Phản hồi từ server thiếu trường 'valid' kiểu boolean.",
        )

    if not raw_valid:
        msg = str(data.get("message") or "Key bản quyền không hợp lệ.")
        if data.get("status") == 'error':
            code = CODE_VERSION_OUTDATED
        elif "máy khác" in msg.lower() or "machine" in msg.lower():
            code = CODE_MACHINE_MISMATCH
        else:
            code = CODE_KEY_INVALID
        return LicenseResult(
            valid=False,
            engine=engine,
            code=code,
            message=msg,
        )

    raw_level = data.get("level")
    if not isinstance(raw_level, str) or raw_level.strip().lower() not in ("lite", "plus"):
        return LicenseResult(
            valid=False,
            engine=engine,
            code=CODE_INVALID_RESPONSE,
            message="Server trả về cấp độ (level) bản quyền không hợp lệ.",
        )

    level = raw_level.strip().lower()
    pricing = data.get("pricing") if isinstance(data.get("pricing"), dict) else {}

    return LicenseResult(
        valid=True,
        engine=engine,
        level=level,
        code=CODE_OK,
        message="Xác thực bản quyền thành công.",
        pricing=pricing,
    )
