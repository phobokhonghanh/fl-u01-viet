"""
API Client — Gọi API xác thực Key và tối ưu bằng Cache 24h.
"""

import requests
import os
import time
import logging
from typing import Optional
from core.cache import cache
from core.utils import get_hwid

logger = logging.getLogger(__name__)

# Thời hạn Cache: 24 giờ (tính bằng giây)
CACHE_DURATION = 24 * 60 * 60 

class ApiClient:
    """Client for backend key validation with 24h caching."""

    def __init__(self, base_url: Optional[str] = None):
        if not base_url:
            base_url = os.getenv("AUTOHDR_API_BASE", "https://autohdr-backend.up.railway.app")
        self.base_url = base_url.rstrip("/")

    def check_key(self, key: str, machine_id: Optional[str] = None) -> bool:
        """
        Kiểm tra Key qua API, sử dụng cache 24h để hạn chế request.
        """
        if not key:
            return False
        
        if not machine_id:
            machine_id = get_hwid()

        # --- 1. KIỂM TRA CACHE 24H ---
        # Lấy thông tin từ core.cache (lưu trong file .cache nội bộ)
        last_check = cache.get("license_last_check", 0)
        cached_key = cache.get("active_key")
        
        now = time.time()
        
        # Nếu Key đang nhập khớp với Key đã xác thực thành công VÀ chưa quá 24h
        if cached_key == key and (now - float(last_check)) < CACHE_DURATION:
            return True

        # --- 2. GỌI API THỰC TẾ ---
        try:
            res = requests.post(
                f"{self.base_url}/api/key/active",
                json={"key": key, "machine_id": machine_id},
                timeout=15,
            )
            
            if res.status_code == 200:
                is_valid = res.json().get("valid", False)
                
                # Nếu Key hợp lệ -> Lưu vào cache để lần sau skip gọi API
                if is_valid:
                    cache.set("active_key", key)
                    cache.set("license_last_check", now)
                return is_valid
                
            elif res.status_code == 403:
                # Key bị khóa hoặc sai machine_id
                return False
                
            res.raise_for_status()
            
        except requests.exceptions.ConnectionError:
            logger.error("Không thể kết nối mạng để check key.")
        except Exception as e:
            logger.error(f"Lỗi kiểm tra key qua API: {e}")

        # Trường hợp lỗi network hoặc server sập: 
        # Nếu trước đó đã từng valid (có trong cache) thì có thể cho qua tạm thời nếu bạn muốn,
        # nhưng ở đây ta chọn return False để đảm bảo bảo mật.
        return False
