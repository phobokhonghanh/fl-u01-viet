"""
Cloudflare KV Storage Adapter for License Keys.
Supports both Cloudflare KV (in worker runtime) and local JSON file fallback.
"""

import asyncio
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _is_kv(env: Any) -> bool:
    return env is not None and hasattr(env, "KEYS_KV") and env.KEYS_KV is not None


class KVStorage:
    """Utility class for Cloudflare KV operations with local file fallback."""

    def __init__(self, env: Any = None):
        self.env = env

    async def get_text(self, key: str) -> Optional[str]:
        """Lấy nội dung text từ KV hoặc file local."""
        if _is_kv(self.env):
            try:
                res = self.env.KEYS_KV.get(key, type="text")
                if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                    res = await res
                return str(res) if res is not None else None
            except Exception as e:
                logger.error(f"KV get failed for '{key}': {e}")
                return None
        else:
            if not os.path.exists(key):
                return None
            try:
                with open(key, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception as e:
                logger.error(f"Local file read failed for '{key}': {e}")
                return None

    async def put_text(self, key: str, value: str) -> bool:
        """Lưu nội dung text vào KV hoặc file local."""
        if _is_kv(self.env):
            try:
                res = self.env.KEYS_KV.put(key, value)
                if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                    await res
                return True
            except Exception as e:
                logger.error(f"KV put failed for '{key}': {e}")
                return False
        else:
            try:
                with open(key, "w", encoding="utf-8") as f:
                    f.write(value)
                return True
            except Exception as e:
                logger.error(f"Local file write failed for '{key}': {e}")
                return False
