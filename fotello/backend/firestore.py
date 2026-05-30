from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .client import LogFn, json_request, open_checked, print_system_exception, retry
from .constants import FIRESTORE_URL


def firestore_get(doc_path: str, access_token: str) -> dict[str, Any]:
    url = f"{FIRESTORE_URL}/{doc_path}"

    def _do() -> dict[str, Any]:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
        return json_request(req, 15)

    return retry(_do)


def firestore_patch(
    doc_path: str,
    fields: dict[str, Any],
    access_token: str,
    mask: list[str] | None = None,
    log: LogFn = None,
) -> dict[str, Any]:
    mask = mask or list(fields)
    mask_str = "&".join(f"updateMask.fieldPaths={urllib.parse.quote(m)}" for m in mask)
    url = f"{FIRESTORE_URL}/{doc_path}?{mask_str}"
    body = json.dumps({"fields": fields}).encode()

    def _do() -> dict[str, Any]:
        req = urllib.request.Request(
            url,
            data=body,
            method="PATCH",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
        return json_request(req, 20)

    try:
        return retry(_do)
    except urllib.error.HTTPError as exc:
        print_system_exception(f"firestore.firestore_patch doc_path={doc_path}", exc)
        if log:
            log(f"PATCH Error {exc.code}: {exc.read().decode(errors='replace')}", "error")
        raise


def firestore_run_query(
    access_token: str,
    structured_query: dict[str, Any],
    log: LogFn = None,
) -> list[dict[str, Any]]:
    url = f"{FIRESTORE_URL}:runQuery"
    body = json.dumps({"structuredQuery": structured_query}).encode()

    def _do() -> list[dict[str, Any]]:
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
        rows = json_request(req, 30)
        return rows if isinstance(rows, list) else []

    try:
        return retry(_do)
    except Exception as exc:
        print_system_exception("firestore.firestore_run_query", exc)
        if log:
            log(f"Query Error {exc}", "error")
        raise


def storage_download(gs_uri: str, access_token: str) -> bytes:
    parts = gs_uri.replace("gs://", "").split("/", 1)
    bucket = parts[0]
    obj_path = urllib.parse.quote(parts[1], safe="")
    url = f"https://firebasestorage.googleapis.com/v0/b/{bucket}/o/{obj_path}?alt=media"

    def _do() -> bytes:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
        with open_checked(req, 60) as resp:
            return resp.read()

    return retry(_do)
