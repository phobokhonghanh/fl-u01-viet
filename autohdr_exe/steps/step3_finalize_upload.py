"""
Step 3: Finalize Upload (Local).

Notifies AutoHDR API that all files have been uploaded for the given unique_str.
"""

import logging
from core.http_client import HttpClient
from core.logger import log

logger = logging.getLogger(__name__)


def execute(client: HttpClient, unique_str: str) -> bool:
    """
    Execute Step 3: Finalize the upload.
    
    Returns True if finalization succeeded, False otherwise.
    """
    step = 3
    payload = {"unique_str": unique_str}

    try:
        response = client.post("/api/proxy/finalize_upload", json_data=payload)

        if response.status_code != 200:
            log(logger, "ERROR", step, f"HTTP {response.status_code}: {response.text}")
            return False

        data = response.json()
    except Exception as e:
        log(logger, "ERROR", step, f"Finalize upload failed: {e}")
        return False

    info = data.get("info", "")
    if "successfully" in info:
        log(logger, "INFO", step, "Finalize upload thành công")
        return True
    else:
        log(logger, "ERROR", step, f"Finalize không thành công: {data}")
        return False
