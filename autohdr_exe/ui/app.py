"""
Main Application Controller — manages screen switching.
"""

import customtkinter as ctk
from tkinterdnd2 import TkinterDnD
from core.api_client import ApiClient
from core.pipeline import PipelineManager


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Initialize Drag and Drop
        self.TkdndVersion = TkinterDnD._require(self)

        self.title("AutoHDR - v3")
        self.geometry("1280×800")
        self.minsize(1000, 700)

        self.api = ApiClient()
        self.pipeline_mgr = PipelineManager()

        # Container for screens
        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.pack(fill="both", expand=True)

        # Screens
        self.screen_key = None
        self.screen_main = None

        self._auto_check_key()

    def _auto_check_key(self):
        """Auto-check cached key on startup."""
        try:
            is_valid = self.api.check_key()
            if is_valid:
                self.show_main_screen()
                return
        except Exception:
            pass
        # Key invalid or expired — show key screen
        self.show_key_screen(self.api.last_check_status)

    def show_key_screen(self, license_status: str = ""):
        """Switch to key activation screen."""
        self._clear_screens()
        from ui.screen_key import ScreenKey
        self.screen_key = ScreenKey(self.container, self, license_status=license_status)
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
