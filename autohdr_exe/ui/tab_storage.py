"""
Tab Lưu — Cấu hình thư mục lưu trữ đầu ra (Download Dir).
Được nhúng vào SettingsDialog.
"""
import os
import customtkinter as ctk
from tkinter import filedialog
from typing import Optional, Callable
from core.cache import cache


class StorageTabFrame(ctk.CTkFrame):
    def __init__(
        self,
        master,
        current_download_dir: str = "",
        on_save_callback: Optional[Callable[[str], None]] = None,
        **kwargs
    ):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.download_dir = current_download_dir or cache.get("download_dir", os.path.expanduser("~/Downloads"))
        self.on_save_callback = on_save_callback

        self._build_ui()

    def _build_ui(self):
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(
            container,
            text="📁 Thư mục lưu trữ thành phẩm (Download Dir)",
            font=("Arial", 14, "bold"),
        ).pack(anchor="w", pady=(0, 6))

        desc_text = "Tất cả ảnh thành phẩm sau khi xử lý từ AutoHDR và Autoenhance sẽ được tự động tải về thư mục này."
        ctk.CTkLabel(
            container,
            text=desc_text,
            font=("Arial", 11),
            text_color="#9CA3AF",
            wraplength=560,
            justify="left"
        ).pack(anchor="w", pady=(0, 14))

        # Input row
        dir_row = ctk.CTkFrame(container, fg_color="transparent")
        dir_row.pack(fill="x", pady=(0, 10))

        self.dir_entry = ctk.CTkEntry(dir_row, font=("Arial", 12), height=34)
        self.dir_entry.insert(0, self.download_dir)
        self.dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        ctk.CTkButton(
            dir_row,
            text="📂 Duyệt",
            width=80,
            height=34,
            font=("Arial", 12, "bold"),
            fg_color="#4B5563",
            hover_color="#374151",
            command=self._select_directory,
        ).pack(side="right")

        # Action row
        action_row = ctk.CTkFrame(container, fg_color="transparent")
        action_row.pack(fill="x", pady=(10, 0))

        self.btn_save = ctk.CTkButton(
            action_row,
            text="Lưu cấu hình",
            width=120,
            height=34,
            font=("Arial", 12, "bold"),
            fg_color="#059669",
            hover_color="#047857",
            command=self._save_settings,
        )
        self.btn_save.pack(side="left")

        self.status_label = ctk.CTkLabel(action_row, text="", font=("Arial", 11), text_color="#10B981")
        self.status_label.pack(side="left", padx=12)

    def _select_directory(self):
        current = self.dir_entry.get().strip()
        initial = current if os.path.isdir(current) else os.path.expanduser("~")
        selected = filedialog.askdirectory(initialdir=initial, title="Chọn thư mục lưu ảnh")
        if selected:
            norm_dir = os.path.normpath(selected)
            self.dir_entry.delete(0, "end")
            self.dir_entry.insert(0, norm_dir)

    def _save_settings(self):
        new_dir = self.dir_entry.get().strip()
        if not new_dir:
            self.status_label.configure(text="⚠️ Thư mục không được để trống", text_color="#EF4444")
            return

        try:
            os.makedirs(new_dir, exist_ok=True)
            norm_dir = os.path.normpath(new_dir)
            self.download_dir = norm_dir
            cache.set("download_dir", norm_dir)
            if self.on_save_callback:
                self.on_save_callback(norm_dir)
            self.status_label.configure(text="✅ Đã lưu cấu hình thư mục thành công!", text_color="#10B981")
        except Exception as exc:
            self.status_label.configure(text=f"⚠️ Không thể tạo thư mục: {exc}", text_color="#EF4444")

