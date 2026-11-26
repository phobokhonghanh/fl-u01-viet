"""
Constants for AutoHDR v2 (Photoshoot Resolver & Downloader).
Độc lập hoàn toàn với AutoHDR v1. Không lưu server_url tĩnh tại client.
"""

# Endpoints
V2_ENDPOINT_RESOLVE: str = "/api/photoshoot/resolve"

# Context / Credential / Preference Keys
V2_CRED_RESOLVER_KEY: str = "resolver_key"
V2_CRED_HWID: str = "hwid"
V2_CRED_SERVER_URL: str = "server_url"
V2_PREF_PHOTOSHOOT_URL: str = "photoshoot_url"
V2_EXTRA_DOWNLOAD_MAP: str = "v2_download_map"
V2_EXTRA_UUID: str = "uuid"
V2_EXTRA_TITLE: str = "title"
V2_EXTRA_FOLDER: str = "folder"
V2_EXTRA_PHOTOSHOOT_ID: str = "photoshoot_id"

# Resolver Request / Response Schema Keys
V2_REQ_KEY: str = "key"
V2_REQ_HWID: str = "hwid"
V2_REQ_URL: str = "url"
V2_RESP_SUCCESS: str = "success"
V2_RESP_ERROR: str = "error"
V2_RESP_UUID: str = "uuid"
V2_RESP_TITLE: str = "title"
V2_RESP_FOLDER: str = "folder"
V2_RESP_ITEMS: str = "items"
V2_RESP_PHOTOSHOOT_ID: str = "photoshoot_id"
V2_ITEM_FILENAME: str = "filename"
V2_ITEM_URLS: str = "urls"

# Execution Steps & Progress Milestones
V2_STEP_PREPARE: str = "prepare"
V2_STEP_RESOLVE: str = "resolve"
V2_STEP_DOWNLOAD: str = "download"
V2_PROGRESS_PREPARE: int = 5
V2_PROGRESS_RESOLVE: int = 15
V2_PROGRESS_DOWNLOAD_START: int = 30
V2_PROGRESS_COMPLETE: int = 100

# Network & Download Defaults
V2_USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
V2_RESOLVE_TIMEOUT: int = 150
V2_DOWNLOAD_TIMEOUT: int = 25
V2_CHUNK_SIZE: int = 65536
V2_DEFAULT_DOWNLOAD_WORKERS: int = 6
V2_MAX_DOWNLOAD_WORKERS: int = 32
V2_VERIFY_SSL: bool = False
V2_DEFAULT_URL_SCHEME: str = "http://"

# File & Folder Defaults
V2_DEFAULT_FOLDER_NAME: str = "Photoshoot"
V2_DEFAULT_IMAGE_NAME_PREFIX: str = "photo"
V2_DEFAULT_IMAGE_EXT: str = ".jpg"
V2_PART_SUFFIX: str = ".part"
V2_ALLOWED_IMAGE_EXTENSIONS: frozenset[str] = frozenset({
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".heic",
    ".dng", ".raw", ".arw", ".cr2", ".cr3", ".nef",
})
