"""
Constants for AutoHDR Engine.
Endpoints, base URLs, timeouts, and storage configurations.
"""
import os

# Base URL (hỗ trợ override qua biến môi trường)
API_BASE: str = os.getenv("AUTOHDR_BASE_URL", "https://old.autohdr.com").rstrip("/")

# Endpoints
ENDPOINT_APP_USER: str = "/api/rpc/auth/appUser"
ENDPOINT_PRESIGNED_URLS: str = "/api/proxy/generate_presigned_urls"
ENDPOINT_FINALIZE_UPLOAD: str = "/api/proxy/finalize_upload"
ENDPOINT_ASSOCIATE_RUN: str = "/api/proxy/associate_and_run"
ENDPOINT_PHOTOSHOOTS: str = "/api/proxy/photoshoots"
ENDPOINT_FINALIZE_ADJUSTMENT: str = "/api/proxy/photos/finalize-adjustment"

# Storage filenames
STORAGE_FILENAME: str = "autohdr.json"

# Network defaults
DEFAULT_USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT: tuple[int, int] = (15, 120)

