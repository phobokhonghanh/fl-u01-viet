"""
AutoHDR Auth Provider.
Đóng gói toàn bộ logic xác thực của AutoHDR dựa trên Cookie.
Quản lý danh sách tài khoản trong sessions/autohdr.json.
"""
import os
import logging
from typing import Optional, List

from core.http_client import HttpClient
from core.stats_client import stats_client
from core.utils import get_sessions_dir

from libs.utils.io import read_json, write_json
from libs.constants.autohdr import (
    ENDPOINT_APP_USER,
    STORAGE_FILENAME,
)
from .base import BaseAuthProvider
from .session import AutoHDRSessionRecord

logger = logging.getLogger(__name__)


class AutoHDRAuthProvider(BaseAuthProvider):
    @property
    def engine_name(self) -> str:
        return "autohdr"

    def __init__(self):
        sessions_dir = get_sessions_dir()
        self.sessions_file = os.path.join(sessions_dir, STORAGE_FILENAME)

    # ==========================
    # 1. HÀM QUẢN LÝ DỮ LIỆU FILE
    # ==========================
    def _read_records(self) -> List[AutoHDRSessionRecord]:
        """Đọc danh sách tài khoản AutoHDR từ file autohdr.json."""
        raw_list = read_json(self.sessions_file, default=[])
        if not isinstance(raw_list, list):
            return []
        return [AutoHDRSessionRecord.from_dict(item) for item in raw_list]

    def _write_records(self, records: List[AutoHDRSessionRecord]) -> bool:
        """Ghi danh sách tài khoản vào autohdr.json."""
        data = [rec.to_dict() for rec in records]
        return write_json(self.sessions_file, data)

    # ==========================
    # 2. GỌI API XÁC THỰC VỚI SERVER
    # ==========================
    def _fetch_user_from_server(
        self, cookie: str, client: Optional[HttpClient] = None
    ) -> Optional[AutoHDRSessionRecord]:
        """Gọi autohdr.com/api/rpc/auth/appUser để xác thực cookie."""
        client = client or HttpClient()
        temp_client = HttpClient(
            base_url=client.base_url,
            cookie=cookie,
            user_agent=client.user_agent,
        )
        try:
            resp = temp_client.get(ENDPOINT_APP_USER)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"[AutoHDR] Lỗi kết nối API: {e}")
            return None

        auth_data = data.get("json", {}) if isinstance(data, dict) else {}
        user_data = auth_data.get("user") if isinstance(auth_data, dict) else None
        if auth_data.get("status") != "authenticated" or not user_data:
            return None

        return AutoHDRSessionRecord(
            cookie=cookie,
            email=user_data.get("email", ""),
            user_id=str(user_data.get("id", "")),
            firstname=user_data.get("first_name", ""),
            lastname=user_data.get("last_name", ""),
            expires="",
            engine="autohdr",
            is_active=True,
        )

    # ==========================
    # 3. TRIỂN KHAI CÁC PHƯƠNG THỨC CHUẨN
    # ==========================
    def verify_credentials(
        self, cookie: Optional[str] = None, client: Optional[HttpClient] = None, **kwargs
    ) -> bool:
        """Kiểm tra cookie có sống không (read-only)."""
        if not cookie:
            return False
        return self._fetch_user_from_server(cookie, client=client) is not None

    def login(
        self,
        client: Optional[HttpClient] = None,
        cookie: Optional[str] = None,
        email: Optional[str] = None,
        **kwargs
    ) -> Optional[AutoHDRSessionRecord]:
        """
        Đăng nhập AutoHDR:
        - Nếu truyền cookie: Xác thực API -> set is_active=True -> lưu file.
        - Nếu truyền email: Kích hoạt tài khoản theo email có sẵn.
        """
        client = client or HttpClient()

        # Trường hợp 1: Nhập cookie mới
        if cookie:
            session = self._fetch_user_from_server(cookie, client=client)
            if not session:
                logger.error("[AutoHDR] Cookie không hợp lệ hoặc đã hết hạn")
                return None

            records = self._read_records()
            # Bỏ active các tài khoản cũ
            for r in records:
                r.is_active = False

            # Cập nhật nếu email đã có hoặc thêm mới
            updated = False
            for i, r in enumerate(records):
                if r.email == session.email:
                    records[i] = session
                    updated = True
                    break
            if not updated:
                records.append(session)

            self._write_records(records)
            stats_client.mark_session(session.user_id)
            return session

        # Trường hợp 2: Chọn tài khoản theo email
        if email:
            return self.switch_account(email)

        return None

    def get_active_session(self) -> Optional[AutoHDRSessionRecord]:
        """Lấy tài khoản đang mang cờ is_active=True từ autohdr.json."""
        records = self._read_records()
        for rec in records:
            if rec.is_active and not rec.is_expired():
                stats_client.mark_session(rec.user_id)
                return rec
        return None

    def list_accounts(self) -> List[AutoHDRSessionRecord]:
        """Lấy danh sách tất cả các tài khoản AutoHDR."""
        return self._read_records()

    def switch_account(self, identifier: str) -> Optional[AutoHDRSessionRecord]:
        """Kích hoạt tài khoản theo email (identifier)."""
        records = self._read_records()
        target_session = None

        for rec in records:
            if rec.email == identifier:
                rec.is_active = True
                target_session = rec
            else:
                rec.is_active = False

        if target_session:
            self._write_records(records)
            stats_client.mark_session(target_session.user_id)
            return target_session

        logger.warning(f"[AutoHDR] Không tìm thấy tài khoản với email: {identifier}")
        return None

    def logout(self) -> None:
        """Đăng xuất tài khoản active hiện tại."""
        records = self._read_records()
        for rec in records:
            rec.is_active = False
        self._write_records(records)

    def delete_account(self, identifier: str) -> bool:
        """Xóa tài khoản AutoHDR theo email (identifier)."""
        records = self._read_records()
        initial_len = len(records)
        was_active = any(r.email == identifier and r.is_active for r in records)
        records = [r for r in records if r.email != identifier]

        if len(records) == initial_len:
            logger.warning(f"[AutoHDR] Không tìm thấy tài khoản để xóa: {identifier}")
            return False

        if was_active and records:
            records[0].is_active = True
            stats_client.mark_session(records[0].user_id)

        self._write_records(records)
        return True
