"""
Step 1: Generate Presigned URLs (Local).

Creates a UUID (unique_str), sends file info to AutoHDR API to get
presigned S3 URLs for uploading.

Adapted from backend: removed _save_input_files (files stay in place locally).
"""

import os
import uuid
import logging
from typing import List, Optional

from core.http_client import HttpClient
from core.logger import log
from models.schemas import PipelineContext, PresignedUrl

logger = logging.getLogger(__name__)


def _generate_unique_str() -> str:
    """Generate a random UUID string."""
    return str(uuid.uuid4())


def _extract_filenames(file_paths: List[str]) -> List[str]:
    """Extract filenames from file paths."""
    return [os.path.basename(fp) for fp in file_paths]


def _build_payload(unique_str: str, filenames: List[str]) -> dict:
    """Build the request payload for presigned URL generation."""
    files = [{"filename": fn, "md5": ""} for fn in filenames]
    return {"unique_str": unique_str, "files": files}


def _parse_presigned_urls(response_data: dict) -> List[PresignedUrl]:
    """Parse presigned URLs from the API response."""
    urls = []
    for item in response_data.get("presignedUrls", []):
        urls.append(PresignedUrl(
            filename=item["filename"],
            url=item["url"],
        ))
    return urls


def execute(client: HttpClient, context: PipelineContext) -> Optional[PipelineContext]:
    """
    Execute Step 1: Generate presigned URLs.

    1. Generates unique_str (UUID)
    2. Extracts filenames from file_paths
    3. Calls API to get presigned URLs
    4. Updates context

    Note: Unlike backend, files are NOT moved — they stay at original paths.
    """
    step = 1

    # Generate unique_str
    context.unique_str = _generate_unique_str()
    log(logger, "INFO", step, f"Generated unique_str: {context.unique_str}")

    # Extract filenames
    context.filenames = _extract_filenames(context.file_paths)
    log(logger, "INFO", step, f"Files: {context.filenames}")

    # Build and send request
    payload = _build_payload(context.unique_str, context.filenames)
    try:
        response = client.post("/api/proxy/generate_presigned_urls", json_data=payload)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        log(logger, "ERROR", step, f"Lỗi tạo presigned URLs: {e}")
        return None

    # Parse response
    context.presigned_urls = _parse_presigned_urls(data)

    if not context.presigned_urls:
        log(logger, "ERROR", step, "Không nhận được presigned URLs từ API")
        return None

    log(logger, "INFO", step, f"Thành công: {len(context.presigned_urls)} presigned URLs")
    return context
