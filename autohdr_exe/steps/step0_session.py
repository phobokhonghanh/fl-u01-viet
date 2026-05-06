"""
Step 0: Session Management (Local).

Calls autohdr.com/api/auth/session directly with cookie to get user info.
Saves session to local cache for reuse.
No server-side sessions.json needed.
"""

import json
import os
import logging
import requests
from typing import Optional

from core.http_client import HttpClient
from core.logger import log
from core.cache import cache
from core.utils import get_sessions_dir
from models.schemas import SessionRecord

logger = logging.getLogger(__name__)

SESSIONS_FILE = os.path.join(get_sessions_dir(), "sessions.json")


def _load_sessions() -> list:
    """Load session records from local file."""
    if not os.path.exists(SESSIONS_FILE):
        return []
    try:
        with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _save_sessions(records: list) -> None:
    """Save session records to local file."""
    os.makedirs(os.path.dirname(SESSIONS_FILE), exist_ok=True)
    with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def _find_session_by_email(records: list, email: str) -> Optional[SessionRecord]:
    """Find a session record by email."""
    for record in records:
        if record.get("email") == email:
            return SessionRecord.from_dict(record)
    return None


def _update_session(records: list, session: SessionRecord) -> list:
    """Update or insert a session record."""
    for i, record in enumerate(records):
        if record.get("email") == session.email:
            records[i] = session.to_dict()
            return records
    records.append(session.to_dict())
    return records


def _fetch_session_from_api(client: HttpClient, cookie: str) -> Optional[SessionRecord]:
    """
    Call autohdr.com/api/auth/session to get user info from cookie.
    """
    step = 0

    # Create a temporary client with the provided cookie
    temp_client = HttpClient(
        base_url=client.base_url,
        cookie=cookie,
        user_agent=client.user_agent,
    )

    try:
        response = temp_client.get("/api/auth/session")
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.HTTPError as e:
        log(logger, "ERROR", step, f"HTTP Error: {e.response.status_code} {e.response.reason}")
        if e.response.status_code == 403:
            log(logger, "ERROR", step, "Cookie không hợp lệ hoặc IP bị chặn (403)")
        return None
    except Exception as e:
        log(logger, "ERROR", step, f"Lỗi kết nối: {e}")
        return None

    user_data = data.get("user")
    if not user_data:
        log(logger, "ERROR", step, f"Không có user data trong response: {data}")
        return None

    session = SessionRecord(
        cookie=cookie,
        email=user_data.get("email", ""),
        user_id=str(user_data.get("id", "")),
        firstname=user_data.get("first_name", ""),
        lastname=user_data.get("last_name", ""),
        expires=data.get("expires", ""),
    )

    log(logger, "INFO", step, f"Session OK: email={session.email}, user_id={session.user_id}")
    return session


def execute(client: HttpClient, cookie: Optional[str] = None, email: Optional[str] = None) -> Optional[SessionRecord]:
    """
    Execute Step 0: Resolve session from cookie or cached email.
    
    Flow:
    1. Cookie provided → call API → save session
    2. Email provided → lookup saved session → check expiry
    3. Nothing → return None (need cookie)
    
    Returns:
        SessionRecord on success, None on failure.
    """
    step = 0
    sessions = _load_sessions()

    # Case 1: Cookie provided
    if cookie:
        log(logger, "INFO", step, "Đang xác thực cookie...")
        session = _fetch_session_from_api(client, cookie)

        if session is None:
            log(logger, "ERROR", step, "Cookie không hợp lệ")
            return None

        # Save session locally
        sessions = _update_session(sessions, session)
        _save_sessions(sessions)

        # Update cache
        cache.set("email", session.email)
        cache.set("cookie", session.cookie)

        log(logger, "INFO", step, f"Xác thực thành công: {session.email}")
        return session

    # Case 2: Email provided → lookup saved session
    if email:
        log(logger, "INFO", step, f"Tìm session cho email: {email}")
        session = _find_session_by_email(sessions, email)

        if session:
            if session.is_expired():
                log(logger, "ERROR", step, f"Session cho {email} đã hết hạn. Vui lòng nhập cookie mới.")
                return None

            log(logger, "INFO", step, f"Tìm thấy session: {session.email}")
            return session

        log(logger, "INFO", step, f"Không tìm thấy session cho: {email}")

    # Case 3: Try cached cookie
    cached_cookie = cache.get("cookie")
    if cached_cookie:
        log(logger, "INFO", step, "Sử dụng cookie đã lưu...")
        session = _fetch_session_from_api(client, cached_cookie)
        if session:
            sessions = _update_session(sessions, session)
            _save_sessions(sessions)
            cache.set("email", session.email)
            return session
        else:
            log(logger, "ERROR", step, "Cookie đã lưu không còn hợp lệ")
            cache.delete("cookie")

    log(logger, "ERROR", step, "Không có thông tin xác thực. Vui lòng nhập cookie.")
    return None
