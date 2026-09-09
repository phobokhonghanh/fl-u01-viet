"""Shared Chrome DevTools Protocol (CDP) utilities.

Engine-agnostic implementation of Chrome discovery, profile management,
process launching, and CDP interaction over HTTP and WebSocket.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    import websocket
except ImportError:
    websocket = None  # type: ignore

from core.shared.config import get_app_dir

logger = logging.getLogger(__name__)

# Standard Chrome paths for various platforms
_WIN_REG_PATHS = [
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
]

_WIN_ENV_TEMPLATES = [
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(Arm)%\Google\Chrome\Application\chrome.exe",
    r"%ProgramW6432%\Google\Chrome\Application\chrome.exe",
]

_MAC_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

_LINUX_BINARIES = [
    "google-chrome",
    "google-chrome-stable",
    "chromium-browser",
    "chromium",
]


def find_chrome() -> str | None:
    """Find installed Google Chrome / Chromium executable across operating systems.

    Checks:
    1. CHROME_PATH environment variable
    2. Windows Registry (on Windows)
    3. Standard installation paths (Windows / macOS)
    4. PATH binaries (Linux / macOS / Windows)
    """
    env_path = os.environ.get("CHROME_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path

    if sys.platform.startswith("win"):
        try:
            import winreg

            for reg_path in _WIN_REG_PATHS:
                for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                    try:
                        with winreg.OpenKey(root, reg_path) as key:
                            val, _ = winreg.QueryValueEx(key, "")
                            if val and os.path.isfile(val):
                                return val
                    except OSError:
                        continue
        except ImportError:
            pass

        for tmpl in _WIN_ENV_TEMPLATES:
            expanded = os.path.expandvars(tmpl)
            if expanded and os.path.isfile(expanded):
                return expanded

        app_dir = get_app_dir()
        portable_candidates = [
            app_dir / "GoogleChromePortable" / "App" / "Chrome-bin" / "chrome.exe",
            Path("C:/GoogleChromePortable/App/Chrome-bin/chrome.exe"),
        ]
        for pc in portable_candidates:
            if pc.is_file():
                return str(pc)

    elif sys.platform == "darwin":
        for p in _MAC_PATHS:
            expanded = Path(os.path.expanduser(p))
            if expanded.is_file():
                return str(expanded)

    candidates = ["chrome.exe", "google-chrome.exe"] if sys.platform.startswith("win") else _LINUX_BINARIES
    for bin_name in candidates:
        found = shutil.which(bin_name)
        if found:
            return found

    return None


def get_chrome_profile_dir(profile_name: str = "default") -> Path:
    """Return dedicated Chrome user-data-dir under the app directory."""
    profile_dir = get_app_dir() / "chrome_profiles" / profile_name
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir


def launch_chrome(
    chrome_path: str | None = None,
    port: int = 9222,
    profile_dir: Path | str | None = None,
    url: str | None = None,
    headless: bool = False,
    extra_args: list[str] | None = None,
) -> subprocess.Popen:
    """Launch Google Chrome with remote debugging enabled."""
    exe = chrome_path or find_chrome()
    if not exe:
        raise FileNotFoundError(
            "Google Chrome executable not found. Please install Chrome or set CHROME_PATH."
        )

    p_dir = Path(profile_dir) if profile_dir else get_chrome_profile_dir()
    p_dir.mkdir(parents=True, exist_ok=True)

    args = [
        exe,
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={p_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if headless:
        args.extend(["--headless=new", "--disable-gpu"])
    if extra_args:
        args.extend(extra_args)
    if url:
        args.append(url)

    creationflags = 0
    if sys.platform.startswith("win"):
        creationflags = 0x00000008  # DETACHED_PROCESS

    return subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )


def request_json(
    path: str,
    method: str = "GET",
    port: int = 9222,
    timeout: float = 5.0,
) -> Any:
    """Make an HTTP request to Chrome's DevTools HTTP endpoint bypassing proxies."""
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        content = resp.read().decode("utf-8")
        if not content.strip():
            return None
        return json.loads(content)


def wait_for_debugging(
    port: int = 9222,
    timeout: float = 30.0,
    interval: float = 0.5,
    stop_event: Any | None = None,
) -> bool:
    """Wait for Chrome's DevTools endpoint to become responsive."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
            return False
        try:
            ver = request_json("/json/version", port=port, timeout=1.0)
            if ver and "Browser" in ver:
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def tabs(port: int = 9222) -> list[dict]:
    """Return all open Chrome page tabs with an active WebSocket debugger URL."""
    try:
        data = request_json("/json", port=port)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [
        t for t in data
        if isinstance(t, dict) and t.get("type") == "page" and t.get("webSocketDebuggerUrl")
    ]


def open_tab(url: str, port: int = 9222) -> dict:
    """Open a new browser tab with the specified URL."""
    quoted = urllib.parse.quote(url, safe=":/?&=%")
    res = request_json(f"/json/new?{quoted}", method="PUT", port=port)
    if isinstance(res, dict):
        return res
    raise RuntimeError(f"Failed to open tab for URL: {url}")


def find_or_open_tab(
    domain_or_pattern: str,
    open_url: str | None = None,
    port: int = 9222,
    autocreate: bool = True,
    timeout: float = 5.0,
) -> dict | None:
    """Find an existing tab containing domain_or_pattern in URL, or open one if requested."""
    open_tabs = tabs(port=port)
    for t in open_tabs:
        if domain_or_pattern.lower() in t.get("url", "").lower():
            return t

    if autocreate and open_url:
        tab = open_tab(open_url, port=port)
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            for t in tabs(port=port):
                if tab.get("id") == t.get("id") or domain_or_pattern.lower() in t.get("url", "").lower():
                    return t
            time.sleep(0.3)
        return tab

    return None


def call(
    tab: dict,
    method: str,
    params: dict | None = None,
    timeout: float = 10.0,
) -> Any:
    """Send a CDP command via WebSocket to a target tab and return result."""
    if websocket is None:
        raise ImportError("The 'websocket-client' package is required for CDP communication.")

    ws_url = tab.get("webSocketDebuggerUrl")
    if not ws_url:
        raise ValueError("Tab Chrome không có websocket debugger URL")

    ws = websocket.create_connection(ws_url, timeout=timeout, suppress_origin=True)
    try:
        req_id = int(time.time() * 1000) % 1000000
        msg = {
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        ws.send(json.dumps(msg))

        start = time.monotonic()
        while time.monotonic() - start < timeout:
            raw = ws.recv()
            if not raw:
                continue
            data = json.loads(raw)
            if data.get("id") == req_id:
                if "error" in data:
                    raise RuntimeError(f"CDP error: {data['error']}")
                return data.get("result")
        raise TimeoutError(f"CDP command {method} timed out after {timeout}s")
    finally:
        ws.close()


def eval_js(
    tab: dict,
    expression: str,
    await_promise: bool = False,
    timeout: float = 10.0,
) -> Any:
    """Evaluate a JavaScript expression in the context of the tab."""
    res = call(
        tab,
        "Runtime.evaluate",
        params={
            "expression": expression,
            "awaitPromise": await_promise,
            "returnByValue": True,
        },
        timeout=timeout,
    )
    if isinstance(res, dict):
        result_obj = res.get("result", {})
        return result_obj.get("value")
    return None


def get_cookies(
    tab: dict,
    domain: str | None = None,
    timeout: float = 10.0,
) -> list[dict]:
    """Fetch all browser cookies via Network.getAllCookies, optionally filtered by domain."""
    res = call(tab, "Network.getAllCookies", timeout=timeout)
    if not isinstance(res, dict):
        return []
    all_cookies = res.get("cookies", [])
    if not domain:
        return all_cookies
    dom_lower = domain.lower()
    return [c for c in all_cookies if dom_lower in c.get("domain", "").lower()]


def get_cookie_header(
    tab: dict,
    domain: str | None = None,
    timeout: float = 10.0,
) -> str:
    """Get HTTP Cookie header string ('name=value; name2=value2') for a domain."""
    cookie_list = get_cookies(tab, domain=domain, timeout=timeout)
    return "; ".join(f"{c['name']}={c['value']}" for c in cookie_list if "name" in c and "value" in c)


class CDPClient:
    """Object-oriented interface for Chrome DevTools Protocol interaction."""

    def __init__(self, port: int = 9222, host: str = "127.0.0.1") -> None:
        self.port = port
        self.host = host

    def is_alive(self) -> bool:
        """Check if Chrome DevTools endpoint is responsive."""
        try:
            ver = request_json("/json/version", port=self.port, timeout=1.0)
            return bool(ver and "Browser" in ver)
        except Exception:
            return False

    def wait_ready(self, timeout: float = 30.0, interval: float = 0.5, stop_event: Any | None = None) -> bool:
        """Wait until DevTools endpoint is ready."""
        return wait_for_debugging(
            port=self.port, timeout=timeout, interval=interval, stop_event=stop_event
        )

    def tabs(self) -> list[dict]:
        """List all open page tabs."""
        return tabs(port=self.port)

    def open_tab(self, url: str) -> dict:
        """Open a new tab with url."""
        return open_tab(url, port=self.port)

    def find_or_open_tab(
        self,
        domain_or_pattern: str,
        open_url: str | None = None,
        autocreate: bool = True,
        timeout: float = 5.0,
    ) -> dict | None:
        """Find or open a tab matching domain_or_pattern."""
        return find_or_open_tab(
            domain_or_pattern=domain_or_pattern,
            open_url=open_url,
            port=self.port,
            autocreate=autocreate,
            timeout=timeout,
        )

    def call(self, tab: dict, method: str, params: dict | None = None, timeout: float = 10.0) -> Any:
        """Call CDP method on target tab."""
        return call(tab, method, params=params, timeout=timeout)

    def eval_js(self, tab: dict, expression: str, await_promise: bool = False, timeout: float = 10.0) -> Any:
        """Evaluate JS in target tab."""
        return eval_js(tab, expression, await_promise=await_promise, timeout=timeout)

    def get_cookies(self, tab: dict, domain: str | None = None, timeout: float = 10.0) -> list[dict]:
        """Get cookies from target tab."""
        return get_cookies(tab, domain=domain, timeout=timeout)

    def get_cookie_header(self, tab: dict, domain: str | None = None, timeout: float = 10.0) -> str:
        """Get formatted Cookie header for domain."""
        return get_cookie_header(tab, domain=domain, timeout=timeout)
