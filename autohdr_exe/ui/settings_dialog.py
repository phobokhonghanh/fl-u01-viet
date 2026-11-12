"""
Settings Dialog — Modal dialog for application settings (Download dir, etc.)
"""
import os
import customtkinter as ctk
from tkinter import filedialog
from core.cache import cache


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, master, current_download_dir: str, on_save_callback=None):
        super().__init__(master)
        self.title("Cài Đặt Hệ Thống")
        self.geometry("520x240")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self.on_save_callback = on_save_callback
        self.download_dir = current_download_dir

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
        main_frame.pack(fill="both", expand=True, padx=20, pady=15)

        # Title
        ctk.CTkLabel(
            main_frame,
            text="⚙ Cài Đặt",
            font=("Arial", 16, "bold")
        ).pack(anchor="w", pady=(0, 10))

        # Download directory
        ctk.CTkLabel(
            main_frame,
            text="Thư mục đầu ra (Download Dir):",
            font=("Arial", 12, "bold")
        ).pack(anchor="w", pady=(5, 3))

        dir_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        dir_frame.pack(fill="x", expand=True, pady=(0, 5))

        self.dir_entry = ctk.CTkEntry(dir_frame, font=("Arial", 12), height=32)
        self.dir_entry.insert(0, self.download_dir)
        self.dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(
            dir_frame,
            text="📂 Duyệt",
            width=70,
            height=32,
            font=("Arial", 12, "bold"),
            command=self._select_dir,
            fg_color="#4B5563",
            hover_color="#374151"
        ).pack(side="right")

        ctk.CTkLabel(
            main_frame,
            text="Tất cả ảnh thành phẩm tải về từ AutoHDR và Autoenhance sẽ lưu tại đây.",
            font=("Arial", 10),
            text_color="#9CA3AF"
        ).pack(anchor="w", pady=(0, 15))

        # Buttons
        btn_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        btn_frame.pack(fill="x", side="bottom")

        ctk.CTkButton(
            btn_frame,
            text="Đóng",
            width=80,
            height=32,
            font=("Arial", 12),
            fg_color="#374151",
            hover_color="#1F2937",
            command=self.destroy
        ).pack(side="right", padx=(5, 0))

        ctk.CTkButton(
            btn_frame,
            text="Lưu cấu hình",
            width=110,
            height=32,
            font=("Arial", 12, "bold"),
            fg_color="#059669",
            hover_color="#047857",
            command=self._save
        ).pack(side="right")

    def _select_dir(self):
        init_dir = self.dir_entry.get().strip() or os.path.expanduser("~/Downloads")
        chosen = filedialog.askdirectory(title="Chọn thư mục lưu ảnh", initialdir=init_dir)
        if chosen:
            self.dir_entry.delete(0, "end")
            self.dir_entry.insert(0, chosen)

    def _save(self):
        new_dir = self.dir_entry.get().strip()
        if new_dir:
            cache.set("download_dir", new_dir)
            if self.on_save_callback:
                self.on_save_callback(new_dir)
        self.destroy()

