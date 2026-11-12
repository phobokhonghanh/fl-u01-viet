"""
Constants for Autoenhance Engine v3.
Endpoints, base URLs, queries, and defaults.
"""
import os

# Base URL (hỗ trợ override qua biến môi trường)
API_BASE: str = os.getenv("AUTOENHANCE_API_BASE", "https://api.autoenhance.ai/v3").rstrip("/")
APP_URL: str = "https://app.autoenhance.ai"

# Endpoints
ENDPOINT_VALIDATION: str = "/orders/?per_page=1"
ENDPOINT_ORDERS: str = "/orders/"
ENDPOINT_ORDER_DETAIL: str = "/orders/{order_id}"
ENDPOINT_ORDER_BRACKETS: str = "/orders/{order_id}/brackets"
ENDPOINT_IMAGES: str = "/images/"
ENDPOINT_PROCESS: str = "/orders/{order_id}/process"
ENDPOINT_ENHANCED_IMAGE: str = "/images/{image_id}/enhanced"

# Storage filenames
STORAGE_FILENAME: str = "autoenhance.json"

# Network defaults
USER_AGENT: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
DEFAULT_TIMEOUT: int = 30
DEFAULT_POLL_INTERVAL: int = 5
DEFAULT_MAX_WAIT_SECONDS: int = 1800
DOWNLOAD_CHUNK_SIZE: int = 65536
MAX_API_WORKERS: int = 4
MAX_DOWNLOAD_WORKERS: int = 4

# Image queries: Tải ảnh thành phẩm gốc chất lượng cao không watermark (preview=false)
ENHANCED_IMAGE_QUERY: str = "format=png&watermark=false&preview=true&quality=90&scale=1&max_width=8192"


