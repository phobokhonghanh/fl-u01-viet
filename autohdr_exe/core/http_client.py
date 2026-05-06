"""
HTTP client for direct communication with AutoHDR API (autohdr.com).

Features:
- Proxy support (HTTP/HTTPS with auth)
- Connection pooling (reuse TCP connections)
- Default timeouts (prevent hanging forever)
- Streaming upload (low RAM usage for large files)
"""

import os
import logging
from typing import Optional, Tuple, IO

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Default timeout: (connect, read) in seconds
DEFAULT_TIMEOUT = (15, 120)


class HttpClient:
    """
    Shared HTTP client wrapping requests.Session.
    Connects directly to autohdr.com (not through Railway).
    """

    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) "
        "Gecko/20100101 Firefox/148.0"
    )

    def __init__(self, base_url: Optional[str] = None, cookie: str = "", user_agent: Optional[str] = None):
        if not base_url:
            base_url = os.getenv("AUTOHDR_BASE_URL", "https://www.autohdr.com")
        self.base_url = base_url.rstrip("/")
        self.cookie = cookie
        self.user_agent = user_agent or self.DEFAULT_USER_AGENT

        self.session = requests.Session()
        self.session.headers.update(self._get_default_headers())

        # Connection pooling — reuse TCP connections across requests
        adapter = HTTPAdapter(
            pool_connections=10,
            pool_maxsize=10,
            max_retries=Retry(total=0),  # We handle retries ourselves
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _get_default_headers(self) -> dict:
        """Default headers for AutoHDR API calls."""
        return {
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Referer": f"{self.base_url}/",
            "Origin": self.base_url,
            "Connection": "keep-alive",
            "Content-Type": "application/json",
            "Cookie": self.cookie,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Priority": "u=4",
            "TE": "trailers",
        }

    def update_cookie(self, cookie: str):
        """Update the cookie for this session."""
        self.cookie = cookie
        self.session.headers["Cookie"] = cookie

    # ------------------------------------------------------------------
    # Proxy management
    # ------------------------------------------------------------------

    def set_proxy(self, ip: str, port: str, user: str = "", password: str = ""):
        """
        Configure proxy for all requests.
        Supports HTTP/HTTPS proxy with optional authentication.
        """
        if user and password:
            proxy_url = f"http://{user}:{password}@{ip}:{port}"
        else:
            proxy_url = f"http://{ip}:{port}"

        self.session.proxies = {
            "http": proxy_url,
            "https": proxy_url,
        }
        logger.info(f"Proxy configured: {ip}:{port}")

    def clear_proxy(self):
        """Remove proxy configuration."""
        self.session.proxies = {}
        logger.info("Proxy cleared — using direct connection")

    @staticmethod
    def validate_proxy(ip: str, port: str, user: str = "", password: str = "") -> Tuple[bool, str]:
        """
        Test if a proxy is reachable and functional.
        Returns (success, message).
        """
        if not ip or not port:
            return False, "IP và Port không được để trống"

        try:
            port_int = int(port)
            if not (1 <= port_int <= 65535):
                return False, "Port phải trong khoảng 1-65535"
        except ValueError:
            return False, "Port phải là số"

        if user and password:
            proxy_url = f"http://{user}:{password}@{ip}:{port}"
        else:
            proxy_url = f"http://{ip}:{port}"

        proxies = {"http": proxy_url, "https": proxy_url}

        try:
            response = requests.get(
                "https://httpbin.org/ip",
                proxies=proxies,
                timeout=(10, 10),
            )
            if response.status_code == 200:
                data = response.json()
                origin_ip = data.get("origin", "unknown")
                return True, f"Proxy OK: {origin_ip}"
            return False, f"Proxy lỗi HTTP {response.status_code}"
        except requests.exceptions.ProxyError:
            return False, "Proxy authentication failed"
        except requests.exceptions.ConnectTimeout:
            return False, "Proxy timeout"
        except Exception as e:
            return False, f"Lỗi: {str(e)[:60]}"

    # ------------------------------------------------------------------
    # HTTP methods (with default timeout)
    # ------------------------------------------------------------------

    def post(self, url: str, json_data: Optional[dict] = None, **kwargs) -> requests.Response:
        """Send a POST request."""
        full_url = self._build_url(url)
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
        return self.session.post(full_url, json=json_data, **kwargs)

    def get(self, url: str, params: Optional[dict] = None, **kwargs) -> requests.Response:
        """Send a GET request."""
        full_url = self._build_url(url)
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
        return self.session.get(full_url, params=params, **kwargs)

    def put_binary(self, url: str, data: bytes, headers: Optional[dict] = None) -> requests.Response:
        """Send a PUT request with binary data (for S3 upload)."""
        upload_headers = headers or self.get_s3_upload_headers()
        return self.session.put(url, data=data, headers=upload_headers, timeout=DEFAULT_TIMEOUT)

    def put_stream(self, url: str, file_obj: IO, file_size: int, headers: Optional[dict] = None) -> requests.Response:
        """
        Send a PUT request with streaming file data (low RAM usage).
        The file_obj is streamed directly — NOT loaded entirely into memory.
        """
        upload_headers = headers or self.get_s3_upload_headers()
        upload_headers["Content-Length"] = str(file_size)
        return self.session.put(url, data=file_obj, headers=upload_headers, timeout=(15, 300))

    def get_s3_upload_headers(self) -> dict:
        """Headers specific to S3 file upload (step2)."""
        return {
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Content-Type": "application/octet-stream",
            "x-amz-acl": "private",
            "Origin": self.base_url,
            "Connection": "keep-alive",
            "Referer": f"{self.base_url}/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
        }

    def close(self):
        """Close the underlying session and release connections."""
        try:
            self.session.close()
        except Exception:
            pass

    def _build_url(self, url: str) -> str:
        """Build full URL from a path or return as-is if already absolute."""
        if url.startswith("http://") or url.startswith("https://"):
            return url
        base = self.base_url.rstrip("/")
        path = url if url.startswith("/") else f"/{url}"
        return f"{base}{path}"
