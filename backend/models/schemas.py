from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class KeyRecord:
    key: str
    name: str = ""
    is_active: bool = True
    expires_at: Optional[str] = None # ISO format datetime
    machine_id: Optional[str] = None # Unique ID of the locked machine

    def is_expired(self) -> bool:
        if not self.is_active:
            return True
        if not self.expires_at:
            return False # No expiration means valid indefinitely

        try:
            expires_str = self.expires_at.replace("Z", "+00:00")
            expires_dt = datetime.fromisoformat(expires_str)
            now = datetime.now(expires_dt.tzinfo)
            return now >= expires_dt
        except (ValueError, TypeError):
            return True

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "is_active": self.is_active,
            "expires_at": self.expires_at,
            "machine_id": self.machine_id
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "KeyRecord":
        return cls(
            key=data.get("key", ""),
            name=data.get("name", ""),
            is_active=data.get("is_active", True),
            expires_at=data.get("expires_at", None),
            machine_id=data.get("machine_id", None)
        )
