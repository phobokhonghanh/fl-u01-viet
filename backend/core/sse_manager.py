"""
SSE Manager - Quản lý pub/sub cho Server-Sent Events.
Dùng để thông báo cho client khi trạng thái đơn hàng thay đổi.
"""

import asyncio
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


class SSEManager:
    """Bộ quản lý đăng ký/hủy đăng ký SSE theo order_id."""

    def __init__(self):
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}

    def subscribe(self, order_id: str) -> asyncio.Queue:
        """Client đăng ký nhận sự kiện cho một đơn hàng."""
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(order_id, []).append(q)
        logger.info(f"SSE: Client đăng ký theo dõi đơn hàng {order_id}")
        return q

    def unsubscribe(self, order_id: str, q: asyncio.Queue):
        """Hủy đăng ký khi client ngắt kết nối."""
        subscribers = self._subscribers.get(order_id, [])
        if q in subscribers:
            subscribers.remove(q)
            logger.info(f"SSE: Client hủy theo dõi đơn hàng {order_id}")

    async def notify(self, order_id: str, data: dict):
        """Gửi sự kiện tới toàn bộ client đang theo dõi đơn hàng."""
        subscribers = self._subscribers.get(order_id, [])
        if subscribers:
            logger.info(f"SSE: Gửi sự kiện tới {len(subscribers)} client của đơn hàng {order_id}: {data}")
        for q in subscribers:
            await q.put(data)


# Singleton instance — dùng chung toàn bộ ứng dụng
sse_manager = SSEManager()
