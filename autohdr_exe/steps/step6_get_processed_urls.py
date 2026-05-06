"""
Step 6: Get Processed Photo URLs (Local).

Fetches the list of processed photos for a photoshoot,
cleans URLs by removing query parameters.
"""

import logging
from typing import List, Optional, Callable
from urllib.parse import urlparse, urlunparse

from core.http_client import HttpClient
from core.logger import log

logger = logging.getLogger(__name__)


def _clean_url(url: str) -> str:
    """Remove query parameters from a URL."""
    parsed = urlparse(url)
    clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    return clean


def execute(
    client: HttpClient,
    photoshoot_id: int,
    unique_str: str,
    input_filenames: List[str],
    page_size: int = 10,
    on_log: Optional[Callable] = None,
) -> List[str]:
    """
    Execute Step 6: Get processed photo URLs.

    Returns list of cleaned processed photo URLs.
    """
    step = 6
    
    def _log(level: str, msg: str):
        """Log through both the module logger and the pipeline callback."""
        log(logger, level, step, msg)
        if on_log:
            try:
                on_log(level, step, msg)
            except Exception:
                pass

    url = f"/api/proxy/photoshoots/{photoshoot_id}/processed_photos?page=1&page_size={page_size}"

    try:
        response = client.get(url)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        _log("ERROR", f"Lỗi lấy processed photos: {e}")
        return []

    if not isinstance(data, list):
        _log("ERROR", f"Response format không đúng: {type(data)}")
        return []

    if len(data) == 0:
        _log("ERROR", f"Không có processed photos")
        return []

    # Extract and clean URLs
    raw_urls = [item.get("url", "") for item in data if item.get("url")]
    # cleaned_urls = [_clean_url(u) for u in raw_urls]

    _log("INFO", f"Tìm thấy {len(raw_urls)} ảnh đã xử lý")
    return raw_urls
