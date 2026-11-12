"""
Universal Auth Package for Multi-Engine Architecture.
Export các interface, model và facade functions cho toàn app.
"""
from typing import Optional, List

from .session import (
    BaseSessionRecord,
    AutoHDRSessionRecord,
    AutoenhanceSessionRecord,
)
from .base import BaseAuthProvider, AuthProviderRegistry
from .autohdr import AutoHDRAuthProvider
from .autoenhance import AutoenhanceAuthProvider

# Đăng ký tự động các providers mặc định
AuthProviderRegistry.register("autohdr", AutoHDRAuthProvider())
AuthProviderRegistry.register("autoenhance", AutoenhanceAuthProvider())


def get_auth_provider(engine: str = "autohdr") -> BaseAuthProvider:
    """Lấy provider tương ứng cho engine chỉ định."""
    return AuthProviderRegistry.get_provider(engine)


def authenticate(engine: str = "autohdr", **kwargs) -> Optional[BaseSessionRecord]:
    """Hàm facade tiện lợi để xác thực hoặc auto-login cho bất kỳ engine nào."""
    provider = get_auth_provider(engine)
    return provider.authenticate(**kwargs)


def get_active_session(engine: str = "autohdr") -> Optional[BaseSessionRecord]:
    """Lấy session đang kích hoạt của engine chỉ định."""
    provider = get_auth_provider(engine)
    return provider.get_active_session()


def list_accounts(engine: str = "autohdr") -> List[BaseSessionRecord]:
    """Lấy danh sách các tài khoản / API Key đã lưu của engine chỉ định."""
    provider = get_auth_provider(engine)
    return provider.list_accounts()


def switch_account(identifier: str, engine: str = "autohdr") -> Optional[BaseSessionRecord]:
    """Chuyển đổi tài khoản đang kích hoạt cho engine chỉ định."""
    provider = get_auth_provider(engine)
    return provider.switch_account(identifier)


def logout(engine: str = "autohdr") -> None:
    """Đăng xuất tài khoản active của engine chỉ định."""
    provider = get_auth_provider(engine)
    provider.logout()


def delete_account(identifier: str, engine: str = "autohdr") -> bool:
    """Xóa tài khoản / API Key của engine chỉ định."""
    provider = get_auth_provider(engine)
    return provider.delete_account(identifier)


__all__ = [
    # Data Models
    "BaseSessionRecord",
    "AutoHDRSessionRecord",
    "AutoenhanceSessionRecord",
    # Core Classes
    "BaseAuthProvider",
    "AuthProviderRegistry",
    "AutoHDRAuthProvider",
    "AutoenhanceAuthProvider",
    # Facade Functions
    "get_auth_provider",
    "authenticate",
    "get_active_session",
    "list_accounts",
    "switch_account",
    "delete_account",
    "logout",
]

