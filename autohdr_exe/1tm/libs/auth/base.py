"""
Abstract Base Auth Provider & Registry.
Chuẩn hóa interface xác thực chung cho mọi engine xử lý ảnh.
"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, List, Any
import logging

from .session import BaseSessionRecord

logger = logging.getLogger(__name__)


class BaseAuthProvider(ABC):
    """Lớp cơ sở trừu tượng cho mọi Auth Provider."""

    @property
    @abstractmethod
    def engine_name(self) -> str:
        """Tên định danh engine (vd: 'autohdr', 'autoenhance')."""
        pass

    @abstractmethod
    def verify_credentials(self, **kwargs) -> bool:
        """Kiểm tra tính hợp lệ của credentials với server (không có side-effect)."""
        pass

    @abstractmethod
    def login(self, **kwargs) -> Optional[BaseSessionRecord]:
        """Xác thực thông tin mới, lưu trữ nếu thành công và trả về session."""
        pass

    @abstractmethod
    def get_active_session(self) -> Optional[BaseSessionRecord]:
        """Khôi phục phiên đăng nhập đang hoạt động từ file lưu trữ."""
        pass

    @abstractmethod
    def list_accounts(self) -> List[BaseSessionRecord]:
        """Lấy danh sách tất cả các tài khoản/key đã lưu của engine này."""
        pass

    @abstractmethod
    def switch_account(self, identifier: str) -> Optional[BaseSessionRecord]:
        """Chuyển đổi tài khoản kích hoạt theo identifier và lưu lại file."""
        pass

    @abstractmethod
    def logout(self) -> None:
        """Đăng xuất, hủy kích hoạt phiên hiện tại của engine này."""
        pass

    @abstractmethod
    def delete_account(self, identifier: str) -> bool:
        """Xóa tài khoản khỏi danh sách lưu trữ."""
        pass

    def authenticate(self, **kwargs) -> Optional[BaseSessionRecord]:
        """
        Template method điều phối:
        - Nếu có truyền credentials (cookie, email, api_key...) -> gọi login()
        - Nếu không truyền gì -> gọi get_active_session() để auto-login.
        """
        has_input = any(v is not None for v in kwargs.values())
        if has_input:
            return self.login(**kwargs)
        return self.get_active_session()


class AuthProviderRegistry:
    """Registry quản lý và cung cấp Auth Provider theo engine."""
    _providers: Dict[str, BaseAuthProvider] = {}

    @classmethod
    def register(cls, engine: str, provider: BaseAuthProvider) -> None:
        cls._providers[engine.lower()] = provider
        logger.debug(f"Registered auth provider for engine: {engine}")

    @classmethod
    def get_provider(cls, engine: str) -> BaseAuthProvider:
        provider = cls._providers.get(engine.lower())
        if not provider:
            supported = ", ".join(cls._providers.keys())
            raise ValueError(f"Chưa hỗ trợ auth cho engine: '{engine}'. Hỗ trợ: [{supported}]")
        return provider

    @classmethod
    def list_supported_engines(cls) -> List[str]:
        return list(cls._providers.keys())

