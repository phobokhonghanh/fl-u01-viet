from dataclasses import dataclass
from datetime import datetime
from typing import Optional

DEFAULT_KEY_PRODUCT = "autohdr"
VALID_KEY_PRODUCTS = {"autohdr", "fotello"}

VALID_KEY_LEVELS = ["lite", "plus"]
DEFAULT_KEY_LEVEL = "lite"


def normalize_key_product(product: Optional[str] = None) -> str:
    value = str(product or DEFAULT_KEY_PRODUCT).strip().lower()
    return value or DEFAULT_KEY_PRODUCT


def is_valid_key_product(product: Optional[str] = None) -> bool:
    return normalize_key_product(product) in VALID_KEY_PRODUCTS


def normalize_key_level(level: Optional[str] = None) -> str:
    value = str(level or DEFAULT_KEY_LEVEL).strip().lower()
    return value if value in VALID_KEY_LEVELS else DEFAULT_KEY_LEVEL


def is_valid_key_level(level: Optional[str] = None) -> bool:
    return normalize_key_level(level) in VALID_KEY_LEVELS


def check_level_access(current_level: str, required_level: str) -> bool:
    """
    Kiểm tra xem cấp độ hiện tại có đủ quyền sử dụng tính năng yêu cầu hay không.
    So sánh dựa trên chỉ mục trong danh sách VALID_KEY_LEVELS.
    """
    curr = normalize_key_level(current_level)
    req = normalize_key_level(required_level)
    try:
        return VALID_KEY_LEVELS.index(curr) >= VALID_KEY_LEVELS.index(req)
    except ValueError:
        return False


@dataclass
class KeyRecord:
    key: str
    name: str = ""
    is_active: bool = True
    expires_at: Optional[str] = None # ISO format datetime
    machine_id: Optional[str] = None # Unique ID of the locked machine
    product: str = DEFAULT_KEY_PRODUCT
    level: str = DEFAULT_KEY_LEVEL

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
            "machine_id": self.machine_id,
            "product": normalize_key_product(self.product),
            "level": normalize_key_level(self.level)
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "KeyRecord":
        return cls(
            key=data.get("key", ""),
            name=data.get("name", ""),
            is_active=data.get("is_active", True),
            expires_at=data.get("expires_at", None),
            machine_id=data.get("machine_id", None),
            product=normalize_key_product(data.get("product")),
            level=normalize_key_level(data.get("level"))
        )

