"""
Settings Dialog — Màn hình Cài đặt gộp chung hệ thống.
Bao gồm:
- Tab 1: Lưu (StorageTabFrame)
- Tab 2: Quản lý account (AccountsTabFrame)
- Nút Bản quyền (License Key) được đưa vào trong Settings.
"""
import customtkinter as ctk
from typing import Optional, Callable
from ui.tab_storage import StorageTabFrame
from ui.tab_accounts import AccountsTabFrame
from libs.auth import BaseSessionRecord


class SettingsDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        app=None,
        current_download_dir: str = "",
        on_save_storage: Optional[Callable[[str], None]] = None,
        on_change_account: Optional[Callable[[str, Optional[BaseSessionRecord]], None]] = None,
        initial_tab: str = "storage",
        initial_engine: str = "autohdr",
    ):
        super().__init__(master)
        self.app = app or getattr(master, "app", None)
        self.title("Cài Đặt Hệ Thống")
        self.geometry("640x580")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self.current_download_dir = current_download_dir
        self.on_save_storage = on_save_storage
        self.on_change_account = on_change_account
        self.initial_tab = initial_tab
        self.initial_engine = initial_engine

        self._build_ui()
        self.center_window()

    def center_window(self):
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = (self.winfo_screenwidth() // 2) - (width // 2)
        y = (self.winfo_screenheight() // 2) - (height // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build_ui(self):
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=16, pady=12)

        # Header: Title + License Key button (Đưa icon key vào trong setting)
        header_row = ctk.CTkFrame(main_frame, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(
            header_row,
            text="⚙ Cài Đặt Hệ Thống",
            font=("Arial", 18, "bold")
        ).pack(side="left")

        # Nút License Key nằm góc trên phải của Settings
        ctk.CTkButton(
            header_row,
            text="🔑 Bản quyền",
            width=100,
            height=30,
            font=("Arial", 12, "bold"),
            fg_color="#7C3AED",
            hover_color="#6D28D9",
            command=self._open_license_key,
        ).pack(side="right")

        # Tabview chính
        self.tabview = ctk.CTkTabview(main_frame, height=460)
        self.tabview.pack(fill="both", expand=True, pady=(0, 8))

        self.tab_storage = self.tabview.add("Lưu")
        self.tab_accounts = self.tabview.add("Quản lý account")

        # Tab 1: Lưu
        self.storage_frame = StorageTabFrame(
            self.tab_storage,
            current_download_dir=self.current_download_dir,
            on_save_callback=self.on_save_storage,
        )
        self.storage_frame.pack(fill="both", expand=True)

        # Tab 2: Quản lý Account
        self.accounts_frame = AccountsTabFrame(
            self.tab_accounts,
            initial_engine=self.initial_engine,
            on_change_callback=self.on_change_account,
        )
        self.accounts_frame.pack(fill="both", expand=True)

        # Chọn tab khởi tạo
        if self.initial_tab == "accounts":
            self.tabview.set("Quản lý account")
        else:
            self.tabview.set("Lưu")

        # Footer row: Nút Đóng
        footer_row = ctk.CTkFrame(main_frame, fg_color="transparent")
        footer_row.pack(fill="x")

        ctk.CTkButton(
            footer_row,
            text="Đóng",
            width=90,
            height=32,
            font=("Arial", 12),
            fg_color="#374151",
            hover_color="#1F2937",
            command=self.destroy,
        ).pack(side="right")

    def _open_license_key(self):
        """Chuyển sang màn hình kích hoạt bản quyền."""
        self.destroy()
        if self.app and hasattr(self.app, "show_key_screen"):
            self.app.show_key_screen()
