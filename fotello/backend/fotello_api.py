from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .client import json_request, open_checked, print_system_exception, request_logger, retry
from .constants import CONTENT_TYPES, EP_CREATE_UPLOAD, FOTELLO_API


def api_post(endpoint: str, body: dict[str, Any], id_token: str) -> dict[str, Any]:
    data = json.dumps(body).encode()

    def _do() -> dict[str, Any]:
        req = urllib.request.Request(
            FOTELLO_API + "/" + endpoint,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "authorization": id_token,
                "Origin": "https://app.fotello.co",
                "Referer": "https://app.fotello.co/",
            },
        )
        return json_request(req, 15)

    try:
        return retry(_do)
    except Exception as exc:
        print_system_exception(f"fotello_api.api_post endpoint={endpoint}", exc)
        logger = request_logger()
        if logger:
            safe_body = json.dumps(body, ensure_ascii=False)[:1200]
            logger(f"POST {FOTELLO_API}/{endpoint} payload={safe_body}", "error")
        raise


def get_content_type(filepath: Path) -> str:
    return CONTENT_TYPES.get(filepath.suffix.lower(), "image/jpeg")


def upload_image_resumable(filepath: Path, id_token: str, team_id: str) -> str:
    filename = filepath.name
    result = api_post(EP_CREATE_UPLOAD, {"filename": filename, "teamId": team_id}, id_token)
    upload_id = result["id"]
    object_name = upload_id + "/" + filename
    content_type = get_content_type(filepath)
    file_size = filepath.stat().st_size
    file_data = filepath.read_bytes()
    start_url = "https://firebasestorage.googleapis.com/v0/b/fotello-uploads/o?name="
    start_url += urllib.parse.quote(object_name, safe="")

    def _start():
        start_body = json.dumps({"contentType": content_type}).encode()
        start_req = urllib.request.Request(
            start_url,
            data=start_body,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(file_size),
                "X-Goog-Upload-Header-Content-Type": content_type,
                "X-Goog-Upload-Protocol": "resumable",
                "Authorization": "Firebase " + id_token,
            },
        )
        return open_checked(start_req, 15)

    resp = retry(_start)
    upload_url = resp.headers.get("x-goog-upload-url") or resp.headers.get("X-Goog-Upload-URL")
    if not upload_url:
        raise RuntimeError("Resumable start didn't return upload URL")

    def _upload():
        upload_req = urllib.request.Request(
            upload_url,
            data=file_data,
            method="POST",
            headers={
                "Content-Type": content_type,
                "X-Goog-Upload-Command": "upload, finalize",
                "X-Goog-Upload-Offset": "0",
            },
        )
        return open_checked(upload_req, 120)

    retry(_upload)
    return upload_id
