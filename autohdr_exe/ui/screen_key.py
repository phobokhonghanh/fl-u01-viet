"""
Screen Key — Key activation screen.

Displayed first when opening the app. User enters their API key
and clicks Check. If valid, switches to main screen.
"""

import customtkinter as ctk
from core.api_client import ApiClient
import webbrowser


class ScreenKey(ctk.CTkFrame):
    def __init__(self, master, app, license_status: str = "", **kwargs):
        super().__init__(master, **kwargs)
        self.app = app
        self.api = ApiClient()
        self.license_status = license_status

        # Center content
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        center_frame = ctk.CTkFrame(self, fg_color="transparent")
        center_frame.grid(row=0, column=0)

        # Title
        ctk.CTkLabel(
            center_frame,
            text="AutoHDR",
            font=("Arial", 28, "bold"),
        ).pack(pady=(0, 30))

        # Key input
        ctk.CTkLabel(
            center_frame,
            text="Nhập Key kích hoạt",
            font=("Arial", 16, "bold"),
        ).pack(pady=(25, 10))

        self.key_entry = ctk.CTkEntry(
            center_frame,
            placeholder_text="Nhập Key của bạn...",
            width=400,
            height=50,
            font=("Arial", 16),
        )
        self.key_entry.pack(pady=5)

        # Check button
        self.btn_check = ctk.CTkButton(
            center_frame,
            text="Kiểm Tra & Kích Hoạt",
            command=self.check_key,
            width=250,
            height=50,
            font=("Arial", 16, "bold"),
            fg_color="#2563EB",
            hover_color="#1D4ED8",
        )
        self.btn_check.pack(pady=20)

        # Status label
        self.status_label = ctk.CTkLabel(
            center_frame,
            text="",
            font=("Arial", 15),
            text_color="gray",
        )
        self.status_label.pack(pady=5)
        self._apply_initial_status()

        # Footer Link: by tuitenPhở
        footer_label = ctk.CTkLabel(
            self,
            text="© 2026 Nguyen Dinh Nguyen - Development & Maintenance",
            text_color="#22C55E",  # Green
            font=ctk.CTkFont(family="Arial", size=13, weight="bold", slant="italic"),
            cursor="hand2"
        )
        footer_label.place(relx=0.98, rely=0.97, anchor="se")
        footer_label.bind("<Button-1>", lambda e: webbrowser.open("https://www.facebook.com/ndinhnguyende/"))

    def _apply_initial_status(self):
        """Display the reason the user was routed to the key screen."""
        if self.license_status == "expired":
            self.status_label.configure(text="Key đã hết hạn, vui lòng nhập lại.", text_color="#F59E0B")
        elif self.license_status in {"invalid", "machine_mismatch"}:
            self.status_label.configure(
                text="Key không hợp lệ, hết hạn hoặc đã dùng trên máy khác",
                text_color="#EF4444",
            )

    def check_key(self):
        key = self.key_entry.get().strip()
        if not key:
            self.status_label.configure(text="Vui lòng nhập Key", text_color="#EF4444")
            return

        self.status_label.configure(text="Đang kết nối server...", text_color="#3B82F6")
        self.btn_check.configure(state="disabled", text="Đang kiểm tra...")
        self.update()

        try:
            is_valid = self.api.check_key(key)
            if is_valid:
                self.status_label.configure(text="Kích hoạt thành công!", text_color="#22C55E")
                self.update()
                self.after(800, lambda: self.app.show_main_screen())
            else:
                status = self.api.last_check_status
                if status == "expired":
                    self.status_label.configure(text="Key đã hết hạn, vui lòng nhập lại.", text_color="#F59E0B")
                elif status == "network_error":
                    self.status_label.configure(text="Không thể kết nối.", text_color="#EF4444")
                else:
                    self.status_label.configure(
                        text="Key không hợp lệ, hết hạn hoặc đã dùng trên máy khác",
                        text_color="#EF4444",
                    )
                self.btn_check.configure(state="normal", text="Kiểm Tra & Kích Hoạt")
        except Exception as e:
            self.status_label.configure(text=f"Lỗi: {str(e)}", text_color="#EF4444")
            self.btn_check.configure(state="normal", text="Kiểm Tra & Kích Hoạt")
