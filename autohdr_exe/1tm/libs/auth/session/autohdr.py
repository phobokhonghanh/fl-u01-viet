"""
AutoHDR Session Data Model.
Chứa thông tin xác thực phiên làm việc bằng Cookie của AutoHDR.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any

from .base import BaseSessionRecord


@dataclass
class AutoHDRSessionRecord(BaseSessionRecord):
    """Bản ghi phiên xác thực của AutoHDR (dựa trên Cookie)."""
    email: str = ""
    cookie: str = ""
    user_id: str = ""
    firstname: str = ""
    lastname: str = ""
    expires: str = ""
    engine: str = "autohdr"
    is_active: bool = False

    @property
    def identifier(self) -> str:
        return self.email

    @property
    def display_name(self) -> str:
        fullname = f"{self.firstname} {self.lastname}".strip()
        return f"{fullname} ({self.email})" if fullname else self.email

    def is_expired(self) -> bool:
        if not self.expires:
            return False
        try:
            expires_str = self.expires.replace("Z", "+00:00")
            expires_dt = datetime.fromisoformat(expires_str)
            return datetime.now(expires_dt.tzinfo) >= expires_dt
        except (ValueError, TypeError):
            return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "engine": self.engine,
            "is_active": self.is_active,
            "email": self.email,
            "user_id": self.user_id,
            "firstname": self.firstname,
            "lastname": self.lastname,
            "cookie": self.cookie,
            "expires": self.expires,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AutoHDRSessionRecord":
        return cls(
            engine=data.get("engine", "autohdr"),
            is_active=bool(data.get("is_active", False)),
            email=data.get("email", ""),
            user_id=str(data.get("user_id", "")),
            firstname=data.get("firstname", ""),
            lastname=data.get("lastname", ""),
            cookie=data.get("cookie", ""),
            expires=data.get("expires", ""),
        )

