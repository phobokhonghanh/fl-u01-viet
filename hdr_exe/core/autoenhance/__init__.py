"""Autoenhance Engine Public Package.

Provides clean public API for Autoenhance v3 service:
- API key management & validation
- Order creation, listing, and inspection
- Order metadata management
- File preparation and upload
- Execution options mapping and triggering
- Enhancement polling
- Batch and selective download
- End-to-end 7-step workflow
- Chrome DevTools Protocol API key extraction
"""
from __future__ import annotations

from core.autoenhance.constants import (
    API_BASE,
    API_KEY_REGEX,
    APP_URL,
    CDP_USER_AGENT,
    DOMAIN,
    ENGINE_DIR,
    META_DIR,
    NATIVE_EXTS,
    PRESET_MAP,
    SETTINGS_URL,
    STORAGE_FILE,
    USER_AGENT,
    WORKFLOW_STEPS,
)
from core.autoenhance.auth import (
    clear_api_key,
    load_api_key,
    save_api_key,
    validate_api_key,
)
from core.autoenhance.metadata import (
    delete_order_metadata,
    load_order_metadata,
    save_order_metadata,
)
from core.autoenhance.orders import (
    create_order,
    get_order_details,
    list_orders,
)
from core.autoenhance.upload import (
    get_upload_s3_info,
    prepare_upload_files,
)
from core.autoenhance.execute import (
    map_options_to_payload,
    trigger_process,
)
from core.autoenhance.polling import (
    poll_order_completion,
)
from core.autoenhance.download import (
    batch_download,
    download_selected_photos,
)
from core.autoenhance.workflow import (
    upload_and_process,
)
from core.autoenhance.cdp import (
    extract_and_save_api_key,
    extract_api_key,
)

__all__ = [
    # Constants
    "API_BASE",
    "APP_URL",
    "DOMAIN",
    "SETTINGS_URL",
    "API_KEY_REGEX",
    "CDP_USER_AGENT",
    "ENGINE_DIR",
    "STORAGE_FILE",
    "META_DIR",
    "NATIVE_EXTS",
    "PRESET_MAP",
    "USER_AGENT",
    "WORKFLOW_STEPS",
    # Auth
    "save_api_key",
    "load_api_key",
    "clear_api_key",
    "validate_api_key",
    # Metadata
    "save_order_metadata",
    "load_order_metadata",
    "delete_order_metadata",
    # Orders
    "create_order",
    "list_orders",
    "get_order_details",
    # Upload & Execute
    "prepare_upload_files",
    "get_upload_s3_info",
    "map_options_to_payload",
    "trigger_process",
    # Polling & Download
    "poll_order_completion",
    "batch_download",
    "download_selected_photos",
    # High-level Workflow
    "upload_and_process",
    # CDP API Key Extraction
    "extract_api_key",
    "extract_and_save_api_key",
]
