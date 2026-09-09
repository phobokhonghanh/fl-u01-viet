"""Shared Utilities for HDR Engines.

Contains cross-cutting capabilities used by engines:
- events: StepEvent and StepTracker
- callbacks: safe_call_progress and ProgressAdapter
- workspace: TemporaryWorkspace
- images: format conversion and dimension detection
- config: centralized paths and configuration
- cdp: Chrome DevTools Protocol interaction
"""
from __future__ import annotations

from core.shared.events import StepEvent, StepTracker
from core.shared.callbacks import ProgressAdapter, safe_call_progress
from core.shared.workspace import TemporaryWorkspace
from core.shared.config import (
    get_app_dir,
    get_engine_config,
    get_engine_dir,
    get_metadata_dir,
    get_token_path,
    load_app_config,
)
from core.shared.images import (
    convert_to_jpg,
    get_image_dimensions,
)
from core.shared.cdp import (
    CDPClient,
    call,
    eval_js,
    find_chrome,
    find_or_open_tab,
    get_chrome_profile_dir,
    get_cookie_header,
    get_cookies,
    launch_chrome,
    open_tab,
    request_json,
    tabs,
    wait_for_debugging,
)

__all__ = [
    "StepEvent",
    "StepTracker",
    "ProgressAdapter",
    "safe_call_progress",
    "TemporaryWorkspace",
    "get_app_dir",
    "get_engine_dir",
    "get_token_path",
    "get_metadata_dir",
    "load_app_config",
    "get_engine_config",
    "convert_to_jpg",
    "get_image_dimensions",
    "CDPClient",
    "find_chrome",
    "get_chrome_profile_dir",
    "launch_chrome",
    "wait_for_debugging",
    "request_json",
    "tabs",
    "open_tab",
    "find_or_open_tab",
    "call",
    "eval_js",
    "get_cookies",
    "get_cookie_header",
]
