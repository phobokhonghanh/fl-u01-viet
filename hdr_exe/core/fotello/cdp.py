"""Chrome DevTools Protocol (CDP) Token Extractor for Fotello."""
from __future__ import annotations

import logging
import time
from urllib.parse import urlparse
from core.fotello.config import load_fotello_config
from typing import Any, Callable

from core.fotello.auth import save_tokens, validate_session
from core.shared.cdp import (
    CDPClient,
    eval_js,
    find_or_open_tab,
    launch_chrome,
    wait_for_debugging,
)

logger = logging.getLogger(__name__)



def extract_firebase_tokens_from_tab(tab: dict) -> dict[str, Any] | None:
    """Extract Firebase auth credentials from localStorage via JS execution."""
    js_script = """
    (() => {
        function pick(v) {
            try {
                const data = typeof v === 'string' ? JSON.parse(v) : v;
                const token = data?.stsTokenManager || data?.value?.stsTokenManager;
                if (token?.refreshToken) return {
                    refresh_token: token.refreshToken,
                    id_token: token.accessToken || '',
                    access_token: token.accessToken || '',
                    uid: data.uid || data?.value?.uid || '',
                    email: data.email || data?.value?.email || ''
                };
            } catch(e) {}
            return null;
        }
        try {
            for (let i = 0; i < localStorage.length; i++) {
                const key = localStorage.key(i);
                if (key && key.startsWith('firebase:authUser')) {
                    const found = pick(localStorage.getItem(key));
                    if (found) return found;
                }
            }
        } catch(e) {}
        return null;
    })()
    """
    res = eval_js(tab, js_script)
    if res and isinstance(res, dict) and res.get("refresh_token"):
        return res
    return None


def login(
    port: int | None = None,
    launch_browser: bool = True,
    timeout: float | None = None,
    poll_interval: float | None = None,
    auto_save: bool = True,
    log_fn: Callable[[str], None] | None = None,
    stop_event: Any | None = None,
) -> tuple[bool, dict[str, Any] | str]:
    """Dang nhap Fotello qua Chrome Remote Debugging (chi loopback 127.0.0.1) va luu token."""
    config = load_fotello_config()
    port = config.browser.port if port is None else port
    timeout = config.browser.login_timeout_seconds if timeout is None else timeout
    poll_interval = config.browser.poll_interval_seconds if poll_interval is None else poll_interval
    app_url = config.endpoints.app_url
    app_domain = urlparse(app_url).hostname

    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)
        else:
            logger.info(msg)

    # Chi ket noi toi loopback
    client = CDPClient(host="127.0.0.1", port=port)
    if not client.is_alive():
        if not launch_browser:
            return False, "Chrome khong dang chay tren cong debug loopback"
        _log("[Fotello][CDP] Dang khoi chay Chrome de dang nhap Fotello...")
        try:
            launch_chrome(port=port, url=app_url)
        except Exception as e:
            return False, f"Khong khoi chay duoc Chrome: {e}"

        if not wait_for_debugging(host="127.0.0.1", port=port, timeout=config.browser.startup_timeout_seconds, interval=config.browser.startup_poll_interval_seconds, stop_event=stop_event):
            return False, f"Chrome khong mo cong Remote Debugging {port}"

    tab = find_or_open_tab(domain_or_pattern=app_domain, open_url=app_url, port=port, autocreate=True)
    if not tab:
        return False, "Khong tim thay hoac mo duoc tab Fotello"

    _log("[Fotello][CDP] Vui long dang nhap tai khoan Fotello tren trinh duyet Chrome...")
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
            return False, "Tac vu dang nhap bi huy boi nguoi dung"

        current_tab = find_or_open_tab(domain_or_pattern=app_domain, open_url=None, port=port, autocreate=False) or tab
        tokens = extract_firebase_tokens_from_tab(current_tab)
        if tokens:
            if auto_save:
                tokens["connected"] = True
                save_tokens(tokens)
                if not validate_session():
                    _log("[Fotello][CDP] Token trich xuat khong the xac thuc phien lam viec.")
                    time.sleep(poll_interval)
                    continue
                _log("[Fotello][CDP] Da trich xuat va luu phien lam viec thanh cong.")

            # Khong tra token nguyen ban de tranh in ra log ngoai
            return True, {
                "connected": True,
                "email": tokens.get("email", ""),
                "uid": tokens.get("uid", ""),
                "tokens_saved": auto_save,
            }

        time.sleep(poll_interval)

    return False, f"Het thoi gian cho dang nhap Fotello ({int(timeout)}s)"
