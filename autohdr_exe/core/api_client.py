"""
API Client — Gọi API xác thực Key và tối ưu bằng Cache 6h.
"""

import requests
import os
import time
import logging
from typing import Optional
from core.cache import cache
from core.stats_client import stats_client
from core.utils import get_hwid

logger = logging.getLogger(__name__)

# Thời hạn Cache: 6 giờ (tính bằng giây)
CACHE_DURATION = 6 * 60 * 60
# CACHE_DURATION = 60

class ApiClient:
    """Client for backend key validation with 6h caching."""

    def __init__(self, base_url: Optional[str] = None):
        if not base_url:
            base_url = os.getenv("AUTOHDR_API_BASE", "https://u01-viet-backend.up.railway.app")
        self.base_url = base_url.rstrip("/")
        self.last_check_status = "idle"

    def _clear_license_cache(self):
        """Clear local license cache fields."""
        cache.delete("active_key")
        cache.delete("license_last_check")
        cache.delete("license_machine_id")

    def _save_license_cache(self, key: str, machine_id: str, checked_at: float) -> None:
        """Persist validated license data locally."""
        cache.set("active_key", key)
        cache.set("license_last_check", checked_at)
        cache.set("license_machine_id", machine_id)

    def _get_cached_key_if_valid(self, machine_id: str) -> Optional[str]:
        """Return cached key when the local license state is still valid."""
        cached_key = cache.get("active_key")
        cached_machine_id = cache.get("license_machine_id")
        last_check = cache.get("license_last_check")

        if not cached_key or not cached_machine_id or last_check in (None, ""):
            self.last_check_status = "missing"
            return None

        if cached_machine_id != machine_id:
            self._clear_license_cache()
            self.last_check_status = "machine_mismatch"
            return None

        try:
            last_check_ts = float(last_check)
        except (TypeError, ValueError):
            self._clear_license_cache()
            self.last_check_status = "invalid_cache"
            return None

        now = time.time()
        age = now - last_check_ts
        if age < 0 and abs(age) > CACHE_DURATION:
            self._clear_license_cache()
            self.last_check_status = "clock_rollback"
            return None

        if age < 0:
            self._clear_license_cache()
            self.last_check_status = "invalid_cache"
            return None

        if age >= CACHE_DURATION:
            self.last_check_status = "expired"
            print(self.last_check_status )
            is_valid = self._check_remote_key(cached_key, machine_id)
            print(self.last_check_status )
            if is_valid:
                return cached_key
            return None

        self.last_check_status = "valid_cached"
        return cached_key

    def _check_remote_key(self, key: str, machine_id: str) -> bool:
        """Validate the provided key against the backend."""
        now = time.time()

        try:
            res = requests.post(
                f"{self.base_url}/api/key/active",
                json={"key": key, "machine_id": machine_id},
                timeout=15,
            )

            if res.status_code == 200:
                is_valid = res.json().get("valid", False)
                if is_valid:
                    self._save_license_cache(key, machine_id, now)
                    self.last_check_status = "valid_remote"
                    stats_client.dispatch_async()
                    return True

                self._clear_license_cache()
                self.last_check_status = "invalid"
                return False

            if res.status_code == 403:
                self._clear_license_cache()
                self.last_check_status = "invalid"
                return False

            res.raise_for_status()

        except requests.exceptions.ConnectionError:
            logger.error("Không thể kết nối mạng để check key.")
            self.last_check_status = "network_error"
        except Exception as e:
            logger.error(f"Lỗi kiểm tra key qua API: {e}")
            self.last_check_status = "error"

        return False

    def check_key(self, key: Optional[str] = None, machine_id: Optional[str] = None) -> bool:
        """
        Kiểm tra key theo 2 mode:
        - Có input key: chỉ check key đó với backend.
        - Không có input key: chỉ check local cache còn hạn hay không.
        """
        if not machine_id:
            machine_id = get_hwid()

        if key is not None:
            normalized_key = key.strip() if isinstance(key, str) else None
            if normalized_key == "":
                self.last_check_status = "missing_input"
                return False
            if normalized_key:
                return self._check_remote_key(normalized_key, machine_id)
        else: 
            cached_key = self._get_cached_key_if_valid(machine_id)
            if cached_key:
                return True
        return False
