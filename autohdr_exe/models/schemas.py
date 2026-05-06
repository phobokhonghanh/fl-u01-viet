"""
Data models for the AutoHDR Client EXE v2 pipeline.
Adapted from autohdr_backend/models/schemas.py — only client-relevant models.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class PresignedUrl:
    """A single presigned URL from step1 response."""
    filename: str
    url: str


@dataclass
class PipelineContext:
    """
    Context object passed through pipeline steps.
    Holds all state needed across Steps 1-7.
    """
    file_paths: List[str]
    address: str
    email: str
    firstname: str
    lastname: str
    user_id: str
    unique_str: str = ""
    presigned_urls: List[PresignedUrl] = field(default_factory=list)
    filenames: List[str] = field(default_factory=list)
    photoshoot_id: Optional[int] = None
    processed_urls: List[str] = field(default_factory=list)


@dataclass
class SessionRecord:
    """
    Session record for storing cookie and user info locally.
    """
    cookie: str
    email: str
    user_id: str
    firstname: str
    lastname: str
    expires: str

    def is_expired(self) -> bool:
        """Check if the session has expired."""
        try:
            expires_str = self.expires.replace("Z", "+00:00")
            expires_dt = datetime.fromisoformat(expires_str)
            now = datetime.now(expires_dt.tzinfo)
            return now >= expires_dt
        except (ValueError, TypeError):
            return True

    def to_dict(self) -> dict:
        return {
            "cookie": self.cookie,
            "email": self.email,
            "user_id": self.user_id,
            "firstname": self.firstname,
            "lastname": self.lastname,
            "expires": self.expires,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionRecord":
        return cls(
            cookie=data.get("cookie", ""),
            email=data.get("email", ""),
            user_id=str(data.get("user_id", "")),
            firstname=data.get("firstname", ""),
            lastname=data.get("lastname", ""),
            expires=data.get("expires", ""),
        )
