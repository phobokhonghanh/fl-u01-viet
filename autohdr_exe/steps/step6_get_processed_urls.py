"""
Step 6: Get Processed Photo URLs (Local).

Fetches the list of processed photos for a photoshoot,
cleans URLs by removing query parameters.
"""

import logging
from typing import List, Optional, Callable
from urllib.parse import urlparse

from core.http_client import HttpClient
from core.logger import log

logger = logging.getLogger(__name__)


def _extract_watermarked_s3_key(base_url: str) -> str:
    """Extract the S3 key from a CloudFront watermarked URL."""
    path = urlparse(base_url).path
    marker = "/watermarked/"

    if marker not in path:
        raise ValueError("Không có watermarked")

    s3_key = path.split(marker, 1)[1].lstrip("/")
    if not s3_key:
        raise ValueError("Không tìm thấy watermarked")

    return s3_key


def remove_watermark(client: HttpClient, photo_id: int, s3_key: str) -> str:
    """
    Finalize a watermarked photo adjustment and return the full image URL.
    """
    payload = {
        "photo_id": str(photo_id),
        "add_clouds": False,
        "s3_key": s3_key,
        "preserve_photo": True,
    }

    response = client.post("/api/proxy/photos/finalize-adjustment", json_data=payload)
    
    response.raise_for_status()
    data = response.json()

    if not isinstance(data, dict) or not data.get("success") or not data.get("url"):
        raise ValueError("Lỗi remove watermark")

    return data["url"]


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
        _log("ERROR", f"Lỗi 6.1")
        # Lỗi lấy processed photos: {e} 
        return []

    if not isinstance(data, list):
        _log("ERROR", f"Lỗi 6.2")
        # Response format không đúng: {type(data)}
        return []

    if len(data) == 0:
        _log("ERROR", f"Lỗi 6.3")
        # Không có processed photos
        return []

    # Extract and clean URLs
    ids = [item.get("id", "") for item in data if item.get("id")]

    urls = []
    
    for photo_id in ids:
        for attempt in range(10):
            try:
                url = f"/api/proxy/photos/{photo_id}/adjustments"
                response = client.get(url)
                response.raise_for_status()
                adjustments = response.json()
                base_url = adjustments.get("base_url") if isinstance(adjustments, dict) else None
                if not base_url:
                    _log("ERROR", f"Lỗi 6.3.1 khong tim thay base_url")
                    raise ValueError("Lỗi 6.3.1")

                parsed_path = urlparse(base_url).path
                if "/watermarked/" in parsed_path:
                    s3_key = _extract_watermarked_s3_key(base_url)
                    urls.append(remove_watermark(client, photo_id, s3_key))
                # elif "/full/" in parsed_path:
                #     urls.append(base_url)
                else:
                    urls.append(base_url)
                break  # Success, exit retry loop
            except Exception as e:
                if attempt == 9:
                    _log("ERROR", f"Lỗi 6.4")
                    return []
                else:
                     _log("WARNING", f"retry {attempt + 1}/10")
    
    _log("INFO", f"Tìm thấy {len(urls)} ảnh đã xử lý")
    return urls
