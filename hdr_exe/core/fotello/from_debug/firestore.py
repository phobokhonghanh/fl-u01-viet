"""Firestore REST API communication for Fotello (from_debug)."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Callable

from core.fotello.from_debug.constants import FIRESTORE_URL
from core.fotello.from_debug.client import _retry


def firestore_get(path: str, access_token: str) -> dict[str, Any]:
    url = f"{FIRESTORE_URL}/{path}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, headers=headers)
    def _do():
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    return _retry(_do)


def firestore_run_query(access_token: str, query: dict[str, Any], log_fn: Callable[[str, str], None] | None = None) -> list[dict[str, Any]]:
    url = f"{FIRESTORE_URL}:runQuery"
    body = json.dumps({"structuredQuery": query}).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    def _do():
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    res = _retry(_do)
    return res if isinstance(res, list) else []


def decode_firestore_value(val: Any) -> Any:
    if not isinstance(val, dict):
        return val
    if "stringValue" in val:
        return val["stringValue"]
    if "integerValue" in val:
        return int(val["integerValue"])
    if "booleanValue" in val:
        return val["booleanValue"]
    if "arrayValue" in val:
        return [decode_firestore_value(v) for v in val["arrayValue"].get("values", [])]
    if "mapValue" in val:
        return {k: decode_firestore_value(v) for k, v in val["mapValue"].get("fields", {}).items()}
    return {k: decode_firestore_value(v) for k, v in val.items()}
