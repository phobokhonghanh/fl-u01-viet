"""
Autoenhance Auth Provider.
Đóng gói logic xác thực x-api-key của Autoenhance v3.
Quản lý danh sách các API Key trong sessions/autoenhance.json.
"""
import os
import time
import logging
from datetime import datetime
from typing import Optional, List
import requests

from core.utils import get_sessions_dir

from libs.utils.io import read_json, write_json, delete_file
from libs.constants.autoenhance import (
    API_BASE,
    ENDPOINT_VALIDATION,
    STORAGE_FILENAME,
    USER_AGENT,
)
from .base import BaseAuthProvider
from .session import AutoenhanceSessionRecord

logger = logging.getLogger(__name__)


class AutoenhanceAuthProvider(BaseAuthProvider):
    @property
    def engine_name(self) -> str:
        return "autoenhance"

    def __init__(self):
        sessions_dir = get_sessions_dir()
        self.sessions_file = os.path.join(sessions_dir, STORAGE_FILENAME)

    # ==========================
    # 1. HÀM QUẢN LÝ DỮ LIỆU FILE
    # ==========================
    def _read_records(self) -> List[AutoenhanceSessionRecord]:
        """Đọc danh sách API Key từ file autoenhance.json."""
        raw_list = read_json(self.sessions_file, default=[])
        if not isinstance(raw_list, list):
            return []
        return [AutoenhanceSessionRecord.from_dict(item) for item in raw_list]

    def _write_records(self, records: List[AutoenhanceSessionRecord]) -> bool:
        """Ghi danh sách API key vào autoenhance.json."""
        data = [rec.to_dict() for rec in records]
        return write_json(self.sessions_file, data)

    # ==========================
    # 2. GỌI API XÁC THỰC VỚI SERVER
    # ==========================
    def verify_credentials(self, api_key: Optional[str] = None, **kwargs) -> bool:
        """Kiểm tra API Key có sống không qua endpoint /orders/?per_page=1."""
        key = (api_key or "").strip()
        if not key:
            return False

        url = f"{API_BASE}{ENDPOINT_VALIDATION}"
        headers = {
            "x-api-key": key,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"[Autoenhance] Validate API key failed: {e}")
            return False

    # ==========================
    # 3. TRIỂN KHAI CÁC PHƯƠNG THỨC CHUẨN
    # ==========================
    def login(
        self,
        api_key: Optional[str] = None,
        name: Optional[str] = None,
        **kwargs
    ) -> Optional[AutoenhanceSessionRecord]:
        """
        Đăng nhập Autoenhance bằng API Key mới:
        - Kiểm tra tính hợp lệ với server Autoenhance v3.
        - Đặt is_active=True cho key này, tắt active các key khác.
        - Lưu vào autoenhance.json.
        """
        key = (api_key or "").strip()
        if not key:
            return None

        if not self.verify_credentials(api_key=key):
            logger.error("[Autoenhance] API Key không hợp lệ hoặc không thể kết nối server")
            return None

        records = self._read_records()
        for r in records:
            r.is_active = False

        session_name = (name or "").strip() or f"Key {len(records) + 1}"
        new_session = AutoenhanceSessionRecord(
            api_key=key,
            name=session_name,
            created_at=datetime.now().isoformat(),
            engine="autoenhance",
            is_active=True,
        )

        # Cập nhật nếu đã có key này hoặc append mới
        updated = False
        for i, r in enumerate(records):
            if r.api_key == key:
                records[i] = new_session
                updated = True
                break
        if not updated:
            records.append(new_session)

        self._write_records(records)
        return new_session

    def get_active_session(self) -> Optional[AutoenhanceSessionRecord]:
        """Lấy API Key đang mang cờ is_active=True."""
        records = self._read_records()
        for rec in records:
            if rec.is_active:
                return rec
        return None

    def list_accounts(self) -> List[AutoenhanceSessionRecord]:
        """Lấy danh sách tất cả các API Key Autoenhance đã lưu."""
        return self._read_records()

    def switch_account(self, identifier: str) -> Optional[AutoenhanceSessionRecord]:
        """Kích hoạt API key theo chuỗi api_key hoặc name."""
        records = self._read_records()
        target_session = None

        for rec in records:
            if rec.api_key == identifier or rec.name == identifier:
                rec.is_active = True
                target_session = rec
            else:
                rec.is_active = False

        if target_session:
            self._write_records(records)
            return target_session

        logger.warning(f"[Autoenhance] Không tìm thấy API key: {identifier}")
        return None

    def logout(self) -> None:
        """Hủy kích hoạt API key active hiện tại."""
        records = self._read_records()
        for rec in records:
            rec.is_active = False
        self._write_records(records)

    def delete_account(self, identifier: str) -> bool:
        """Xóa API Key Autoenhance theo api_key hoặc name (identifier)."""
        records = self._read_records()
        initial_len = len(records)
        was_active = any((r.api_key == identifier or r.name == identifier) and r.is_active for r in records)
        records = [r for r in records if r.api_key != identifier and r.name != identifier]

        if len(records) == initial_len:
            logger.warning(f"[Autoenhance] Không tìm thấy API key để xóa: {identifier}")
            return False

        if was_active and records:
            records[0].is_active = True

        self._write_records(records)
        return True
