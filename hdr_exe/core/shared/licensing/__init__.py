"""Centralized Licensing and Access Control Module for HDR Engines.

Public API:
- activate(engine, key): Online verification and persistent local storage on success.
- check(engine): Strict online verification using locally stored key.
- require_access(engine, mode): Enforces activation status and single/batch authorization.
- clear(engine): Deletes stored local key without resetting server machine binding.
"""
from __future__ import annotations

from core.shared.licensing.client import verify_license_remotely
from core.shared.licensing.machine import get_machine_id
from core.shared.licensing.models import (
    CODE_FEATURE_NOT_PERMITTED,
    CODE_INVALID_ENGINE,
    CODE_INVALID_MODE,
    CODE_KEY_EMPTY,
    CODE_KEY_NOT_FOUND,
    LicenseResult,
    LicensingAccessError,
    MachineIdError,
    VALID_ENGINES,
    VALID_LEVELS,
    VALID_MODES,
)
from core.shared.licensing.storage import get_key, remove_key, set_key


def activate(
    engine: str,
    key: str,
    *,
    server_url: str | None = None,
    timeout_seconds: float | None = None,
) -> LicenseResult:
    """Xác thực bản quyền trực tuyến với server và lưu key cục bộ khi thành công.
    
    Chỉ lưu key vào ~/.hdr_exe/licensing/keys.json khi server trả về valid=True
    và level hợp lệ thuộc ('lite', 'plus').
    """
    eng_norm = str(engine).strip().lower()
    if eng_norm not in VALID_ENGINES:
        return LicenseResult(
            valid=False,
            engine=eng_norm,
            code=CODE_INVALID_ENGINE,
            message=f"Engine '{engine}' không được hỗ trợ. Các engine khả dụng: {sorted(VALID_ENGINES)}",
        )

    key_norm = str(key).strip()
    if not key_norm:
        return LicenseResult(
            valid=False,
            engine=eng_norm,
            code=CODE_KEY_EMPTY,
            message="Mã kích hoạt (key) không được để trống.",
        )

    try:
        machine_id = get_machine_id()
    except MachineIdError as exc:
        return LicenseResult(
            valid=False,
            engine=eng_norm,
            code=exc.code,
            message=exc.message,
        )

    result = verify_license_remotely(
        engine=eng_norm,
        key=key_norm,
        machine_id=machine_id,
        server_url=server_url,
        timeout_seconds=timeout_seconds,
    )

    if result.valid and result.level in VALID_LEVELS:
        set_key(eng_norm, key_norm)

    return result


def check(
    engine: str,
    *,
    server_url: str | None = None,
    timeout_seconds: float | None = None,
) -> LicenseResult:
    """Xác thực trực tuyến bằng key đã lưu trên máy, trả kết quả trạng thái và level.
    
    Quy định:
    - Luôn xác thực trực tuyến qua HTTP POST /api/key/active.
    - Không sử dụng cache thời gian (như cache 12 giờ) để cấp quyền.
    """
    eng_norm = str(engine).strip().lower()
    if eng_norm not in VALID_ENGINES:
        return LicenseResult(
            valid=False,
            engine=eng_norm,
            code=CODE_INVALID_ENGINE,
            message=f"Engine '{engine}' không được hỗ trợ. Các engine khả dụng: {sorted(VALID_ENGINES)}",
        )

    stored_key = get_key(eng_norm)
    if not stored_key:
        return LicenseResult(
            valid=False,
            engine=eng_norm,
            code=CODE_KEY_NOT_FOUND,
            message=f"Chưa kích hoạt bản quyền cho engine '{engine}'. Vui lòng nhập key bản quyền.",
        )

    try:
        machine_id = get_machine_id()
    except MachineIdError as exc:
        return LicenseResult(
            valid=False,
            engine=eng_norm,
            code=exc.code,
            message=exc.message,
        )

    return verify_license_remotely(
        engine=eng_norm,
        key=stored_key,
        machine_id=machine_id,
        server_url=server_url,
        timeout_seconds=timeout_seconds,
    )


def require_access(
    engine: str,
    mode: str,
    *,
    server_url: str | None = None,
    timeout_seconds: float | None = None,
) -> LicenseResult:
    """Kiểm tra kích hoạt và quyền thực thi single/batch cho engine.
    
    Chính sách:
    - Lite: Chỉ được chạy mode 'single' (trong giới hạn output/job). Bị từ chối khi chọn batch.
    - Plus: Được chạy cả mode 'single' và 'batch'.
    
    Raises:
        LicensingAccessError: Nếu key không hợp lệ, không thể xác thực hoặc không đủ quyền.
    """
    mode_norm = str(mode).strip().lower()
    if mode_norm not in VALID_MODES:
        raise LicensingAccessError(
            f"Chế độ chạy '{mode}' không hợp lệ. Chỉ hỗ trợ 'single' hoặc 'batch'.",
            code=CODE_INVALID_MODE,
        )

    res = check(engine, server_url=server_url, timeout_seconds=timeout_seconds)
    if not res.valid:
        raise LicensingAccessError(res.message, code=res.code)

    if mode_norm == "batch" and res.level == "lite":
        raise LicensingAccessError(
            f"Engine '{engine}' với gói Lite chỉ hỗ trợ chế độ 'single'. "
            f"Vui lòng nâng cấp lên Plus để sử dụng tính năng batch.",
            code=CODE_FEATURE_NOT_PERMITTED,
        )

    return res


def clear(engine: str) -> None:
    """Xóa key đã lưu trên máy, không tự động reset liên kết máy trên server."""
    eng_norm = str(engine).strip().lower()
    remove_key(eng_norm)


__all__ = [
    "activate",
    "check",
    "require_access",
    "clear",
    "get_machine_id",
    "LicenseResult",
    "LicensingAccessError",
    "MachineIdError",
    "VALID_ENGINES",
    "VALID_LEVELS",
    "VALID_MODES",
]
