"""
Tab Quản lý Account — Quản lý Cookie AutoHDR và API Key Autoenhance.
Được nhúng vào SettingsDialog.
"""
import os
import json
import threading
import customtkinter as ctk
from tkinter import filedialog, messagebox
from typing import Optional, Callable

from libs.auth import (
    list_accounts,
    switch_account,
    delete_account,
    authenticate,
    get_active_session,
    BaseSessionRecord,
)


class AccountsTabFrame(ctk.CTkFrame):
    def __init__(
        self,
        master,
        initial_engine: str = "autohdr",
        on_change_callback: Optional[Callable[[str, Optional[BaseSessionRecord]], None]] = None,
        **kwargs
    ):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.initial_engine = initial_engine.lower()
        self.on_change_callback = on_change_callback

        self._build_ui()

    def _build_ui(self):
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=8, pady=4)

        # Tabview (AutoHDR vs Autoenhance)
        self.tabview = ctk.CTkTabview(container, height=440)
        self.tabview.pack(fill="both", expand=True)

        self.tab_autohdr = self.tabview.add("AutoHDR (Cookie)")
        self.tab_autoenhance = self.tabview.add("Autoenhance (API Key)")

        # Chọn tab mặc định theo engine
        if self.initial_engine == "autoenhance":
            self.tabview.set("Autoenhance (API Key)")
        else:
            self.tabview.set("AutoHDR (Cookie)")

        self._build_autohdr_tab()
        self._build_autoenhance_tab()

    # =========================================================================
    # 1. TAB AUTOHDR (COOKIE / EMAIL)
    # =========================================================================
    def _build_autohdr_tab(self):
        self.hdr_list_frame = ctk.CTkScrollableFrame(self.tab_autohdr, height=240)
        self.hdr_list_frame.pack(fill="both", expand=True, padx=4, pady=(4, 8))

        # Form thêm Cookie mới
        add_frame = ctk.CTkFrame(self.tab_autohdr, fg_color=("gray85", "gray20"), corner_radius=6)
        add_frame.pack(fill="x", padx=4, pady=(0, 4))

        ctk.CTkLabel(add_frame, text="➕ Thêm Cookie mới:", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=(6, 2))

        entry_row = ctk.CTkFrame(add_frame, fg_color="transparent")
        entry_row.pack(fill="x", padx=10, pady=(0, 4))

        self.hdr_cookie_entry = ctk.CTkEntry(entry_row, placeholder_text="Dán chuỗi Cookie hoặc JSON...", height=32, font=("Arial", 12))
        self.hdr_cookie_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(
            entry_row,
            text="📁 File",
            width=55,
            height=32,
            font=("Arial", 12),
            fg_color="#4B5563",
            hover_color="#374151",
            command=self._load_cookie_file,
        ).pack(side="left", padx=(0, 6))

        self.btn_add_hdr = ctk.CTkButton(
            entry_row,
            text="Xác thực & Thêm",
            width=115,
            height=32,
            font=("Arial", 12, "bold"),
            fg_color="#059669",
            hover_color="#047857",
            command=self._add_autohdr_cookie_async,
        )
        self.btn_add_hdr.pack(side="left")

        self.hdr_status_label = ctk.CTkLabel(add_frame, text="", font=("Arial", 11), text_color="gray")
        self.hdr_status_label.pack(anchor="w", padx=10, pady=(0, 6))

        self._refresh_autohdr_list()

    def _refresh_autohdr_list(self):
        for widget in self.hdr_list_frame.winfo_children():
            widget.destroy()

        accounts = list_accounts("autohdr")
        if not accounts:
            ctk.CTkLabel(
                self.hdr_list_frame,
                text="Chưa có tài khoản AutoHDR nào được lưu.",
                font=("Arial", 12, "italic"),
                text_color="gray",
            ).pack(pady=30)
            return

        for acc in accounts:
            card = ctk.CTkFrame(self.hdr_list_frame, fg_color=("gray90", "gray17"), border_width=1, border_color="#374151", corner_radius=6)
            card.pack(fill="x", pady=3, padx=2)

            left_box = ctk.CTkFrame(card, fg_color="transparent")
            left_box.pack(side="left", fill="x", expand=True, padx=8, pady=6)

            badge_text = "🟢 [Đang dùng]" if acc.is_active else "⚪ [Khả dụng]"
            badge_color = "#22C55E" if acc.is_active else "gray"
            ctk.CTkLabel(left_box, text=badge_text, font=("Arial", 11, "bold"), text_color=badge_color).pack(anchor="w")

            fullname = f"{getattr(acc, 'firstname', '')} {getattr(acc, 'lastname', '')}".strip()
            display_title = f"{acc.email} ({fullname})" if fullname else acc.email
            ctk.CTkLabel(left_box, text=display_title, font=("Arial", 13, "bold")).pack(anchor="w")

            right_box = ctk.CTkFrame(card, fg_color="transparent")
            right_box.pack(side="right", padx=8, pady=6)

            if acc.is_active:
                btn_active = ctk.CTkButton(
                    right_box, text="Đang dùng", width=85, height=28,
                    state="disabled", fg_color="gray40", font=("Arial", 11)
                )
                btn_active.pack(side="left", padx=4)
            else:
                btn_switch = ctk.CTkButton(
                    right_box, text="Kích hoạt", width=85, height=28,
                    font=("Arial", 11, "bold"), fg_color="#059669", hover_color="#047857",
                    command=lambda email=acc.email: self._switch_autohdr(email)
                )
                btn_switch.pack(side="left", padx=4)

            btn_del = ctk.CTkButton(
                right_box, text="🗑️", width=34, height=28,
                font=("Arial", 12), fg_color="#DC2626", hover_color="#B91C1C",
                command=lambda email=acc.email: self._delete_autohdr(email)
            )
            btn_del.pack(side="left", padx=2)

    def _switch_autohdr(self, email: str):
        record = switch_account(email, "autohdr")
        self._refresh_autohdr_list()
        if self.on_change_callback:
            self.on_change_callback("autohdr", record)

    def _delete_autohdr(self, email: str):
        if not messagebox.askyesno("Xác nhận", f"Bạn có chắc muốn xóa tài khoản {email}?", parent=self):
            return
        delete_account(email, "autohdr")
        active = get_active_session("autohdr")
        self._refresh_autohdr_list()
        if self.on_change_callback:
            self.on_change_callback("autohdr", active)

    def _load_cookie_file(self):
        file_path = filedialog.askopenfilename(
            title="Chọn file Cookie",
            filetypes=[("JSON/TEXT files", "*.json *.txt"), ("All files", "*.*")],
        )
        if not file_path:
            return
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                try:
                    data = json.loads(content)
                    if isinstance(data, dict) and "cookie" in data:
                        content = data["cookie"]
                    elif isinstance(data, list):
                        content = "; ".join([f"{c['name']}={c['value']}" for c in data if 'name' in c and 'value' in c])
                    elif isinstance(data, dict) and "cookies" in data and isinstance(data["cookies"], list):
                        content = "; ".join([f"{c['name']}={c['value']}" for c in data["cookies"] if 'name' in c and 'value' in c])
                except Exception:
                    pass
                self.hdr_cookie_entry.delete(0, "end")
                self.hdr_cookie_entry.insert(0, content)
        except Exception as e:
            self.hdr_status_label.configure(text=f"Lỗi đọc file: {e}", text_color="#EF4444")

    def _add_autohdr_cookie_async(self):
        cookie = self.hdr_cookie_entry.get().strip()
        if not cookie:
            self.hdr_status_label.configure(text="⚠️ Vui lòng nhập Cookie", text_color="#EF4444")
            return

        self.btn_add_hdr.configure(state="disabled", text="⏳ Đang kiểm tra...")
        self.hdr_status_label.configure(text="Đang kết nối xác thực với AutoHDR...", text_color="gray")

        def _do_auth():
            try:
                record = authenticate("autohdr", cookie=cookie)
                if record:
                    self.after(0, lambda: self._on_add_hdr_success(record))
                else:
                    self.after(0, lambda: self.hdr_status_label.configure(
                        text="⚠️ Cookie không hợp lệ hoặc đã hết hạn", text_color="#EF4444"))
            except Exception as e:
                self.after(0, lambda: self.hdr_status_label.configure(
                    text=f"⚠️ Lỗi xác thực: {str(e)[:35]}", text_color="#EF4444"))
            finally:
                self.after(0, lambda: self.btn_add_hdr.configure(state="normal", text="Xác thực & Thêm"))

        threading.Thread(target=_do_auth, daemon=True).start()

    def _on_add_hdr_success(self, record):
        self.hdr_cookie_entry.delete(0, "end")
        self.hdr_status_label.configure(text=f"✅ Đã thêm tài khoản: {record.email}", text_color="#22C55E")
        self._refresh_autohdr_list()
        if self.on_change_callback:
            self.on_change_callback("autohdr", record)

    # =========================================================================
    # 2. TAB AUTOENHANCE (API KEY)
    # =========================================================================
    def _build_autoenhance_tab(self):
        self.ae_list_frame = ctk.CTkScrollableFrame(self.tab_autoenhance, height=240)
        self.ae_list_frame.pack(fill="both", expand=True, padx=4, pady=(4, 8))

        # Form thêm API Key mới
        add_frame = ctk.CTkFrame(self.tab_autoenhance, fg_color=("gray85", "gray20"), corner_radius=6)
        add_frame.pack(fill="x", padx=4, pady=(0, 4))

        ctk.CTkLabel(add_frame, text="➕ Thêm API Key mới:", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=(6, 2))

        entry_row = ctk.CTkFrame(add_frame, fg_color="transparent")
        entry_row.pack(fill="x", padx=10, pady=(0, 4))

        self.ae_key_entry = ctk.CTkEntry(entry_row, placeholder_text="Dán chuỗi API Key Autoenhance...", height=32, font=("Arial", 12), show="*")
        self.ae_key_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.btn_add_ae = ctk.CTkButton(
            entry_row,
            text="Xác thực & Lưu",
            width=115,
            height=32,
            font=("Arial", 12, "bold"),
            fg_color="#2563EB",
            hover_color="#1D4ED8",
            command=self._add_autoenhance_key_async,
        )
        self.btn_add_ae.pack(side="left")

        self.ae_status_label = ctk.CTkLabel(add_frame, text="", font=("Arial", 11), text_color="gray")
        self.ae_status_label.pack(anchor="w", padx=10, pady=(0, 6))

        self._refresh_autoenhance_list()

    def _refresh_autoenhance_list(self):
        for widget in self.ae_list_frame.winfo_children():
            widget.destroy()

        keys = list_accounts("autoenhance")
        if not keys:
            ctk.CTkLabel(
                self.ae_list_frame,
                text="Chưa có API Key Autoenhance nào được lưu.",
                font=("Arial", 12, "italic"),
                text_color="gray",
            ).pack(pady=30)
            return

        for acc in keys:
            card = ctk.CTkFrame(self.ae_list_frame, fg_color=("gray90", "gray17"), border_width=1, border_color="#374151", corner_radius=6)
            card.pack(fill="x", pady=3, padx=2)

            left_box = ctk.CTkFrame(card, fg_color="transparent")
            left_box.pack(side="left", fill="x", expand=True, padx=8, pady=6)

            badge_text = "🟢 [Đang dùng]" if acc.is_active else "⚪ [Khả dụng]"
            badge_color = "#22C55E" if acc.is_active else "gray"
            ctk.CTkLabel(left_box, text=badge_text, font=("Arial", 11, "bold"), text_color=badge_color).pack(anchor="w")

            ctk.CTkLabel(left_box, text=acc.display_name, font=("Arial", 13, "bold")).pack(anchor="w")

            right_box = ctk.CTkFrame(card, fg_color="transparent")
            right_box.pack(side="right", padx=8, pady=6)

            if acc.is_active:
                btn_active = ctk.CTkButton(
                    right_box, text="Đang dùng", width=85, height=28,
                    state="disabled", fg_color="gray40", font=("Arial", 11)
                )
                btn_active.pack(side="left", padx=4)
            else:
                btn_switch = ctk.CTkButton(
                    right_box, text="Kích hoạt", width=85, height=28,
                    font=("Arial", 11, "bold"), fg_color="#2563EB", hover_color="#1D4ED8",
                    command=lambda k=acc.api_key: self._switch_autoenhance(k)
                )
                btn_switch.pack(side="left", padx=4)

            btn_del = ctk.CTkButton(
                right_box, text="🗑️", width=34, height=28,
                font=("Arial", 12), fg_color="#DC2626", hover_color="#B91C1C",
                command=lambda k=acc.api_key: self._delete_autoenhance(k)
            )
            btn_del.pack(side="left", padx=2)

    def _switch_autoenhance(self, api_key: str):
        record = switch_account(api_key, "autoenhance")
        self._refresh_autoenhance_list()
        if self.on_change_callback:
            self.on_change_callback("autoenhance", record)

    def _delete_autoenhance(self, api_key: str):
        masked = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else api_key
        if not messagebox.askyesno("Xác nhận", f"Bạn có chắc muốn xóa API Key {masked}?", parent=self):
            return
        delete_account(api_key, "autoenhance")
        active = get_active_session("autoenhance")
        self._refresh_autoenhance_list()
        if self.on_change_callback:
            self.on_change_callback("autoenhance", active)

    def _add_autoenhance_key_async(self):
        key = self.ae_key_entry.get().strip()
        if not key:
            self.ae_status_label.configure(text="⚠️ Vui lòng nhập API Key", text_color="#EF4444")
            return

        self.btn_add_ae.configure(state="disabled", text="⏳ Đang kiểm tra...")
        self.ae_status_label.configure(text="Đang kiểm tra tính hợp lệ với Autoenhance API...", text_color="gray")

        def _do_auth():
            try:
                record = authenticate("autoenhance", api_key=key)
                if record:
                    self.after(0, lambda: self._on_add_ae_success(record))
                else:
                    self.after(0, lambda: self.ae_status_label.configure(
                        text="⚠️ API Key không hợp lệ hoặc đã hết hạn", text_color="#EF4444"))
            except Exception as e:
                self.after(0, lambda: self.ae_status_label.configure(
                    text=f"⚠️ Lỗi xác thực: {str(e)[:35]}", text_color="#EF4444"))
            finally:
                self.after(0, lambda: self.btn_add_ae.configure(state="normal", text="Xác thực & Lưu"))

        threading.Thread(target=_do_auth, daemon=True).start()

    def _on_add_ae_success(self, record):
        self.ae_key_entry.delete(0, "end")
        self.ae_status_label.configure(text=f"✅ Đã lưu API Key: {record.display_name}", text_color="#22C55E")
        self._refresh_autoenhance_list()
        if self.on_change_callback:
            self.on_change_callback("autoenhance", record)

