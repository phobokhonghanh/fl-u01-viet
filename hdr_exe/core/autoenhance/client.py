"""Autoenhance HTTP API Client.

Provides resilient GET and POST requests with automated retry on connection error
and custom authorization headers for Autoenhance v3.
"""
from __future__ import annotations

import time
from typing import Any

import requests

from core.autoenhance.constants import (
    API_BASE,
    DEFAULT_RETRY_COUNT,
    DEFAULT_RETRY_DELAY,
    DEFAULT_TIMEOUT,
    USER_AGENT,
)


def _api_request(
    method: str,
    endpoint: str,
    api_key: str,
    session: requests.Session | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Gửi HTTP request tới API Autoenhance v3 với cơ chế retry khi lỗi kết nối."""
    sess = session or requests.Session()
    url = f"{API_BASE}{endpoint}"
    headers = {
        "x-api-key": api_key.strip(),
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    kwargs: dict[str, Any] = {
        "headers": headers,
        "timeout": DEFAULT_TIMEOUT,
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
        kwargs["json"] = data

    http_fn = getattr(sess, method.lower())
    last_err = None
    for attempt in range(DEFAULT_RETRY_COUNT):
        try:
            resp = http_fn(url, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_err = e
            time.sleep(DEFAULT_RETRY_DELAY)
        except requests.exceptions.HTTPError:
            raise

    if last_err:
        raise last_err
    return {}


def _api_get(
    session: requests.Session | None,
    endpoint: str,
    api_key: str,
) -> dict[str, Any]:
    """Gửi HTTP GET tới API Autoenhance v3."""
    return _api_request("GET", endpoint, api_key, session=session)


def _api_post(
    session: requests.Session | None,
    endpoint: str,
    data: dict[str, Any],
    api_key: str,
) -> dict[str, Any]:
    """Gửi HTTP POST tới API Autoenhance v3."""
    return _api_request("POST", endpoint, api_key, session=session, data=data)
