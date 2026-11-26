"""
Step 6: Get Processed Photo URLs (Local).

Fetches the list of processed photos for a photoshoot,
cleans URLs by removing query parameters.
"""

import logging
from typing import List, Optional, Callable, Union
from urllib.parse import urlparse

import requests

from core.http_client import HttpClient
from core.logger import log

logger = logging.getLogger(__name__)


def _extract_watermarked_s3_key(
    client_or_photo_id: Union[HttpClient, int, str],
    photo_id_or_client: Optional[Union[HttpClient, int, str]] = None,
    *,
    client: Optional[HttpClient] = None,
    photo_id: Optional[Union[int, str]] = None,
    base_url: Optional[str] = None,
) -> str:
    """
    Generate adjustment presigned URL to get S3 key for a photo,
    and upload the image bytes to S3 presigned URL so that finalize_adjustment succeeds.

    Supports:
        _extract_watermarked_s3_key(client, photo_id)
        _extract_watermarked_s3_key(photo_id, client)
        _extract_watermarked_s3_key(photo_id)
        _extract_watermarked_s3_key(client, photo_id, base_url=base_url)
    """
    actual_client: Optional[HttpClient] = client
    actual_photo_id: Optional[Union[int, str]] = photo_id

    if isinstance(client_or_photo_id, HttpClient):
        actual_client = client_or_photo_id
        if photo_id_or_client is not None and not isinstance(photo_id_or_client, HttpClient):
            actual_photo_id = photo_id_or_client
    elif client_or_photo_id is not None:
        actual_photo_id = client_or_photo_id
        if isinstance(photo_id_or_client, HttpClient):
            actual_client = photo_id_or_client

    if actual_client is None:
        actual_client = HttpClient()

    if actual_photo_id is None:
        raise ValueError("photo_id không được để trống")

    payload = {
        "photo_id": str(actual_photo_id),
        "add_clouds": False,
    }

    try:
        response = actual_client.post(
            "/api/proxy/photos/generate-adjustment-presigned-url",
            json_data=payload,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        status_code = getattr(getattr(e, "response", None), "status_code", None)
        if status_code is not None:
            raise ValueError(f"HTTP {status_code}") from e
        raise ValueError(f"Lỗi generate adjustment presigned url: {e}") from e

    if not isinstance(data, dict) or not data.get("success") or not data.get("s3_key"):
        msg = data.get("message") if isinstance(data, dict) else None
        raise ValueError(msg or "Không tìm thấy watermarked s3_key")

    s3_key = data["s3_key"]
    presigned_url = data.get("presigned_url")

    # Upload image bytes to presigned_url on S3
    if presigned_url:
        try:
            current_base_url = base_url
            if not current_base_url:
                adj_resp = actual_client.get(f"/api/proxy/photos/{actual_photo_id}/adjustments")
                adj_resp.raise_for_status()
                adj_data = adj_resp.json()
                current_base_url = adj_data.get("base_url") if isinstance(adj_data, dict) else None

            if current_base_url:
                wm_resp = requests.get(current_base_url, timeout=30)
                wm_resp.raise_for_status()
                put_resp = requests.put(
                    presigned_url,
                    data=wm_resp.content,
                    headers={"Content-Type": "image/jpeg"},
                    timeout=60,
                )
                put_resp.raise_for_status()
        except Exception as e:
            logger.warning(f"Không thể upload ảnh lên presigned S3 cho photo {actual_photo_id}: {e}")

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

    try:
        response = client.post("/api/proxy/photos/finalize-adjustment", json_data=payload)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        status_code = getattr(getattr(e, "response", None), "status_code", None)
        if status_code is not None:
            raise ValueError(f"HTTP {status_code}") from e
        raise ValueError("Lỗi remove watermark") from e

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
        for attempt in range(5):
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
                    s3_key = _extract_watermarked_s3_key(client, photo_id, base_url=base_url)
                    urls.append(remove_watermark(client, photo_id, s3_key))
                # elif "/full/" in parsed_path:
                #     urls.append(base_url)
                else:
                    urls.append(base_url)
                break  # Success, exit retry loop
            except Exception as e:
                if attempt == 4:
                    _log("ERROR", f"Lỗi 6.4 {e}")
                    return []
                else:
                     _log("WARNING", f"retry {attempt + 1}/5")
    
    _log("INFO", f"Tìm thấy {len(urls)} ảnh đã xử lý")
    return urls
