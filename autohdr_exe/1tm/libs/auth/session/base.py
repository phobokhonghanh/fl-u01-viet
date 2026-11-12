"""
Abstract Base Session Data Model.
Quy định interface chung cho mọi bản ghi phiên (SessionRecord).
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class BaseSessionRecord(ABC):
    """Lớp trừu tượng cơ sở cho mọi Session/Credential Record."""
    engine: str
    is_active: bool = False

    @property
    @abstractmethod
    def identifier(self) -> str:
        """Khóa định danh duy nhất (dùng để switch/lookup tài khoản)."""
        pass

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Tên hiển thị thân thiện trên giao diện (Dropdown, Label)."""
        pass

    @abstractmethod
    def to_dict(self) -> Dict[str, Any]:
        """Serialize sang dict để lưu JSON."""
        pass

    @abstractmethod
    def is_expired(self) -> bool:
        """Kiểm tra phiên có hết hạn không."""
        pass

