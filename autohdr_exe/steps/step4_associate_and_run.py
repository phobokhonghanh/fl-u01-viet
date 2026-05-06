"""
Step 4: Associate and Run Processing (Local).

Sends user info and unique_str to AutoHDR API to trigger HDR processing.
"""

import logging
from typing import Optional
from core.http_client import HttpClient
from core.logger import log

logger = logging.getLogger(__name__)


def _build_payload(
    unique_str: str,
    email: str,
    firstname: str,
    lastname: str,
    address: str,
    files_count: int,
    indoor_model_id: int = 3,
    outdoor_model_id: Optional[int] = None,
) -> dict:
    """Build the request payload for associate-and-run."""
    return {
        "unique_str": unique_str,
        "email": email,
        "firstname": firstname,
        "lastname": lastname,
        "address": address,
        "spoofId": None,
        "smartlook_url": None,
        "indoor_model_id": indoor_model_id,
        "outdoor_model_id": outdoor_model_id,
        "files_count": files_count,
        "grass_replacement": False,
        "perspective_correction": False,
        "special_attention": False,
        "declutter": False,
        "photoshoot_id": None,
    }


def execute(
    client: HttpClient,
    unique_str: str,
    email: str,
    firstname: str,
    lastname: str,
    address: str,
    files_count: int,
    indoor_model_id: int = 3,
    outdoor_model_id: Optional[int] = None,
) -> bool:
    """
    Execute Step 4: Associate files with user and trigger processing.

    Returns True if response.success is True, False otherwise.
    """
    step = 4

    payload = _build_payload(
        unique_str, email, firstname, lastname, address, files_count, indoor_model_id, outdoor_model_id
    )

    try:
        response = client.post("/api/inference/associate-and-run", json_data=payload)

        if response.status_code != 200:
            log(logger, "ERROR", step, f"HTTP {response.status_code}: {response.text}")
            return False

        data = response.json()
    except Exception as e:
        log(logger, "ERROR", step, f"Associate-and-run failed: {e}")
        return False

    success = data.get("success", False)

    if success:
        log(logger, "INFO", step, "Kích hoạt xử lý HDR thành công")
    else:
        log(logger, "ERROR", step, f"Kích hoạt thất bại. Response: {data}")

    return success
