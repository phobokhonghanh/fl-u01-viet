"""
Autoenhance Session Data Model.
Chứa thông tin xác thực API Key của Autoenhance.
"""
from dataclasses import dataclass
from typing import Dict, Any

from .base import BaseSessionRecord


@dataclass
class AutoenhanceSessionRecord(BaseSessionRecord):
    """Bản ghi phiên xác thực của Autoenhance (dựa trên API Key)."""
    api_key: str = ""
    name: str = "Default Key"
    created_at: str = ""
    engine: str = "autoenhance"
    is_active: bool = False

    @property
    def identifier(self) -> str:
        return self.api_key

    @property
    def display_name(self) -> str:
        if len(self.api_key) > 12:
            return f"{self.api_key[:8]}...{self.api_key[-4:]}"
        return self.api_key

    def is_expired(self) -> bool:
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "engine": self.engine,
            "is_active": self.is_active,
            "name": self.name,
            "api_key": self.api_key,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AutoenhanceSessionRecord":
        return cls(
            engine=data.get("engine", "autoenhance"),
            is_active=bool(data.get("is_active", False)),
            name=data.get("name", "Default Key"),
            api_key=data.get("api_key", ""),
            created_at=data.get("created_at", ""),
        )

