"""
Main Application Controller — manages screen switching.
"""

import customtkinter as ctk
from tkinterdnd2 import TkinterDnD
from core.api_client import ApiClient
from core.cache import cache
from core.utils import get_hwid
from core.pipeline import PipelineManager


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Initialize Drag and Drop
        self.TkdndVersion = TkinterDnD._require(self)

        self.title("AutoHDR - v2.1")
        self.geometry("1280×800")
        self.minsize(1000, 700)

        self.api = ApiClient()
        self.hwid = get_hwid()
        self.pipeline_mgr = PipelineManager()

        # Container for screens
        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.pack(fill="both", expand=True)

        # Screens
        self.screen_key = None
        self.screen_main = None

        # Check cached key on startup
        cached_key = cache.get("active_key")
        if cached_key:
            self._auto_check_key(cached_key)
        else:
            self.show_key_screen()

    def _auto_check_key(self, key: str):
        """Auto-check cached key on startup."""
        try:
            is_valid = self.api.check_key(key, self.hwid)
            if is_valid:
                self.show_main_screen()
                return
        except Exception:
            pass
        # Key invalid — show key screen
        cache.delete("active_key")
        self.show_key_screen()

    def show_key_screen(self):
        """Switch to key activation screen."""
        self._clear_screens()
        from ui.screen_key import ScreenKey
        self.screen_key = ScreenKey(self.container, self)
        self.screen_key.pack(fill="both", expand=True)

    def show_main_screen(self):
        """Switch to main screen."""
        self._clear_screens()
        from ui.screen_main import ScreenMain
        self.screen_main = ScreenMain(self.container, self)
        self.screen_main.pack(fill="both", expand=True)

    def _clear_screens(self):
        """Remove all current screens."""
        for widget in self.container.winfo_children():
            widget.destroy()
        self.screen_key = None
        self.screen_main = None
