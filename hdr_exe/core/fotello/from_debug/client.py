"""Network and API Client Utilities for Fotello (from_debug)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

MAX_RETRIES = 3


def _retry(fn: Callable[[], Any], max_retries: int = MAX_RETRIES) -> Any:
    delay = 1.0
    for attempt in range(max_retries):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    return None


def download_media_uri(uri: str, access_token: str, timeout: int = 60) -> bytes:
    """Download binary blob from Google Cloud Storage or public URL."""
    if uri.startswith("gs://"):
        parts = uri[5:].split("/", 1)
        bucket = parts[0]
        obj = urllib.parse.quote(parts[1], safe="")
        # Firebase Auth tokens authenticate via Firebase Storage REST API
        primary_url = f"https://firebasestorage.googleapis.com/v0/b/{bucket}/o/{obj}?alt=media"
        fallback_url = f"https://storage.googleapis.com/download/storage/v1/b/{bucket}/o/{obj}?alt=media"
    else:
        primary_url = uri
        fallback_url = None

    headers = {"Authorization": f"Bearer {access_token}"}

    def _do():
        try:
            req = urllib.request.Request(primary_url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if fallback_url and e.code in (401, 403):
                req_fb = urllib.request.Request(fallback_url, headers=headers)
                with urllib.request.urlopen(req_fb, timeout=timeout) as resp:
                    return resp.read()
            raise

    return _retry(_do)
