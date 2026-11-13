"""
AutoHDR Client EXE v6.1 — Entry Point.

Local pipeline execution: all AutoHDR API calls (Steps 1-7) run directly
from this EXE. Cloudflare server is only used for key validation.
"""

import customtkinter as ctk
import sys
import os
from dotenv import load_dotenv

# 1. Load bundled .env (if running as PyInstaller EXE)
if getattr(sys, 'frozen', False):
    bundled_env = os.path.join(sys._MEIPASS, '.env')
    if os.path.exists(bundled_env):
        load_dotenv(bundled_env)

# 2. Load external .env (next to EXE or in CWD) — allows user overrides
external_env = os.path.join(os.getcwd(), '.env')
load_dotenv(external_env, override=True)

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

# Add 1tm and 1tm/libs to path for multi-engine jobs & auth
TM_DIR = os.path.join(PROJECT_ROOT, "1tm")
LIBS_DIR = os.path.join(TM_DIR, "libs")
if TM_DIR not in sys.path:
    sys.path.insert(0, TM_DIR)
if LIBS_DIR not in sys.path:
    sys.path.insert(0, LIBS_DIR)

# Smoke test flag for CI/CD packaging validation
if "--smoke-test" in sys.argv:
    from ui.app import App
    from ui.screen_main import ScreenMain
    from ui.screen_key import ScreenKey
    from ui.settings_dialog import SettingsDialog
    from ui.tab_storage import StorageTabFrame
    from ui.tab_accounts import AccountsTabFrame
    from ui.components import (
        ConfigAutoHDRFrame,
        ConfigAutoenhanceFrame,
        DropZoneFrame,
        JobListFrame,
        LogViewerFrame,
    )
    from jobs import (
        JobRunner,
        AutoHDRJob,
        AutoHDRDownloadJob,
        AutoenhanceJob,
        AutoenhanceDownloadJob,
    )
    from libs.auth import get_active_session, authenticate
    from libs.executor import ExecutorRegistry
    from PIL import Image
    print("Smoke test passed: All critical modules imported successfully.")
    sys.exit(0)

from ui.app import App

# Fix Linux lag by disabling system appearance tracking globally
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
