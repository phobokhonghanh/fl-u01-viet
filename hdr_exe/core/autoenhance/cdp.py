"""Autoenhance CDP API Key Extraction.

Extracts the Autoenhance API key directly from an authenticated Chrome session
using session cookies and the subscription settings page.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

import requests

from core.autoenhance.auth import save_api_key, validate_api_key
from core.autoenhance.constants import (
    API_KEY_REGEX,
    APP_URL,
    CDP_USER_AGENT,
    DOMAIN,
    SETTINGS_URL,
)
from core.shared.cdp import (
    CDPClient,
    call,
    eval_js,
    find_or_open_tab,
    get_cookies,
    launch_chrome,
    wait_for_debugging,
)

logger = logging.getLogger(__name__)


def extract_api_key(
    tab: dict,
    timeout: float = 10.0,
    log_fn: Callable[[str], None] | None = None,
) -> str | None:
    """Extract Autoenhance API key using DOM unmasking or session cookies."""
    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)
        else:
            logger.info(msg)

    # 1. Try unmasking #api-key input in DOM directly if present
    try:
        js_unmask = """
        (() => {
            const input = document.getElementById('api-key');
            if (!input) return null;
            if (input.value && !input.value.includes('*') && input.value.length >= 32) {
                return input.value;
            }
            const btn = input.nextElementSibling;
            if (btn) btn.click();
            return input.value;
        })()
        """
        val = eval_js(tab, js_unmask)
        if val and isinstance(val, str) and "*" not in val and len(val) >= 32:
            _log(f"[Autoenhance][CDP] Extracted unmasked API key from DOM: {val[:8]}...")
            return val
    except Exception:
        pass

    # 2. Try extracting directly from current tab DOM if already loaded in browser
    try:
        tab_html = eval_js(tab, "document.documentElement.outerHTML")
        if tab_html and isinstance(tab_html, str):
            match = API_KEY_REGEX.search(tab_html)
            if match:
                key = match.group(1)
                _log(f"[Autoenhance][CDP] Extracted API key from tab DOM: {key[:8]}...")
                return key
    except Exception:
        pass

    # 3. Extract via session cookies and HTTP request
    try:
        cookies = get_cookies(tab, domain=DOMAIN, timeout=timeout)
        if not cookies:
            _log("[Autoenhance][CDP] No autoenhance.ai cookies found")
            return None

        cookie_str = "; ".join(
            f"{c['name']}={c['value']}"
            for c in cookies
            if "name" in c and "value" in c
        )
        _log(f"[Autoenhance][CDP] Found {len(cookies)} cookies, fetching settings page...")

        headers = {
            "Cookie": cookie_str,
            "User-Agent": CDP_USER_AGENT,
        }
        res = requests.get(SETTINGS_URL, headers=headers, timeout=timeout)
        if res.status_code != 200:
            _log(f"[Autoenhance][CDP] Settings page returned {res.status_code}")
            return None

        match = API_KEY_REGEX.search(res.text)
        if match:
            key = match.group(1)
            _log(f"[Autoenhance][CDP] Extracted API key: {key[:8]}...")
            return key

        _log("[Autoenhance][CDP] API key not found in settings page HTML")
        return None

    except Exception as e:
        _log(f"[Autoenhance][CDP] Error: {e}")
        return None


def extract_and_save_api_key(
    port: int = 9222,
    launch_browser: bool = True,
    timeout: float = 60.0,
    poll_interval: float = 1.0,
    auto_save: bool = True,
    validate: bool = True,
    log_fn: Callable[[str], None] | None = None,
    stop_event: Any | None = None,
) -> tuple[bool, str | None]:
    """Orchestrate Chrome launch, DevTools connection, API key extraction, and saving.

    Returns:
        tuple[bool, str | None]: (success, api_key_or_error_message)
    """
    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)
        else:
            logger.info(msg)

    client = CDPClient(port=port)

    # 1. Ensure Chrome is running and responsive
    if not client.is_alive():
        if not launch_browser:
            _log("[Autoenhance][CDP] Chrome không đang chạy và launch_browser=False")
            return False, "Chrome không đang chạy trên cổng debug"

        _log("[Autoenhance][CDP] Đang khởi chạy Chrome với Remote Debugging...")
        try:
            launch_chrome(port=port, url=APP_URL)
        except Exception as e:
            err = f"Không khởi chạy được Chrome: {e}"
            _log(f"[Autoenhance][CDP] {err}")
            return False, err

        _log("[Autoenhance][CDP] Đang chờ cổng DevTools sẵn sàng...")
        if not wait_for_debugging(port=port, timeout=30.0, interval=0.5, stop_event=stop_event):
            err = f"Chrome không mở cổng Remote Debugging {port}"
            _log(f"[Autoenhance][CDP] {err}")
            return False, err

    # 2. Find or open Autoenhance tab
    tab = find_or_open_tab(
        domain_or_pattern=DOMAIN,
        open_url=SETTINGS_URL,
        port=port,
        autocreate=True,
    )
    if not tab:
        err = "Không tìm thấy hoặc mở được tab Autoenhance"
        _log(f"[Autoenhance][CDP] {err}")
        return False, err

    _log("[Autoenhance][CDP] Đang liên kết Chrome qua DevTools. Vui lòng đăng nhập Autoenhance...")

    # 3. Poll for api_key
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
            _log("[Autoenhance][CDP] Luồng liên kết bị dừng bởi người dùng.")
            return False, "Dừng luồng liên kết"

        # Refresh tab list in case tab navigated or reopened
        current_tab = find_or_open_tab(
            domain_or_pattern=DOMAIN,
            open_url=None,
            port=port,
            autocreate=False,
        ) or tab

        api_key = extract_api_key(current_tab, timeout=5.0, log_fn=log_fn)
        if not api_key:
            # If cookies exist (logged in) but tab is not on account settings, navigate there
            try:
                cookies = get_cookies(current_tab, domain=DOMAIN, timeout=1.0)
                if cookies:
                    curr_url = eval_js(current_tab, "window.location.href") or ""
                    if "tab=account" not in curr_url:
                        _log("[Autoenhance][CDP] Đã phát hiện phiên đăng nhập, chuyển tới trang Account Settings...")
                        call(current_tab, "Page.navigate", {"url": SETTINGS_URL})
                        time.sleep(2.0)
                        api_key = extract_api_key(current_tab, timeout=5.0, log_fn=log_fn)
            except Exception:
                pass

        if api_key:
            if validate:
                _log("[Autoenhance][CDP] Đang xác thực API key...")
                if not validate_api_key(api_key):
                    _log("[Autoenhance][CDP] API key trích xuất không hợp lệ qua API!")
                    return False, "API key không hợp lệ khi kiểm tra với máy chủ Autoenhance"

            if auto_save:
                save_api_key(api_key)
                _log("[Autoenhance][CDP] Đã lưu API key thành công!")

            return True, api_key

        time.sleep(poll_interval)

    err = f"Hết thời gian chờ đăng nhập Autoenhance ({int(timeout)}s)"
    _log(f"[Autoenhance][CDP] {err}")
    return False, err
