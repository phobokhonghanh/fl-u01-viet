"""
AutoHDR Client EXE v2 — Entry Point.

Local pipeline execution: all AutoHDR API calls (Steps 1-7) run directly
from this EXE. Railway server is only used for key validation.
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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui.app import App

# Fix Linux lag by disabling system appearance tracking globally
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
