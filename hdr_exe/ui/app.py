"""Desktop Application Entry Point using pywebview.

Loads ui/index.html and initializes the Python-JavaScript Bridge API.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import webview
from ui.bridge import BridgeApi

logger = logging.getLogger("hdr_exe.ui")


def create_app(debug: bool = False) -> tuple[webview.Window, BridgeApi]:
    """Initialize pywebview window and BridgeApi."""
    ui_dir = Path(__file__).resolve().parent
    index_html = ui_dir / "index.html"
    if not index_html.is_file():
        raise FileNotFoundError(f"Cannot find UI entrypoint: {index_html}")

    bridge = BridgeApi()
    window = webview.create_window(
        title="hdr_exe - HDR Processing Suite",
        url=index_html.as_uri(),
        js_api=bridge,
        width=1280,
        height=760,
        min_size=(1024, 640),
        text_select=True,
    )
    bridge.set_window(window)
    return window, bridge


def run_desktop(debug: bool = False) -> None:
    """Launch the pywebview GUI application."""
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    window, bridge = create_app(debug=debug)
    webview.start(debug=debug)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="hdr_exe Desktop Application")
    parser.add_argument("--debug", action="store_true", help="Enable developer tools and debug logging")
    args = parser.parse_args()
    run_desktop(debug=args.debug)
