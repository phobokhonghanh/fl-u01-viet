"""Licensing Data Models, Exceptions, and Constants for HDR Engines.

Defines standardized structures for license results, strict error codes,
and domain exceptions for authentication and authorization.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

VALID_ENGINES: frozenset[str] = frozenset({"fotello", "autoenhance", "autohdr"})
VALID_LEVELS: frozenset[str] = frozenset({"lite", "plus"})
VALID_MODES: frozenset[str] = frozenset({"single", "batch"})

# Canonical Error Codes
CODE_OK: str = "OK"
CODE_KEY_EMPTY: str = "KEY_EMPTY"
CODE_KEY_NOT_FOUND: str = "KEY_NOT_FOUND"
CODE_KEY_INVALID: str = "KEY_INVALID"
CODE_MACHINE_MISMATCH: str = "MACHINE_MISMATCH"
CODE_VERSION_OUTDATED: str = "VERSION_OUTDATED"
CODE_INVALID_RESPONSE: str = "INVALID_RESPONSE"
CODE_INVALID_ENGINE: str = "INVALID_ENGINE"
CODE_INVALID_MODE: str = "INVALID_MODE"
CODE_FEATURE_NOT_PERMITTED: str = "FEATURE_NOT_PERMITTED"
CODE_NETWORK_TIMEOUT: str = "NETWORK_TIMEOUT"
CODE_NETWORK_ERROR: str = "NETWORK_ERROR"
CODE_SERVER_ERROR: str = "SERVER_ERROR"
CODE_MACHINE_ID_ERROR: str = "MACHINE_ID_ERROR"
CODE_STORAGE_ERROR: str = "STORAGE_ERROR"


class LicensingError(Exception):
    """Ngoại lệ cơ bản cho các lỗi liên quan đến bản quyền và kích hoạt."""

    def __init__(self, message: str, code: str = "LICENSING_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class MachineIdError(LicensingError):
    """Ngoại lệ khi không thể nhận diện được mã phần cứng của hệ điều hành."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code=CODE_MACHINE_ID_ERROR)


class LicensingAccessError(LicensingError):
    """Ngoại lệ khi không đủ quyền truy cập hoặc chế độ chạy không được phép."""
    pass


@dataclass(frozen=True)
class LicenseResult:
    """Kết quả xác thực bản quyền từ server.
    
    Lưu ý: Không lưu trữ raw key để đảm bảo an toàn, tránh rò rỉ vào log hay event.
    """
    valid: bool
    engine: str
    level: str | None = None
    code: str = CODE_OK
    message: str = ""
    pricing: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "engine": self.engine,
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "pricing": dict(self.pricing),
        }
