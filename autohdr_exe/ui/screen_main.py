"""
Screen Main — Main application screen after key activation.

Layout (proper 50/50 split):
┌──────────────────────────────────────────────────────────┐
│ [Cookie Input] [Init] [⏳]             [email] [Key]    │  ← Top Bar
├────────────────────────────┬─────────────────────────────┤
│ 📋 Danh sách Jobs          │ 📝 Log Job: {selected}     │
│ ┌────────────────────────┐ │ [Clear]                     │
│ │ Job abc | Address...   │ │ ┌─────────────────────────┐ │
│ │ ✅ Hoàn thành          │ │ │ per-job log textbox     │ │
│ │ Job def | Address...   │ │ │ auto-scroll bottom      │ │
│ │ ⏳ Đang xử lý...  [⏹] │ │ │                         │ │
│ └────────────────────────┘ │ │                         │ │
│ ┌────────────────────────┐ │ │                         │ │
│ │ 📁 Drop Zone           │ │ │                         │ │
│ │ (drag & drop area)     │ │ │                         │ │
│ └────────────────────────┘ │ └─────────────────────────┘ │
│ 5 files selected           │                             │
│ [📂 Download Dir] [...]    │                             │
│ [Address Input]            │                             │
│ [====== 🚀 XỬ LÝ ======]  │                             │
└────────────────────────────┴─────────────────────────────┘
"""

import os
import sys
import json
import threading
import datetime
import platform
import subprocess
import customtkinter as ctk
import re
from typing import Optional
from urllib.parse import urlparse
from tkinter import filedialog
from tkinterdnd2 import DND_FILES

# Đưa 1tm và 1tm/libs vào sys.path để nạp JobRunner & Auth đa engine
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TM_DIR = os.path.join(PROJECT_ROOT, "1tm")
LIBS_DIR = os.path.join(TM_DIR, "libs")
if TM_DIR not in sys.path:
    sys.path.insert(0, TM_DIR)
if LIBS_DIR not in sys.path:
    sys.path.insert(0, LIBS_DIR)

# Fix lag on Linux by disabling system appearance tracking
# This prevents constant subprocess calls to darkdetect
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

from core.api_client import ApiClient
from core.cache import cache
from core.http_client import HttpClient
from core.pipeline import PipelineManager, Job
from core.utils import open_folder
from steps import step0_session
from models.available_models import (
    CATEGORIES,
    CATEGORY_INDOOR,
    get_model_names_by_category,
    get_model_by_name_and_category,
    get_payload_model_ids,
)
from ui.settings_dialog import SettingsDialog
from ui.accounts_dialog import AccountsDialog
from jobs import JobRunner
from libs.auth import get_active_session, authenticate, BaseSessionRecord
from libs.executor import ExecutionContext


def parse_autoenhance_url(url_str: str) -> tuple[bool, str, str]:
    """
    Chỉ chấp nhận URL Autoenhance: https://app.autoenhance.ai/orders/<order_id>
    Trả về: (hợp_lệ: bool, order_id: str, lỗi: str)
    """
    raw = url_str.strip()
    if not (raw.startswith("http://") or raw.startswith("https://")):
        return False, "", "Vui lòng nhập link URL hợp lệ (bắt đầu bằng http:// hoặc https://)"

    parsed = urlparse(raw)
    if "autoenhance.ai" not in parsed.netloc.lower():
        return False, "", f"Link không thuộc tên miền autoenhance.ai (Domain: {parsed.netloc})"

    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) >= 2 and parts[0].lower() == "orders":
        return True, parts[1], ""

    return False, "", "Đường dẫn URL không đúng cấu trúc (/orders/<order_id>)"


def parse_autohdr_url(url_str: str) -> tuple[bool, str, str]:
    """
    Chỉ chấp nhận URL AutoHDR: https://app.autohdr.com/photoshoots/<photoshoot_id>
    Trả về: (hợp_lệ: bool, photoshoot_id: str, lỗi: str)
    """
    raw = url_str.strip()
    if not (raw.startswith("http://") or raw.startswith("https://")):
        return False, "", "Vui lòng nhập link URL hợp lệ (bắt đầu bằng http:// hoặc https://)"

    parsed = urlparse(raw)
    if "autohdr" not in parsed.netloc.lower():
        return False, "", f"Link không thuộc hệ thống autohdr (Domain: {parsed.netloc})"

    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) >= 2 and parts[0].lower() == "photoshoots":
        return True, parts[1], ""

    return False, "", "Đường dẫn URL không đúng cấu trúc (/photoshoots/<id>)"


class ScreenMain(ctk.CTkFrame):
    def __init__(self, master, app, **kwargs):
        super().__init__(master, **kwargs)
        self.app = app
        self.api = ApiClient()
        self.pipeline_mgr = self.app.pipeline_mgr  # Use manager from App
        self.job_runner = getattr(self.app, 'job_runner', None) or JobRunner()
        self.selected_files = []
        self.allowed_extensions = {'.jpg', '.jpeg', '.png', '.webp'}
        self.current_session = None
        self.current_ae_session = None
        cached_web = cache.get("selected_web", "AutoHDR")
        if cached_web not in ["AutoHDR", "Autoenhance"]:
            cached_web = "AutoHDR"
        self.current_web = cached_web.strip().lower()
        self.is_manual_mode = bool(cache.get("is_manual_mode", False))
        self.download_dir = cache.get("download_dir", os.path.expanduser("~/Downloads"))
        self.selected_job_id = None  # Currently selected job for log viewing
        self.job_widgets = {}  # job_id -> widget reference
        self.job_engine_map = {}  # job_id -> "autohdr" | "autoenhance"
        self.job_logs = {}  # job_id -> list of log strings

        # Load cached session
        self._load_cached_session()

        self._build_ui()
        self._restore_existing_jobs()

    def _restore_existing_jobs(self):
        """Restore UI for existing jobs from the manager."""
        jobs = self.pipeline_mgr.get_all_jobs()
        if not jobs:
            return

        for job in jobs:
            self._add_job_to_list(job)
            # Re-attach callbacks to this new screen instance
            self.pipeline_mgr.update_callbacks(
                job.job_id,
                on_log=lambda jid, msg: self.after(0, lambda j=jid, m=msg: self._on_job_log(j, m)),
                on_job_update=lambda j: self.after(0, lambda: self._refresh_job_list()),
            )

        # Select the most recent processing job, or last job
        processing_jobs = [j for j in jobs if j.status == "processing"]
        if processing_jobs:
            self._select_job(processing_jobs[-1].job_id)
        else:
            self._select_job(jobs[-1].job_id)

        self._refresh_job_list()

    def _load_cached_session(self):
        """Try to restore sessions for AutoHDR and Autoenhance."""
        # 1. AutoHDR
        active_autohdr = get_active_session("autohdr")
        if active_autohdr:
            from models.schemas import SessionRecord
            self.current_session = SessionRecord(
                cookie=active_autohdr.cookie,
                email=active_autohdr.email,
                user_id=active_autohdr.user_id,
                firstname=active_autohdr.firstname,
                lastname=active_autohdr.lastname,
                expires=active_autohdr.expires,
            )
        else:
            cached_email = cache.get("email")
            cached_cookie = cache.get("cookie")
            if cached_email and cached_cookie:
                from models.schemas import SessionRecord
                self.current_session = SessionRecord(
                    cookie=cached_cookie,
                    email=cached_email,
                    user_id=cache.get("user_id", ""),
                    firstname=cache.get("firstname", ""),
                    lastname=cache.get("lastname", ""),
                    expires=cache.get("expires", ""),
                )

        # 2. Autoenhance
        self.current_ae_session = get_active_session("autoenhance")

    def _build_ui(self):
        """Build the full main screen layout."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ============================================================
        # TOP BAR: Web selector + Auth (AutoHDR / Autoenhance) | Key button
        # ============================================================
        top_bar = ctk.CTkFrame(self)
        top_bar.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))

        # Left side: Web selector + Dynamic Auth
        cookie_frame = ctk.CTkFrame(top_bar, fg_color="transparent")
        cookie_frame.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(cookie_frame, text="Web:", font=("Arial", 14, "bold")).pack(side="left", padx=(5, 3))
        initial_web = "Autoenhance" if self.current_web == "autoenhance" else "AutoHDR"
        self.web_var = ctk.StringVar(value=initial_web)
        self.web_selector = ctk.CTkComboBox(
            cookie_frame,
            values=["AutoHDR", "Autoenhance"],
            variable=self.web_var,
            command=self._on_web_changed,
            state="readonly",
            width=125,
            height=35,
            font=("Arial", 13, "bold"),
        )
        self.web_selector.pack(side="left", padx=(0, 8))

        # Auth container (holds AutoHDR and Autoenhance auth panels)
        self.auth_container = ctk.CTkFrame(cookie_frame, fg_color="transparent")
        self.auth_container.pack(side="left", fill="x", expand=True)

        # 1. AutoHDR Auth Frame
        self.autohdr_auth_frame = ctk.CTkFrame(self.auth_container, fg_color="transparent")
        self.autohdr_auth_frame.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(self.autohdr_auth_frame, text="Tài khoản:", font=("Arial", 13, "bold")).pack(side="left", padx=(2, 6))
        self.email_label = ctk.CTkLabel(self.autohdr_auth_frame, text="", font=("Arial", 13, "bold"), text_color="gray")
        self.email_label.pack(side="left", padx=(0, 10))
        if self.current_session and getattr(self.current_session, "email", ""):
            self.email_label.configure(text=f"🟢 {self.current_session.email}", text_color="#22C55E")
        else:
            self.email_label.configure(text="⚪ Chưa đăng nhập", text_color="gray")

        self.btn_manage_hdr = ctk.CTkButton(
            self.autohdr_auth_frame,
            text="👥 Quản lý Cookie",
            width=125,
            height=32,
            command=lambda: self._open_accounts_dialog("autohdr"),
            fg_color="#374151",
            hover_color="#1F2937",
            font=("Arial", 12, "bold"),
        )
        self.btn_manage_hdr.pack(side="left", padx=2)

        # 2. Autoenhance Auth Frame (initially hidden)
        self.autoenhance_auth_frame = ctk.CTkFrame(self.auth_container, fg_color="transparent")

        ctk.CTkLabel(self.autoenhance_auth_frame, text="API Key:", font=("Arial", 13, "bold")).pack(side="left", padx=(2, 6))
        self.autoenhance_status_label = ctk.CTkLabel(self.autoenhance_auth_frame, text="", font=("Arial", 13, "bold"), text_color="gray")
        self.autoenhance_status_label.pack(side="left", padx=(0, 10))
        if self.current_ae_session and getattr(self.current_ae_session, "api_key", ""):
            self.autoenhance_status_label.configure(text=f"🟢 {self.current_ae_session.display_name}", text_color="#22C55E")
        else:
            self.autoenhance_status_label.configure(text="⚪ Chưa có API Key", text_color="gray")

        self.btn_manage_ae = ctk.CTkButton(
            self.autoenhance_auth_frame,
            text="👥 Quản lý API Key",
            width=125,
            height=32,
            command=lambda: self._open_accounts_dialog("autoenhance"),
            fg_color="#374151",
            hover_color="#1F2937",
            font=("Arial", 12, "bold"),
        )
        self.btn_manage_ae.pack(side="left", padx=2)

        # Right side: Key button (Icon key)
        ctk.CTkButton(
            top_bar, text="🔑", width=42, height=35,
            command=lambda: self.app.show_key_screen(),
            fg_color="#7C3AED", hover_color="#6D28D9",
            font=("Arial", 16)
        ).pack(side="right", padx=5)

        # ============================================================
        # MAIN CONTENT: Left panel (50%) + Right panel (50%)
        # ============================================================
        content_frame = ctk.CTkFrame(self, fg_color="transparent")
        content_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
        content_frame.grid_columnconfigure(0, weight=1, uniform="half")
        content_frame.grid_columnconfigure(1, weight=1, uniform="half")
        content_frame.grid_rowconfigure(0, weight=1)

        # ============================
        # LEFT PANEL
        # ============================
        left_panel = ctk.CTkFrame(content_frame)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        left_panel.grid_columnconfigure(0, weight=1)
        left_panel.grid_rowconfigure(0, weight=0)  # Tên dự án & Cấu hình (chung 1 khung)
        left_panel.grid_rowconfigure(1, weight=0)  # Mode buttons (FILE / URL)
        left_panel.grid_rowconfigure(2, weight=0, minsize=140)  # Drop zone
        left_panel.grid_rowconfigure(3, weight=0)  # Process button
        left_panel.grid_rowconfigure(4, weight=0)  # Header "Danh sách"
        left_panel.grid_rowconfigure(5, weight=1, minsize=140)  # Job list frame
        left_panel.grid_rowconfigure(6, weight=0)  # Bottom-left bar with Settings gear

        # --- 0. TÊN DỰ ÁN & CẤU HÌNH (Chung một nơi và một div) ---
        self.project_config_frame = ctk.CTkFrame(left_panel, fg_color=("gray90", "gray17"), border_width=1, border_color="#374151")
        self.project_config_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))

        # Dòng 1: Tên dự án
        project_row = ctk.CTkFrame(self.project_config_frame, fg_color="transparent")
        project_row.pack(fill="x", padx=6, pady=(6, 4))
        ctk.CTkLabel(project_row, text="Tên dự án:", font=("Arial", 13, "bold")).pack(side="left", padx=(2, 6))
        self.address_entry = ctk.CTkEntry(project_row, placeholder_text="Nhập tên dự án...", height=30, font=("Arial", 13))
        self.address_entry.pack(side="left", fill="x", expand=True, padx=2)
        self.address_entry.insert(0, cache.get("address", "Demo Project"))
        self.address_entry.bind("<FocusOut>", lambda e: cache.set("address", self.address_entry.get().strip()))
        self.address_entry.bind("<KeyRelease>", lambda e: cache.set("address", self.address_entry.get().strip()))

        # Dòng 2: Cấu hình
        config_container = ctk.CTkFrame(self.project_config_frame, fg_color="transparent")
        config_container.pack(fill="x", padx=6, pady=(2, 6))

        # Cấu hình AutoHDR (Style & Model)
        self.config_autohdr_frame = ctk.CTkFrame(config_container, fg_color="transparent")
        self.config_autohdr_frame.pack(fill="x", expand=True)

        ctk.CTkLabel(self.config_autohdr_frame, text="Cấu hình:", font=("Arial", 13, "bold")).pack(side="left", padx=(2, 4))
        ctk.CTkLabel(self.config_autohdr_frame, text="Style:", font=("Arial", 12)).pack(side="left", padx=(2, 4))

        cached_category = cache.get("model_category", CATEGORY_INDOOR)
        if cached_category not in CATEGORIES:
            cached_category = CATEGORY_INDOOR
        self.category_var = ctk.StringVar(value=cached_category)

        self.category_selector = ctk.CTkSegmentedButton(
            self.config_autohdr_frame,
            values=CATEGORIES,
            variable=self.category_var,
            command=self._on_category_changed,
            height=28,
            font=("Arial", 11, "bold"),
        )
        self.category_selector.pack(side="left", padx=(0, 8))

        ctk.CTkLabel(self.config_autohdr_frame, text="Model:", font=("Arial", 12)).pack(side="left", padx=(2, 4))

        initial_names = get_model_names_by_category(cached_category)
        cached_model = cache.get("model_name", "Lisa" if cached_category == CATEGORY_INDOOR else (initial_names[0] if initial_names else ""))
        if cached_model not in initial_names and initial_names:
            cached_model = initial_names[0]

        self.model_var = ctk.StringVar(value=cached_model)
        self.model_selector = ctk.CTkComboBox(
            self.config_autohdr_frame,
            values=initial_names,
            variable=self.model_var,
            command=self._on_model_changed,
            state="readonly",
            height=28,
            font=("Arial", 12),
        )
        self.model_selector.pack(side="left", fill="x", expand=True, padx=2)

        # Cấu hình Autoenhance (Preset, Sky, Checkboxes)
        self.config_autoenhance_frame = ctk.CTkFrame(config_container, fg_color="transparent")

        ae_row1 = ctk.CTkFrame(self.config_autoenhance_frame, fg_color="transparent")
        ae_row1.pack(fill="x", expand=True, pady=(0, 4))

        ctk.CTkLabel(ae_row1, text="Cấu hình:", font=("Arial", 13, "bold")).pack(side="left", padx=(2, 4))
        ctk.CTkLabel(ae_row1, text="Preset:", font=("Arial", 12)).pack(side="left", padx=(2, 4))

        cached_ae_preset = cache.get("ae_preset", "real_estate")
        self.ae_preset_var = ctk.StringVar(value=cached_ae_preset)
        self.ae_preset_selector = ctk.CTkComboBox(
            ae_row1,
            values=["real_estate", "default", "exterior"],
            variable=self.ae_preset_var,
            command=lambda val: cache.set("ae_preset", val),
            state="readonly",
            height=28,
            width=110,
            font=("Arial", 11),
        )
        self.ae_preset_selector.pack(side="left", padx=(0, 6))

        ctk.CTkLabel(ae_row1, text="Sky:", font=("Arial", 12)).pack(side="left", padx=(2, 4))
        cached_ae_sky = cache.get("ae_sky", "auto")
        self.ae_sky_var = ctk.StringVar(value=cached_ae_sky)
        self.ae_sky_selector = ctk.CTkComboBox(
            ae_row1,
            values=["auto", "sunny", "clear_blue", "none"],
            variable=self.ae_sky_var,
            command=lambda val: cache.set("ae_sky", val),
            state="readonly",
            height=28,
            width=100,
            font=("Arial", 11),
        )
        self.ae_sky_selector.pack(side="left", fill="x", expand=True, padx=2)

        ae_row2 = ctk.CTkFrame(self.config_autoenhance_frame, fg_color="transparent")
        ae_row2.pack(fill="x", expand=True)

        cached_ae_vertical = cache.get("ae_vertical", True)
        self.ae_vertical_var = ctk.BooleanVar(value=bool(cached_ae_vertical))
        self.ae_vertical_chk = ctk.CTkCheckBox(
            ae_row2, text="Cân thẳng phối cảnh (Vertical)", variable=self.ae_vertical_var,
            command=lambda: cache.set("ae_vertical", self.ae_vertical_var.get()),
            font=("Arial", 11), height=20, checkbox_width=18, checkbox_height=18
        )
        self.ae_vertical_chk.pack(side="left", padx=(4, 10))

        cached_ae_lens = cache.get("ae_lens", True)
        self.ae_lens_var = ctk.BooleanVar(value=bool(cached_ae_lens))
        self.ae_lens_chk = ctk.CTkCheckBox(
            ae_row2, text="Sửa méo ống kính (Lens)", variable=self.ae_lens_var,
            command=lambda: cache.set("ae_lens", self.ae_lens_var.get()),
            font=("Arial", 11), height=20, checkbox_width=18, checkbox_height=18
        )
        self.ae_lens_chk.pack(side="left", padx=4)

        # --- 1. Mode buttons (FILE vs URL) ---
        mode_buttons_frame = ctk.CTkFrame(left_panel, fg_color="transparent")
        mode_buttons_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(4, 4))

        self.btn_manual = ctk.CTkButton(
            mode_buttons_frame, text="URL", width=75, height=28,
            command=lambda: self._set_manual_mode(True),
            fg_color="#6B7280", hover_color="#4B5563",
            font=("Arial", 12, "bold")
        )
        self.btn_manual.pack(side="right", padx=3)

        self.btn_auto = ctk.CTkButton(
            mode_buttons_frame, text="FILE", width=75, height=28,
            command=lambda: self._set_manual_mode(False),
            fg_color="#059669", hover_color="#047857",
            font=("Arial", 12, "bold")
        )
        self.btn_auto.pack(side="right", padx=3)

        # --- 2. Drop Zone (Nằm dưới Cấu hình và Tên dự án) ---
        drop_container = ctk.CTkFrame(left_panel, fg_color="transparent")
        drop_container.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 6))
        drop_container.grid_rowconfigure(0, weight=1)
        drop_container.grid_columnconfigure(0, weight=1)

        self.drop_frame = ctk.CTkFrame(
            drop_container,
            border_width=2,
            border_color="#6B7280",
            fg_color=("gray95", "gray15"),
        )
        self.drop_frame.grid(row=0, column=0, sticky="nsew")
        self.drop_frame.bind("<Button-1>", lambda e: self._select_images())

        self.drop_label = ctk.CTkLabel(
            self.drop_frame,
            text="📁 Kéo thả Ảnh / Thư mục vào đây\n(Hoặc click để chọn)",
            font=("Arial", 15, "bold"),
        )
        self.drop_label.pack(expand=True, pady=20)
        self.drop_label.bind("<Button-1>", lambda e: self._select_images())

        for widget in [drop_container, self.drop_frame, self.drop_label]:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind('<<Drop>>', self._on_drop)

        self.img_count_label = ctk.CTkLabel(drop_container, text="0 files đã chọn", font=("Arial", 14, "bold"))
        self.img_count_label.grid(row=1, column=0, sticky="w", padx=5, pady=3)

        self.share_url_entry = ctk.CTkEntry(drop_container, placeholder_text="Dán link dự án...", height=34, font=("Arial", 13))
        self.share_url_entry.bind("<KeyRelease>", lambda e: self.url_status_label.configure(text=""))
        self.url_status_label = ctk.CTkLabel(
            drop_container,
            text="",
            font=("Arial", 12),
            text_color="#EF4444",
            wraplength=350,
            justify="left",
        )

        # --- 3. Process Button ---
        controls_frame = ctk.CTkFrame(left_panel, fg_color="transparent")
        controls_frame.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 6))
        controls_frame.grid_columnconfigure(0, weight=1)

        self.btn_process = ctk.CTkButton(
            controls_frame,
            text="BẮT ĐẦU XỬ LÝ",
            command=self._start_process_async,
            fg_color="#16A34A",
            hover_color="#15803D",
            height=40,
            font=("Arial", 16, "bold"),
        )
        self.btn_process.grid(row=0, column=0, sticky="ew")

        # --- 4. Danh Sách (Job Header) ---
        job_header = ctk.CTkFrame(left_panel, fg_color="transparent")
        job_header.grid(row=4, column=0, sticky="ew", padx=8, pady=(4, 2))
        ctk.CTkLabel(job_header, text="Danh sách", font=("Arial", 16, "bold")).pack(side="left", anchor="w")

        self.btn_clear_jobs = ctk.CTkButton(
            job_header, text="Clear", width=65, height=26,
            command=self._clear_job_list,
            fg_color="#DC2626", hover_color="#B91C1C",
            font=("Arial", 12, "bold")
        )
        self.btn_clear_jobs.pack(side="right", padx=3)

        self.btn_scan_failed = ctk.CTkButton(
            job_header, text="Quét job lỗi", width=85, height=26,
            command=self._scan_recoverable_jobs,
            fg_color="#4B5563", hover_color="#374151",
            font=("Arial", 11, "bold")
        )
        self.btn_scan_failed.pack(side="right", padx=3)

        # --- 5. Job list frame ---
        self.job_list_frame = ctk.CTkScrollableFrame(left_panel, label_text="", height=140)
        self.job_list_frame.grid(row=5, column=0, sticky="nsew", padx=8, pady=(2, 4))
        self.job_list_frame.bind_all("<Button-4>", lambda e: self._on_mousewheel(e, self.job_list_frame))
        self.job_list_frame.bind_all("<Button-5>", lambda e: self._on_mousewheel(e, self.job_list_frame))

        # --- 6. Bottom Bar: Settings Gear Icon (Góc trái dưới màn hình) ---
        bottom_bar = ctk.CTkFrame(left_panel, fg_color="transparent")
        bottom_bar.grid(row=6, column=0, sticky="ew", padx=8, pady=(4, 6))

        self.btn_settings = ctk.CTkButton(
            bottom_bar,
            text="⚙",
            width=36,
            height=30,
            font=("Arial", 16),
            command=self._open_settings_dialog,
            fg_color="#374151",
            hover_color="#4B5563",
            cursor="hand2"
        )
        self.btn_settings.pack(side="left")

        # ============================
        # RIGHT PANEL — Per-Job Log & Progress
        # ============================
        right_panel = ctk.CTkFrame(content_frame)
        right_panel.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        right_panel.grid_columnconfigure(0, weight=1)
        right_panel.grid_rowconfigure(3, weight=1)

        # Row 0: Header with Job ID & Action buttons
        log_header = ctk.CTkFrame(right_panel, fg_color="transparent")
        log_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 0))

        self.log_title_label = ctk.CTkLabel(
            log_header, text="Logs", font=("Arial", 18, "bold"))
        self.log_title_label.pack(side="left")

        self.btn_clear_log = ctk.CTkButton(
            log_header, text="Clear", width=70, height=30,
            command=self._clear_selected_job_log,
            fg_color="#DC2626", hover_color="#B91C1C",
            font=("Arial", 13, "bold"),
        )
        self.btn_clear_log.pack(side="right", padx=(4, 0))

        self.btn_stop_job = ctk.CTkButton(
            log_header, text="⏹ Dừng", width=70, height=30,
            command=self._stop_selected_job,
            fg_color="#B91C1C", hover_color="#991B1B",
            font=("Arial", 13, "bold"),
        )
        # Initially hidden, only shown when selected job is running!

        self.btn_restart_job = ctk.CTkButton(
            log_header, text="🔄 Chạy lại", width=95, height=30,
            command=self._resume_selected_job,
            fg_color="#F59E0B", hover_color="#D97706",
            font=("Arial", 13, "bold"),
        )
        # Initially hidden, only shown when selected job is failed or stopped!

        self.btn_open_folder = ctk.CTkButton(
            log_header, text="📂 Mở thư mục", width=105, height=30,
            command=lambda: self._open_selected_job_folder(),
            fg_color="#4B5563", hover_color="#374151",
            font=("Arial", 13, "bold"),
        )
        self.btn_open_folder.pack(side="right", padx=(4, 4))

        # Row 1: Progress step & percentage
        self.progress_frame = ctk.CTkFrame(right_panel, fg_color="transparent")
        self.progress_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(4, 2))

        self.step_label = ctk.CTkLabel(self.progress_frame, text="", font=("Arial", 12, "bold"))
        self.step_label.pack(side="left", anchor="w")

        self.progress_pct_label = ctk.CTkLabel(self.progress_frame, text="", font=("Arial", 12, "bold"), text_color="#3B82F6")
        self.progress_pct_label.pack(side="right", anchor="e")

        # Row 2: Progress bar
        self.progress_bar = ctk.CTkProgressBar(right_panel, height=8)
        self.progress_bar.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 6))
        self.progress_bar.set(0)

        # Row 3: Log textbox
        self.log_textbox = ctk.CTkTextbox(right_panel, font=("Consolas", 13))
        self.log_textbox.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.log_textbox.configure(state="disabled")

        # Bind mouse wheel for Linux on log textbox
        self.log_textbox.bind("<Button-4>", lambda e: self.log_textbox.yview_scroll(-1, "units"))
        self.log_textbox.bind("<Button-5>", lambda e: self.log_textbox.yview_scroll(1, "units"))

        # Khôi phục trạng thái Web và Mode từ Cache
        if self.current_web == "autoenhance":
            self._on_web_changed("Autoenhance")
        if self.is_manual_mode:
            self._set_manual_mode(True)

    # ==================================================
    # Account & Session Management (Modal Dialog)
    # ==================================================

    def _open_accounts_dialog(self, initial_engine: str = "autohdr"):
        """Mở modal quản lý tài khoản Cookie và API Key."""
        AccountsDialog(self, initial_engine=initial_engine, on_change_callback=self._on_account_changed)

    def _on_account_changed(self, engine: str, record: Optional[BaseSessionRecord]):
        """Callback khi tài khoản hoặc API key thay đổi từ AccountsDialog."""
        if engine == "autohdr":
            if record:
                from models.schemas import SessionRecord
                self.current_session = SessionRecord(
                    cookie=getattr(record, "cookie", ""),
                    email=getattr(record, "email", ""),
                    user_id=getattr(record, "user_id", ""),
                    firstname=getattr(record, "firstname", ""),
                    lastname=getattr(record, "lastname", ""),
                    expires=getattr(record, "expires", ""),
                )
                self.email_label.configure(text=f"🟢 {record.email}", text_color="#22C55E")
                cache.set("email", record.email)
                cache.set("cookie", getattr(record, "cookie", ""))
                cache.set("user_id", getattr(record, "user_id", ""))
                cache.set("firstname", getattr(record, "firstname", ""))
                cache.set("lastname", getattr(record, "lastname", ""))
                cache.set("expires", getattr(record, "expires", ""))
            else:
                self.current_session = None
                self.email_label.configure(text="⚪ Chưa đăng nhập", text_color="gray")
                cache.set("email", "")
                cache.set("cookie", "")
        elif engine == "autoenhance":
            if record:
                self.current_ae_session = record
                self.autoenhance_status_label.configure(
                    text=f"🟢 {record.display_name}", text_color="#22C55E"
                )
            else:
                self.current_ae_session = None
                self.autoenhance_status_label.configure(
                    text="⚪ Chưa có API Key", text_color="gray"
                )

    # ==================================================
    # File Selection & Drag-Drop
    # ==================================================

    def _on_drop(self, event):
        """Handle Drag and Drop event (Replace Mode)."""
        # Split paths correctly
        raw_data = event.data
        paths = self.master.tk.splitlist(raw_data)

        # Mode: Replace (Không cộng dồn theo yêu cầu)
        self.selected_files = []
        added_count = 0
        
        for path in paths:
            clean_path = path.strip('{}')
            added_count += self._process_path(clean_path)

        self.img_count_label.configure(text=f"{len(self.selected_files)} files đã chọn")

    def _process_path(self, path):
        """Recursively process file or folder path."""
        added = 0
        if os.path.isfile(path):
            ext = os.path.splitext(path)[1].lower()
            if ext in self.allowed_extensions:
                if path not in self.selected_files:
                    self.selected_files.append(path)
                    added = 1
        elif os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                for f in files:
                    f_path = os.path.join(root, f)
                    ext = os.path.splitext(f_path)[1].lower()
                    if ext in self.allowed_extensions:
                        if f_path not in self.selected_files:
                            self.selected_files.append(f_path)
                            added += 1
        return added

    def _select_images(self):
        """Open native file dialog to select images."""
        if os.name == 'posix' and platform.system() == 'Linux':
            try:
                # Try zenity which is the native GNOME/GTK dialog on Linux
                result = subprocess.run(
                    ['zenity', '--file-selection', '--multiple', 
                     '--title=Chọn hình ảnh', 
                     '--file-filter=Image files | *.jpg *.jpeg *.png *.webp',
                     '--separator=|'], 
                    capture_output=True, text=True
                )
                if result.returncode == 0 and result.stdout.strip():
                    files = result.stdout.strip().split('|')
                    for f in files:
                        if f not in self.selected_files and f.strip():
                            self.selected_files.append(f.strip())
                    self.img_count_label.configure(text=f"{len(self.selected_files)} files đã chọn")
                return # Exit early out of fallback
            except Exception:
                pass # Fallback to tkinter
                
        # Windows / Fallback
        file_types = [("Image files", "*.jpg *.jpeg *.png *.webp"), ("All files", "*.*")]
        files = filedialog.askopenfilenames(title="Chọn hình ảnh", filetypes=file_types)
        if files:
            # Append to existing list
            for f in files:
                if f not in self.selected_files:
                    self.selected_files.append(f)
            self.img_count_label.configure(text=f"{len(self.selected_files)} files đã chọn")

    def _select_dir(self):
        """Select download directory."""
        if os.name == 'posix' and platform.system() == 'Linux':
            try:
                result = subprocess.run(
                    ['zenity', '--file-selection', '--directory', '--title=Chọn thư mục lưu ảnh'], 
                    capture_output=True, text=True
                )
                if result.returncode == 0 and result.stdout.strip():
                    dir_path = result.stdout.strip()
                    self.download_dir = dir_path
                    cache.set("download_dir", dir_path)
                    try:
                        self.dir_label.configure(text=f"📁 {dir_path}")
                    except Exception:
                        pass
                return # Exit early out of fallback
            except Exception:
                pass # Fallback to tkinter
                
        dir_path = filedialog.askdirectory(title="Chọn thư mục lưu ảnh")
        if dir_path:
            self.download_dir = dir_path
            cache.set("download_dir", dir_path)
            try:
                self.dir_label.configure(text=f"📁 {dir_path}")
            except Exception:
                pass

    # ==================================================
    # Web & Settings Handlers
    # ==================================================

    def _on_web_changed(self, new_web: str):
        """Chuyển đổi giữa AutoHDR và Autoenhance."""
        self.current_web = new_web.strip().lower()
        cache.set("selected_web", new_web)
        if hasattr(self, "url_status_label"):
            self.url_status_label.configure(text="")
        if self.current_web == "autohdr":
            self.autoenhance_auth_frame.pack_forget()
            self.autohdr_auth_frame.pack(side="left", fill="x", expand=True)
            self.config_autoenhance_frame.pack_forget()
            self.config_autohdr_frame.pack(fill="x", expand=True)
        else:
            self.autohdr_auth_frame.pack_forget()
            self.autoenhance_auth_frame.pack(side="left", fill="x", expand=True)
            self.config_autohdr_frame.pack_forget()
            self.config_autoenhance_frame.pack(fill="x", expand=True)

        self._filter_job_list_by_web()

    def _open_settings_dialog(self):
        """Mở modal cài đặt để cấu hình thư mục đầu ra."""
        SettingsDialog(self, self.download_dir, on_save_callback=self._on_download_dir_changed)

    def _on_download_dir_changed(self, new_dir: str):
        """Callback khi người dùng lưu thư mục đầu ra trong modal cài đặt."""
        self.download_dir = new_dir
        cache.set("download_dir", new_dir)

    def _filter_job_list_by_web(self):
        """Lọc danh sách job theo Web được chọn (AutoHDR hoặc Autoenhance)."""
        first_visible = None
        for jid, widget in self.job_widgets.items():
            engine = self.job_engine_map.get(jid, "autohdr")
            if engine == self.current_web:
                widget.pack(fill="x", pady=2, padx=2)
                if not first_visible:
                    first_visible = jid
            else:
                widget.pack_forget()

        # Nếu job đang xem không thuộc web này, chuyển sang job đầu tiên của web hiện tại
        if self.selected_job_id and self.job_engine_map.get(self.selected_job_id) != self.current_web:
            if first_visible:
                self._select_job(first_visible)
            else:
                self.selected_job_id = None
                self.log_title_label.configure(text="Logs")
                self.step_label.configure(text="")
                self.progress_pct_label.configure(text="")
                self.progress_bar.set(0)
                self.btn_stop_job.pack_forget()
                self.btn_restart_job.pack_forget()
                self.log_textbox.configure(state="normal")
                self.log_textbox.delete("1.0", "end")
                self.log_textbox.configure(state="disabled")

    # ==================================================
    # Process Pipeline & Job Execution
    # ==================================================

    def _start_process_async(self):
        """Kiểm tra và kích hoạt Job qua JobRunner trên Background Thread."""
        if getattr(self, 'is_manual_mode', False):
            return self._start_manual_process_async()

        if not self.selected_files:
            return

        address = self.address_entry.get().strip() or "Untitled Project"

        # Kiểm tra xác thực tương ứng từng Web
        if self.current_web == "autohdr":
            if not self.current_session:
                self.email_label.configure(text="Cần đăng nhập trước", text_color="#EF4444")
                return
        elif self.current_web == "autoenhance":
            if not self.current_ae_session:
                self.autoenhance_status_label.configure(text="Cần lưu API key trước", text_color="#EF4444")
                return

        # Trạng thái loading
        self.btn_process.configure(state="disabled", text="⏳ Đang kiểm tra key...")
        self.update_idletasks()

        def _do_check_and_start():
            if not self.api.check_key():
                self.after(0, lambda: self.btn_process.configure(state="normal", text="BẮT ĐẦU XỬ LÝ"))
                self.after(0, lambda: self.app.show_key_screen(self.api.last_check_status))
                return

            self.after(0, lambda: self._create_and_start_job(address))

        threading.Thread(target=_do_check_and_start, daemon=True).start()

    def _set_manual_mode(self, manual: bool):
        """Chuyển đổi giữa chế độ FILE và URL."""
        self.is_manual_mode = manual
        cache.set("is_manual_mode", manual)
        if manual:
            try:
                self.drop_frame.grid_remove()
            except Exception:
                pass
            self.share_url_entry.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
            self.url_status_label.grid(row=1, column=0, sticky="w", padx=8, pady=(0, 4))
            self.img_count_label.grid_remove()
            self.btn_manual.configure(fg_color="#059669")
            self.btn_auto.configure(fg_color="#6B7280")
        else:
            try:
                self.share_url_entry.grid_remove()
                self.url_status_label.grid_remove()
            except Exception:
                pass
            self.drop_frame.grid(row=0, column=0, sticky="nsew")
            self.img_count_label.grid(row=1, column=0, sticky="w", padx=5, pady=3)
            self.btn_manual.configure(fg_color="#6B7280")
            self.btn_auto.configure(fg_color="#059669")

    def _start_manual_process_async(self):
        """Khởi chạy xử lý URL chia sẻ theo Dispatcher Pattern (Không tự detect)."""
        raw_input = self.share_url_entry.get().strip()
        if not raw_input:
            self.url_status_label.configure(text="⚠️ Vui lòng dán link dự án", text_color="#EF4444")
            return

        address = self.address_entry.get().strip() or "Manual Project"

        # Tra cứu Handler tương ứng với Dropdown đang chọn
        handlers = {
            "autoenhance": self._handle_url_autoenhance,
            "autohdr": self._handle_url_autohdr,
        }

        handler = handlers.get(self.current_web)
        if not handler:
            return

        handler(raw_input, address)

    def _handle_url_autoenhance(self, raw_url: str, address: str):
        """Xử lý URL Autoenhance khi dropdown đang chọn Autoenhance."""
        self.url_status_label.configure(text="")
        valid, order_id, err = parse_autoenhance_url(raw_url)
        if not valid:
            self.url_status_label.configure(text=f"⚠️ {err}", text_color="#EF4444")
            return

        if not self.current_ae_session or not getattr(self.current_ae_session, "api_key", ""):
            self.url_status_label.configure(text="⚠️ Cần lưu API key Autoenhance trước khi tải", text_color="#EF4444")
            return

        self.btn_process.configure(state="disabled", text="⏳ Đang kiểm tra key...")
        self.update_idletasks()

        def _do_check_ae():
            if not self.api.check_key():
                self.after(0, lambda: self.btn_process.configure(state="normal", text="BẮT ĐẦU XỬ LÝ"))
                self.after(0, lambda: self.app.show_key_screen(self.api.last_check_status))
                return
            self.after(0, lambda: self._create_and_start_ae_download_job(address, order_id))

        threading.Thread(target=_do_check_ae, daemon=True).start()

    def _handle_url_autohdr(self, raw_url: str, address: str):
        """Xử lý URL AutoHDR khi dropdown đang chọn AutoHDR."""
        self.url_status_label.configure(text="")
        valid, photoshoot_uuid, err = parse_autohdr_url(raw_url)
        if not valid:
            self.url_status_label.configure(text=f"⚠️ {err}", text_color="#EF4444")
            return

        if not self.current_session:
            self.url_status_label.configure(text="⚠️ Cần đăng nhập AutoHDR trước khi tải", text_color="#EF4444")
            return

        self.btn_process.configure(state="disabled", text="⏳ Đang kiểm tra key...")
        self.update_idletasks()

        def _do_check_hdr():
            if not self.api.check_key():
                self.after(0, lambda: self.btn_process.configure(state="normal", text="BẮT ĐẦU XỬ LÝ"))
                self.after(0, lambda: self.app.show_key_screen(self.api.last_check_status))
                return
            self.after(0, lambda: self._create_and_start_manual_job(address, photoshoot_uuid))

        threading.Thread(target=_do_check_hdr, daemon=True).start()

    def _create_and_start_ae_download_job(self, address: str, order_id: str):
        """Khởi chạy Autoenhance Cloud Download Job qua JobRunner."""
        cache.set("address", address)
        download_dir = getattr(self, 'download_dir', '').strip() or os.path.expanduser("~/Downloads")
        api_key = self.current_ae_session.api_key if self.current_ae_session else ""

        ctx = self.job_runner.submit_job(
            engine="autoenhance_download",
            file_paths=[],
            output_dir=download_dir,
            credentials={"api_key": api_key},
            preferences={"order_id": order_id, "address": address},
            ui_callback_log=lambda lvl, msg: self.after(0, lambda l=lvl, m=msg: self._on_job_log(ctx.job_id, f"[{l}] {m}")),
            ui_callback_progress=lambda step, desc, pct: self.after(0, lambda s=step, d=desc, p=pct: self._on_job_progress(ctx.job_id, s, d, p)),
            ui_callback_finished=lambda c: self.after(0, lambda fin_c=c: self._on_job_finished(fin_c)),
        )

        display_name = address if address and address != "Manual Photoshoot" else f"Cloud: {order_id[:8]}"
        self._add_job_to_list(
            job_or_id=ctx.job_id,
            address=display_name,
            file_count=0,
            engine="autoenhance",
            status="running",
        )
        self._select_job(ctx.job_id)
        self.btn_process.configure(state="normal", text="BẮT ĐẦU XỬ LÝ")

    def _create_and_start_manual_job(self, address: str, photoshoot_uuid: str):
        """Khởi chạy manual photoshoot share job."""
        cache.set("address", address)
        download_dir = getattr(self, 'download_dir', '').strip() or os.path.expanduser("~/Downloads")

        job = self.pipeline_mgr.run_pipeline_share(
            session=self.current_session,
            photoshoot_uuid=photoshoot_uuid,
            address=address,
            download_dir=download_dir,
            on_log=lambda jid, msg: self.after(0, lambda j=jid, m=msg: self._on_job_log(j, m)),
            on_job_update=lambda j: self.after(0, lambda: self._refresh_job_list()),
            proxy_config={},
        )

        self._add_job_to_list(job)
        self._select_job(job.job_id)
        self.btn_process.configure(state="normal", text="BẮT ĐẦU XỬ LÝ")

    def _on_category_changed(self, new_category: str):
        """Xử lý thay đổi Style (Indoor, Outdoor, Both)."""
        cache.set("model_category", new_category)
        names = get_model_names_by_category(new_category)
        self.model_selector.configure(values=names)

        cached_for_cat = cache.get(f"model_name_{new_category}")
        if cached_for_cat and cached_for_cat in names:
            selected = cached_for_cat
        else:
            if new_category == CATEGORY_INDOOR and "Lisa" in names:
                selected = "Lisa"
            elif names:
                selected = names[0]
            else:
                selected = ""

        self.model_var.set(selected)
        cache.set("model_name", selected)

    def _on_model_changed(self, new_model_name: str):
        """Xử lý thay đổi Model."""
        cache.set("model_name", new_model_name)
        cat = self.category_var.get()
        cache.set(f"model_name_{cat}", new_model_name)

    def _create_and_start_job(self, address: str):
        """Khởi tạo Job mới và kích hoạt qua JobRunner."""
        cache.set("address", address)
        download_dir = getattr(self, 'download_dir', '').strip() or os.path.expanduser("~/Downloads")

        if self.current_web == "autohdr":
            category = self.category_var.get()
            model_name = self.model_var.get()
            model = get_model_by_name_and_category(model_name, category)
            model_id = model["id"] if model else (3 if category == CATEGORY_INDOOR else 1)
            indoor_model_id, outdoor_model_id = get_payload_model_ids(category, model_id)

            credentials = {"cookie": self.current_session.cookie if self.current_session else ""}
            preferences = {
                "category": category,
                "model_name": model_name,
                "indoor_model_id": indoor_model_id,
                "outdoor_model_id": outdoor_model_id,
                "address": address,
            }
        else:  # autoenhance
            credentials = {"api_key": self.current_ae_session.api_key if self.current_ae_session else ""}
            preferences = {
                "preset": self.ae_preset_var.get(),
                "sky_replacement": self.ae_sky_var.get(),
                "vertical_correction": self.ae_vertical_var.get(),
                "lens_correction": self.ae_lens_var.get(),
                "address": address,
            }
            cache.set("ae_preset", self.ae_preset_var.get())
            cache.set("ae_sky", self.ae_sky_var.get())
            cache.set("ae_vertical", self.ae_vertical_var.get())
            cache.set("ae_lens", self.ae_lens_var.get())

        ctx = self.job_runner.submit_job(
            engine=self.current_web,
            file_paths=self.selected_files.copy(),
            output_dir=download_dir,
            credentials=credentials,
            preferences=preferences,
            ui_callback_log=lambda lvl, msg: self.after(0, lambda l=lvl, m=msg: self._on_job_log(ctx.job_id, f"[{l}] {m}")),
            ui_callback_progress=lambda step, desc, pct: self.after(0, lambda s=step, d=desc, p=pct: self._on_job_progress(ctx.job_id, s, d, p)),
            ui_callback_finished=lambda c: self.after(0, lambda fin_c=c: self._on_job_finished(fin_c)),
        )

        self._add_job_to_list(
            job_or_id=ctx.job_id,
            address=address,
            file_count=len(ctx.file_paths),
            engine=self.current_web,
            status="running",
        )
        self._select_job(ctx.job_id)

        self.selected_files = []
        self.img_count_label.configure(text="0 files đã chọn")
        self.btn_process.configure(state="normal", text="BẮT ĐẦU XỬ LÝ")

    def _add_job_to_list(self, job_or_id, address: str = "", file_count: int = 0, engine: str = "autohdr", status: str = "running"):
        """Thêm job vào danh sách UI (không hiển thị nhãn web thừa)."""
        if isinstance(job_or_id, Job):
            job_id = job_or_id.job_id
            address = job_or_id.address
            file_count = job_or_id.file_count
            engine = "autohdr"
            status = job_or_id.status
        else:
            job_id = str(job_or_id)

        job_row = ctk.CTkFrame(self.job_list_frame, cursor="hand2")
        job_row.pack(fill="x", pady=2, padx=2)
        job_row.job_id = job_id

        job_row.bind("<Button-1>", lambda e, jid=job_id: self._select_job(jid))

        info_frame = ctk.CTkFrame(job_row, fg_color="transparent")
        info_frame.pack(side="left", fill="x", expand=True, padx=5, pady=3)
        info_frame.bind("<Button-1>", lambda e, jid=job_id: self._select_job(jid))

        name_label = ctk.CTkLabel(
            info_frame,
            text=f"Job {job_id} | {address[:25]}",
            font=("Arial", 14, "bold"),
        )
        name_label.pack(anchor="w")
        name_label.bind("<Button-1>", lambda e, jid=job_id: self._select_job(jid))

        status_text = f"{file_count} ảnh | Đang xử lý..."
        if status == "completed":
            status_text = f"{file_count} ảnh | Hoàn thành"
        elif status == "failed":
            status_text = f"{file_count} ảnh | Lỗi"
        elif status in ("stopped", "cancelled"):
            status_text = f"{file_count} ảnh | Đã dừng"

        status_label = ctk.CTkLabel(
            info_frame,
            text=status_text,
            font=("Arial", 12),
            text_color="#9CA3AF" if status in ("running", "processing") else ("#22C55E" if status == "completed" else "#EF4444"),
        )
        status_label.pack(anchor="w")
        status_label.bind("<Button-1>", lambda e, jid=job_id: self._select_job(jid))
        job_row.status_label = status_label

        # Nút Dừng — CHỈ HIỆN KHI JOB ĐANG CHẠY!
        stop_btn = ctk.CTkButton(
            job_row, text="⏹", width=36, height=28,
            command=lambda jid=job_id: self._stop_job(jid),
            fg_color="#DC2626", hover_color="#B91C1C",
            font=("Arial", 12, "bold")
        )
        job_row.stop_btn = stop_btn

        # Nút Chạy lại — HIỆN KHI JOB BỊ LỖI HOẶC BỊ DỪNG
        restart_btn = ctk.CTkButton(
            job_row, text="🔄", width=36, height=28,
            command=lambda jid=job_id: self._resume_job(jid),
            fg_color="#F59E0B", hover_color="#D97706",
            font=("Arial", 12, "bold")
        )
        job_row.restart_btn = restart_btn

        # Nút Mở thư mục — hiện khi hoàn thành
        folder_btn = ctk.CTkButton(
            job_row, text="📂", width=36, height=28,
            command=lambda jid=job_id: self._open_job_folder(jid),
            fg_color="#6B7280", hover_color="#4B5563",
            font=("Arial", 12)
        )
        job_row.folder_btn = folder_btn

        if status in ("running", "processing"):
            stop_btn.pack(side="right", padx=5, pady=3)
        elif status == "completed":
            folder_btn.pack(side="right", padx=5, pady=3)
        elif status in ("failed", "cancelled", "stopped"):
            restart_btn.pack(side="right", padx=5, pady=3)

        self.job_widgets[job_id] = job_row
        self.job_engine_map[job_id] = engine

        try:
            self.job_list_frame._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _select_job(self, job_id: str):
        """Chọn job để hiển thị log và tiến độ (ẩn nút dừng nếu job đã xong)."""
        self.selected_job_id = job_id

        for jid, widget in self.job_widgets.items():
            if jid == job_id:
                widget.configure(border_width=2, border_color="#3B82F6")
            else:
                widget.configure(border_width=0)

        # Kiểm tra JobRunner
        ctx = self.job_runner.get_job(job_id)
        if ctx:
            self.log_title_label.configure(text=f"Job {job_id}")

            # Nút điều khiển ở Header
            if ctx.status == "running":
                self.btn_stop_job.pack(side="right", padx=(4, 0))
                self.btn_restart_job.pack_forget()
            elif ctx.status in ("failed", "cancelled", "stopped"):
                self.btn_stop_job.pack_forget()
                self.btn_restart_job.pack(side="right", padx=(4, 0))
            else:
                self.btn_stop_job.pack_forget()
                self.btn_restart_job.pack_forget()

            if ctx.status == "completed":
                self.step_label.configure(text=f"Hoàn thành ({ctx.duration_seconds}s)")
                self.progress_pct_label.configure(text="100%", text_color="#22C55E")
                self.progress_bar.set(1.0)
            elif ctx.status == "failed":
                self.step_label.configure(text=f"Lỗi: {ctx.error_message}")
                self.progress_pct_label.configure(text="Lỗi", text_color="#EF4444")
            elif ctx.status in ("cancelled", "stopped"):
                self.step_label.configure(text="Tác vụ đã dừng")
                self.progress_pct_label.configure(text="Đã dừng", text_color="#F59E0B")
            else:
                desc = ctx.current_step if isinstance(ctx.current_step, str) else ""
                self.step_label.configure(text=desc)

            self._display_job_logs(job_id)
            return

        # Legacy pipeline job
        job = self.pipeline_mgr.get_job(job_id)
        if job:
            self.log_title_label.configure(text=f"Job {job_id}")
            if job.status == "processing":
                self.btn_stop_job.pack(side="right", padx=(4, 0))
                self.btn_restart_job.pack_forget()
            elif job.status in ("failed", "stopped"):
                self.btn_stop_job.pack_forget()
                self.btn_restart_job.pack(side="right", padx=(4, 0))
            else:
                self.btn_stop_job.pack_forget()
                self.btn_restart_job.pack_forget()
            self.step_label.configure(text=f"{job.status}")
            self._display_job_logs(job_id)

    def _display_job_logs(self, job_id: str):
        """Hiển thị toàn bộ log của job được chọn."""
        self.log_textbox.configure(state="normal")
        self.log_textbox.delete("1.0", "end")

        if job_id in self.job_logs:
            for line in self.job_logs[job_id]:
                self.log_textbox.insert("end", line + "\n")
        else:
            logs = self.pipeline_mgr.get_job_logs(job_id)
            for line in logs:
                self.log_textbox.insert("end", line + "\n")

        self.log_textbox.see("end")
        self.log_textbox.configure(state="disabled")

    def _on_job_log(self, job_id: str, message: str):
        """Callback ghi log theo thời gian thực."""
        if job_id not in self.job_logs:
            self.job_logs[job_id] = []
        if len(self.job_logs[job_id]) < 500:
            self.job_logs[job_id].append(message)

        if not self.winfo_exists():
            return

        if job_id == self.selected_job_id:
            try:
                self.log_textbox.configure(state="normal")
                self.log_textbox.insert("end", message + "\n")
                self.log_textbox.see("end")
                self.log_textbox.configure(state="disabled")
            except Exception:
                pass

    def _on_job_progress(self, job_id: str, step_name: str, description: str, progress_pct: int):
        """Callback cập nhật tiến độ theo thời gian thực."""
        if not self.winfo_exists():
            return

        widget = self.job_widgets.get(job_id)
        if widget and hasattr(widget, 'status_label'):
            ctx = self.job_runner.get_job(job_id)
            count = len(ctx.file_paths) if ctx else 0
            pct_str = f" ({progress_pct}%)" if progress_pct >= 0 else ""
            widget.status_label.configure(
                text=f"{count} ảnh | {description}{pct_str}",
                text_color="#9CA3AF"
            )

        if job_id == self.selected_job_id:
            self.step_label.configure(text=description)
            if progress_pct >= 0:
                self.progress_pct_label.configure(text=f"{progress_pct}%", text_color="#3B82F6")
                self.progress_bar.set(progress_pct / 100.0)

    def _on_job_finished(self, ctx: ExecutionContext):
        """Callback khi job hoàn thành hoặc dừng."""
        if not self.winfo_exists():
            return

        job_id = ctx.job_id
        widget = self.job_widgets.get(job_id)
        count = len(ctx.file_paths)

        if ctx.status == "completed":
            if widget:
                if hasattr(widget, 'status_label'):
                    widget.status_label.configure(
                        text=f"{count} ảnh | Hoàn thành ({ctx.duration_seconds}s)",
                        text_color="#22C55E",
                        font=("Arial", 12, "bold")
                    )
                # Ẩn nút dừng và chạy lại trên danh sách job
                if hasattr(widget, 'stop_btn'):
                    widget.stop_btn.pack_forget()
                if hasattr(widget, 'restart_btn'):
                    widget.restart_btn.pack_forget()
                if hasattr(widget, 'folder_btn'):
                    widget.folder_btn.pack(side="right", padx=5, pady=3)

            if job_id == self.selected_job_id:
                # Ẩn nút dừng và chạy lại trên Header
                self.btn_stop_job.pack_forget()
                self.btn_restart_job.pack_forget()
                self.step_label.configure(text=f"Hoàn thành ({ctx.duration_seconds}s)")
                self.progress_pct_label.configure(text="100%", text_color="#22C55E")
                self.progress_bar.set(1.0)

            self._play_notification_sound()

        elif ctx.status == "failed":
            if widget:
                if hasattr(widget, 'status_label'):
                    widget.status_label.configure(
                        text=f"{count} ảnh | Lỗi: {ctx.error_message[:35]}",
                        text_color="#EF4444"
                    )
                if hasattr(widget, 'stop_btn'):
                    widget.stop_btn.pack_forget()
                if hasattr(widget, 'folder_btn'):
                    widget.folder_btn.pack_forget()
                if hasattr(widget, 'restart_btn'):
                    widget.restart_btn.pack(side="right", padx=5, pady=3)

            if job_id == self.selected_job_id:
                self.btn_stop_job.pack_forget()
                self.btn_restart_job.pack(side="right", padx=(4, 0))
                self.step_label.configure(text=f"Lỗi: {ctx.error_message}")
                self.progress_pct_label.configure(text="Lỗi", text_color="#EF4444")

        elif ctx.status in ("cancelled", "stopped"):
            if widget:
                if hasattr(widget, 'status_label'):
                    widget.status_label.configure(
                        text=f"{count} ảnh | Đã dừng",
                        text_color="#F59E0B"
                    )
                if hasattr(widget, 'stop_btn'):
                    widget.stop_btn.pack_forget()
                if hasattr(widget, 'folder_btn'):
                    widget.folder_btn.pack_forget()
                if hasattr(widget, 'restart_btn'):
                    widget.restart_btn.pack(side="right", padx=5, pady=3)

            if job_id == self.selected_job_id:
                self.btn_stop_job.pack_forget()
                self.btn_restart_job.pack(side="right", padx=(4, 0))
                self.step_label.configure(text="Tác vụ đã dừng")
                self.progress_pct_label.configure(text="Đã dừng", text_color="#F59E0B")

    def _resume_job(self, job_id: str):
        """Khôi phục và chạy tiếp job từ bước lỗi (Resume from failed step)."""
        widget = self.job_widgets.get(job_id)

        # 1. Kiểm tra trong JobRunner
        ctx = self.job_runner.get_job(job_id)
        if ctx:
            if hasattr(widget, 'restart_btn'):
                widget.restart_btn.pack_forget()
            if hasattr(widget, 'folder_btn'):
                widget.folder_btn.pack_forget()
            if hasattr(widget, 'stop_btn'):
                widget.stop_btn.pack(side="right", padx=5, pady=3)
            if hasattr(widget, 'status_label'):
                count = len(ctx.file_paths)
                widget.status_label.configure(
                    text=f"{count} ảnh | Đang chạy lại...",
                    text_color="#9CA3AF"
                )

            if job_id == self.selected_job_id:
                self.btn_restart_job.pack_forget()
                self.btn_stop_job.pack(side="right", padx=(4, 0))
                self.step_label.configure(text="Đang chạy lại từ bước lỗi...")
                self.progress_pct_label.configure(text="", text_color="#3B82F6")

            resumed_ctx = self.job_runner.resume_job(
                job_id=job_id,
                ui_callback_log=lambda lvl, msg: self.after(0, lambda l=lvl, m=msg: self._on_job_log(job_id, f"[{l}] {m}")),
                ui_callback_progress=lambda step, desc, pct: self.after(0, lambda s=step, d=desc, p=pct: self._on_job_progress(job_id, s, d, p)),
                ui_callback_finished=lambda c: self.after(0, lambda fin_c=c: self._on_job_finished(fin_c)),
            )
            if resumed_ctx:
                self._select_job(job_id)
            return

        # 2. Fallback sang legacy PipelineManager nếu là job cũ
        on_log = lambda jid, msg: self.after(0, lambda j=jid, m=msg: self._on_job_log(j, m))
        on_job_update = lambda j: self.after(0, lambda: self._refresh_job_list())
        new_job = self.pipeline_mgr.resume_job(job_id, on_log, on_job_update)
        if new_job:
            self._select_job(job_id)
            self._refresh_job_list()

    def _resume_selected_job(self):
        """Khôi phục job đang xem trong right panel."""
        if self.selected_job_id:
            self._resume_job(self.selected_job_id)

    def _stop_job(self, job_id: str):
        """Dừng tác vụ đang chạy."""
        stopped = self.job_runner.stop_job(job_id)
        if not stopped:
            self.pipeline_mgr.stop_job(job_id)

        widget = self.job_widgets.get(job_id)
        if widget:
            if hasattr(widget, 'stop_btn'):
                widget.stop_btn.pack_forget()
            if hasattr(widget, 'restart_btn'):
                widget.restart_btn.pack(side="right", padx=5, pady=3)

        if job_id == self.selected_job_id:
            self.btn_stop_job.pack_forget()
            self.btn_restart_job.pack(side="right", padx=(4, 0))
            self.step_label.configure(text="Tác vụ đã dừng")
            self.progress_pct_label.configure(text="Đã dừng", text_color="#F59E0B")

    def _stop_selected_job(self):
        """Dừng job đang xem trong right panel."""
        if self.selected_job_id:
            self._stop_job(self.selected_job_id)

    def _open_selected_job_folder(self):
        """Mở thư mục của job đang xem."""
        if self.selected_job_id:
            self._open_job_folder(self.selected_job_id)

    def _scan_recoverable_jobs(self):
        """Quét các checkpoint cũ."""
        new_jobs = self.pipeline_mgr.load_recoverable_jobs()
        if not new_jobs:
            return

        for job in new_jobs:
            self._add_job_to_list(job)
            self.pipeline_mgr.update_callbacks(
                job.job_id,
                on_log=lambda jid, msg: self.after(0, lambda j=jid, m=msg: self._on_job_log(j, m)),
                on_job_update=lambda j: self.after(0, lambda: self._refresh_job_list()),
            )

        self._refresh_job_list()
        if new_jobs:
            self._select_job(new_jobs[0].job_id)

    def _clear_job_list(self):
        """Xóa các job đã hoàn tất hoặc thất bại khỏi danh sách của Web hiện tại."""
        to_delete = []
        for jid, widget in list(self.job_widgets.items()):
            engine = self.job_engine_map.get(jid, "autohdr")
            if engine != self.current_web:
                continue

            ctx = self.job_runner.get_job(jid)
            if ctx:
                if ctx.status in ("completed", "failed", "cancelled"):
                    widget.destroy()
                    to_delete.append(jid)
            else:
                legacy_job = self.pipeline_mgr.get_job(jid)
                if legacy_job and legacy_job.status in ("completed", "failed", "stopped"):
                    widget.destroy()
                    to_delete.append(jid)
                elif not legacy_job:
                    widget.destroy()
                    to_delete.append(jid)

        for jid in to_delete:
            self.job_widgets.pop(jid, None)
            self.job_logs.pop(jid, None)
            self.job_engine_map.pop(jid, None)

        self.job_runner.clear_finished_jobs()

        if self.selected_job_id in to_delete:
            remaining = [jid for jid in self.job_widgets if self.job_engine_map.get(jid) == self.current_web]
            if remaining:
                self._select_job(remaining[-1])
            else:
                self.selected_job_id = None
                self.log_title_label.configure(text="Logs")
                self.step_label.configure(text="")
                self.progress_pct_label.configure(text="")
                self.progress_bar.set(0)
                self.btn_stop_job.pack_forget()
                self.btn_restart_job.pack_forget()
                self.log_textbox.configure(state="normal")
                self.log_textbox.delete("1.0", "end")
                self.log_textbox.configure(state="disabled")

    def _refresh_job_list(self):
        """Làm mới trạng thái các job."""
        if not self.winfo_exists():
            return

        for jid, widget in self.job_widgets.items():
            try:
                if not widget.winfo_exists():
                    continue

                # 1. Job trong JobRunner
                ctx = self.job_runner.get_job(jid)
                if ctx:
                    if ctx.status in ("failed", "cancelled", "stopped"):
                        if hasattr(widget, 'restart_btn'):
                            widget.restart_btn.pack(side="right", padx=5, pady=3)
                        if hasattr(widget, 'stop_btn'):
                            widget.stop_btn.pack_forget()
                    elif ctx.status == "running":
                        if hasattr(widget, 'restart_btn'):
                            widget.restart_btn.pack_forget()
                        if hasattr(widget, 'stop_btn'):
                            widget.stop_btn.pack(side="right", padx=5, pady=3)
                    elif ctx.status == "completed":
                        if hasattr(widget, 'restart_btn'):
                            widget.restart_btn.pack_forget()
                        if hasattr(widget, 'stop_btn'):
                            widget.stop_btn.pack_forget()
                        if hasattr(widget, 'folder_btn'):
                            widget.folder_btn.pack(side="right", padx=5, pady=3)
                    continue

                # 2. Legacy pipeline job
                job = self.pipeline_mgr.get_job(jid)
                if not job:
                    continue

                if job.status == "completed":
                    widget.status_label.configure(
                        text=f"{job.file_count} ảnh | Hoàn thành ({job.downloaded_count} đã tải)",
                        text_color="#22C55E",
                        font=("Arial", 12, "bold")
                    )
                    if hasattr(widget, 'stop_btn'):
                        widget.stop_btn.pack_forget()
                    if hasattr(widget, 'restart_btn'):
                        widget.restart_btn.pack_forget()
                    if hasattr(widget, 'folder_btn'):
                        widget.folder_btn.pack(side="right", padx=5, pady=3)
                    self._play_notification_sound()
                    if jid == self.selected_job_id:
                        self.log_title_label.configure(text=f"Job {jid}")
                        self.btn_stop_job.pack_forget()
                        self.btn_restart_job.pack_forget()

                elif job.status == "failed":
                    widget.status_label.configure(
                        text=f"{job.file_count} ảnh | Lỗi: {job.error[:40]}",
                        text_color="#EF4444",
                    )
                    if hasattr(widget, 'stop_btn'):
                        widget.stop_btn.pack_forget()
                    if hasattr(widget, 'restart_btn'):
                        widget.restart_btn.pack(side="right", padx=5, pady=3)
                    if jid == self.selected_job_id:
                        self.log_title_label.configure(text=f"Job {jid}")
                        self.btn_stop_job.pack_forget()
                        self.btn_restart_job.pack(side="right", padx=(4, 0))

                elif job.status == "stopped":
                    widget.status_label.configure(
                        text=f"{job.file_count} ảnh | Đã dừng",
                        text_color="#F59E0B",
                    )
                    if hasattr(widget, 'stop_btn'):
                        widget.stop_btn.pack_forget()
                    if hasattr(widget, 'restart_btn'):
                        widget.restart_btn.pack(side="right", padx=5, pady=3)
                    if jid == self.selected_job_id:
                        self.log_title_label.configure(text=f"Job {jid}")
                        self.btn_stop_job.pack_forget()
                        self.btn_restart_job.pack(side="right", padx=(4, 0))

                checkpoint_ids = self.pipeline_mgr.get_available_checkpoint_ids()
                if jid in checkpoint_ids and job.status != "processing":
                    if hasattr(widget, 'restart_btn'):
                        widget.restart_btn.pack(side="right", padx=5, pady=3)
                else:
                    if hasattr(widget, 'restart_btn') and job.status not in ("failed", "stopped"):
                        widget.restart_btn.pack_forget()
            except Exception:
                pass

    def _open_job_folder(self, job_id: str):
        """Mở thư mục của job."""
        ctx = self.job_runner.get_job(job_id)
        if ctx and ctx.output_dir and os.path.isdir(ctx.output_dir):
            open_folder(ctx.output_dir)
            return

        job = self.pipeline_mgr.get_job(job_id)
        if job and job.output_path and os.path.isdir(job.output_path):
            open_folder(job.output_path)
        else:
            download_dir = getattr(self, 'download_dir', '').strip() or os.path.expanduser("~/Downloads")
            open_folder(download_dir)


    # ==================================================
    # Sound Notification
    # ==================================================

    def _play_notification_sound(self):
        """Play a system bell/notification sound when job completes."""
        try:
            self.bell()  # Tkinter system bell
        except Exception:
            pass

    # ==================================================
    # Per-Job Log Management
    # ==================================================

    def _clear_selected_job_log(self):
        """Clear log for the currently selected job (both UI and file)."""
        if not self.selected_job_id:
            return

        # Clear UI
        self.log_textbox.configure(state="normal")
        self.log_textbox.delete("1.0", "end")
        self.log_textbox.configure(state="disabled")

        # Delete log file
        self.pipeline_mgr.delete_job_log(self.selected_job_id)

    def _on_mousewheel(self, event, widget):
        """Handle mouse wheel scrolling for Linux (Button-4/5)."""
        # Only scroll if the mouse is over the widget
        if event.num == 4:
            widget._parent_canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            widget._parent_canvas.yview_scroll(1, "units")
