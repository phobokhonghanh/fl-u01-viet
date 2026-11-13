"""
Screen Main — Màn hình điều phối chính (Controller) của ứng dụng.

Kiến trúc sạch 100%:
- 1 chuẩn xử lý tác vụ duy nhất: JobRunner (đa engine: AutoHDR, Autoenhance).
- 5 UI Components độc lập: ConfigAutoHDRFrame, ConfigAutoenhanceFrame, DropZoneFrame, JobListFrame, LogViewerFrame.
- Không fallback thuộc tính, không phụ thuộc hệ thống pipeline cũ.
"""

import os
import sys
import threading
import customtkinter as ctk
from typing import Optional
from urllib.parse import urlparse

# Đưa 1tm và 1tm/libs vào sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TM_DIR = os.path.join(PROJECT_ROOT, "1tm")
LIBS_DIR = os.path.join(TM_DIR, "libs")
if TM_DIR not in sys.path:
    sys.path.insert(0, TM_DIR)
if LIBS_DIR not in sys.path:
    sys.path.insert(0, LIBS_DIR)

from core.api_client import ApiClient
from core.cache import cache
from core.utils import open_folder
from ui.settings_dialog import SettingsDialog
from ui.components import (
    ConfigAutoHDRFrame,
    ConfigAutoenhanceFrame,
    DropZoneFrame,
    JobListFrame,
    LogViewerFrame,
)
from jobs import JobRunner, ExecutorRegistry
from libs.auth import get_active_session, BaseSessionRecord
from libs.executor import ExecutionContext


def parse_autoenhance_url(url_str: str) -> tuple[bool, str, str]:
    """Chỉ chấp nhận URL Autoenhance: https://app.autoenhance.ai/orders/<order_id>"""
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
    """Chỉ chấp nhận URL AutoHDR: https://app.autohdr.com/photoshoots/<photoshoot_id>"""
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
        self.job_runner: JobRunner = getattr(self.app, "job_runner", None) or JobRunner()

        self.current_session: Optional[BaseSessionRecord] = None
        self.current_ae_session: Optional[BaseSessionRecord] = None
        cached_web = cache.get("selected_web", "AutoHDR")
        if cached_web not in ["AutoHDR", "Autoenhance"]:
            cached_web = "AutoHDR"
        self.current_web = cached_web.strip().lower()
        self.is_manual_mode = bool(cache.get("is_manual_mode", False))
        self.download_dir = cache.get("download_dir", os.path.expanduser("~/Downloads"))

        self._load_cached_session()
        self._build_ui()
        self._restore_existing_jobs()

    # ==================================================
    # Khởi tạo giao diện
    # ==================================================

    def _load_cached_session(self):
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
        self.current_ae_session = get_active_session("autoenhance")

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # 1. TOP BAR
        top_bar = ctk.CTkFrame(self)
        top_bar.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))

        # Trái: Web selector + FILE/URL Toggle
        left_top = ctk.CTkFrame(top_bar, fg_color="transparent")
        left_top.pack(side="left", padx=5, pady=4)

        ctk.CTkLabel(left_top, text="Web:", font=("Arial", 14, "bold")).pack(side="left", padx=(4, 4))
        initial_web = "Autoenhance" if self.current_web == "autoenhance" else "AutoHDR"
        self.web_var = ctk.StringVar(value=initial_web)
        self.web_selector = ctk.CTkComboBox(
            left_top,
            values=["AutoHDR", "Autoenhance"],
            variable=self.web_var,
            command=self._on_web_changed,
            state="readonly",
            width=125,
            height=34,
            font=("Arial", 13, "bold"),
        )
        self.web_selector.pack(side="left", padx=(0, 10))

        initial_mode = "URL" if self.is_manual_mode else "FILE"
        self.mode_var = ctk.StringVar(value=initial_mode)
        self.mode_toggle = ctk.CTkSegmentedButton(
            left_top,
            values=["FILE", "URL"],
            variable=self.mode_var,
            command=self._on_mode_toggle_changed,
            height=34,
            font=("Arial", 12, "bold"),
        )
        self.mode_toggle.pack(side="left")

        # Phải: Cài đặt & Thông tin Auth
        right_top = ctk.CTkFrame(top_bar, fg_color="transparent")
        right_top.pack(side="right", padx=5, pady=4)

        self.btn_top_settings = ctk.CTkButton(
            right_top,
            text="⚙ Cài đặt",
            width=95,
            height=34,
            font=("Arial", 12, "bold"),
            command=self._open_settings_dialog,
            fg_color="#374151",
            hover_color="#4B5563",
            cursor="hand2",
        )
        self.btn_top_settings.pack(side="right", padx=(8, 2))

        self.auth_container = ctk.CTkFrame(right_top, fg_color="transparent")
        self.auth_container.pack(side="right", padx=4)

        self.autohdr_auth_frame = ctk.CTkFrame(self.auth_container, fg_color="transparent")
        self.autohdr_auth_frame.pack(side="left")
        ctk.CTkLabel(self.autohdr_auth_frame, text="Tài khoản:", font=("Arial", 12, "bold")).pack(side="left", padx=(2, 4))
        self.email_label = ctk.CTkLabel(self.autohdr_auth_frame, text="", font=("Arial", 12, "bold"), text_color="gray", cursor="hand2")
        self.email_label.pack(side="left", padx=(0, 4))
        self.email_label.bind("<Button-1>", lambda e: self._open_settings_dialog(initial_tab="accounts"))
        if self.current_session and getattr(self.current_session, "email", ""):
            self.email_label.configure(text=f"🟢 {self.current_session.email}", text_color="#22C55E")
        else:
            self.email_label.configure(text="⚪ Chưa đăng nhập", text_color="gray")

        self.autoenhance_auth_frame = ctk.CTkFrame(self.auth_container, fg_color="transparent")
        ctk.CTkLabel(self.autoenhance_auth_frame, text="API Key:", font=("Arial", 12, "bold")).pack(side="left", padx=(2, 4))
        self.autoenhance_status_label = ctk.CTkLabel(self.autoenhance_auth_frame, text="", font=("Arial", 12, "bold"), text_color="gray", cursor="hand2")
        self.autoenhance_status_label.pack(side="left", padx=(0, 4))
        self.autoenhance_status_label.bind("<Button-1>", lambda e: self._open_settings_dialog(initial_tab="accounts"))
        if self.current_ae_session and getattr(self.current_ae_session, "api_key", ""):
            self.autoenhance_status_label.configure(text=f"🟢 {self.current_ae_session.display_name}", text_color="#22C55E")
        else:
            self.autoenhance_status_label.configure(text="⚪ Chưa có API Key", text_color="gray")

        # 2. MAIN CONTENT (50/50 Split)
        content_frame = ctk.CTkFrame(self, fg_color="transparent")
        content_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
        content_frame.grid_columnconfigure(0, weight=1, uniform="half")
        content_frame.grid_columnconfigure(1, weight=1, uniform="half")
        content_frame.grid_rowconfigure(0, weight=1)

        # LEFT PANEL
        left_panel = ctk.CTkFrame(content_frame)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        left_panel.grid_columnconfigure(0, weight=1)
        left_panel.grid_rowconfigure(0, weight=0)
        left_panel.grid_rowconfigure(1, weight=0, minsize=140)
        left_panel.grid_rowconfigure(2, weight=0)
        left_panel.grid_rowconfigure(3, weight=1, minsize=160)

        # Tên dự án & Cấu hình
        project_box = ctk.CTkFrame(left_panel, fg_color=("gray90", "gray17"), border_width=1, border_color="#374151")
        project_box.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))

        project_row = ctk.CTkFrame(project_box, fg_color="transparent")
        project_row.pack(fill="x", padx=6, pady=(6, 4))
        ctk.CTkLabel(project_row, text="Tên dự án:", font=("Arial", 13, "bold")).pack(side="left", padx=(2, 6))
        self.address_entry = ctk.CTkEntry(project_row, placeholder_text="Nhập tên dự án...", height=30, font=("Arial", 13))
        self.address_entry.pack(side="left", fill="x", expand=True, padx=2)
        self.address_entry.insert(0, cache.get("address", "Demo Project"))
        self.address_entry.bind("<FocusOut>", lambda e: cache.set("address", self.address_entry.get().strip()))

        config_box = ctk.CTkFrame(project_box, fg_color="transparent")
        config_box.pack(fill="x", padx=6, pady=(2, 6))

        self.cfg_autohdr = ConfigAutoHDRFrame(config_box)
        self.cfg_autoenhance = ConfigAutoenhanceFrame(config_box)

        # Drop Zone (FILE / URL)
        self.drop_zone = DropZoneFrame(
            left_panel,
            get_max_files_fn=self._get_current_max_files,
        )
        self.drop_zone.grid(row=1, column=0, sticky="nsew", padx=8, pady=(4, 6))

        # Nút Xử lý
        btn_box = ctk.CTkFrame(left_panel, fg_color="transparent")
        btn_box.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 6))
        btn_box.grid_columnconfigure(0, weight=1)

        self.btn_process = ctk.CTkButton(
            btn_box,
            text="BẮT ĐẦU XỬ LÝ",
            command=self._start_process_async,
            fg_color="#16A34A",
            hover_color="#15803D",
            height=40,
            font=("Arial", 16, "bold"),
        )
        self.btn_process.grid(row=0, column=0, sticky="ew")

        # Danh sách tác vụ
        self.job_list = JobListFrame(
            left_panel,
            on_select_job=self._select_job,
            on_clear_jobs=self._clear_job_list,
            on_scan_failed=self._scan_recoverable_jobs,
            on_stop_job=self._stop_job,
            on_resume_job=self._resume_job,
            on_open_folder=self._open_job_folder,
        )
        self.job_list.grid(row=3, column=0, sticky="nsew", padx=8, pady=(4, 4))

        # RIGHT PANEL: Log & Progress Viewer
        self.log_viewer = LogViewerFrame(
            content_frame,
            on_stop_job=self._stop_job,
            on_resume_job=self._resume_job,
            on_open_folder=self._open_job_folder,
        )
        self.log_viewer.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

        # Khôi phục trạng thái
        self._on_web_changed(initial_web)
        if self.is_manual_mode:
            self._set_manual_mode(True)

    def _restore_existing_jobs(self):
        jobs = self.job_runner.list_jobs()
        if not jobs:
            return

        for ctx in jobs:
            engine_family = "autoenhance" if "autoenhance" in ctx.engine else "autohdr"
            self.job_list.add_job_card(
                job_id=ctx.job_id,
                address=ctx.preferences.get("address", ""),
                file_count=len(ctx.file_paths),
                engine=engine_family,
                status=ctx.status,
            )
            # Tái kết nối callbacks cho UI
            ctx.on_log = lambda lvl, msg, jid=ctx.job_id: self.after(
                0, lambda l=lvl, m=msg, j=jid: self._on_job_log(j, f"[{l}] {m}")
            )
            ctx.on_step_change = lambda s, d, p, jid=ctx.job_id: self.after(
                0, lambda st=s, de=d, pct=p, j=jid: self._on_job_progress(j, st, de, pct)
            )

        running_jobs = [j for j in jobs if j.status == "running"]
        target = running_jobs[-1].job_id if running_jobs else jobs[-1].job_id
        self._select_job(target)
        self._refresh_job_list()

    # ==================================================
    # Account & Settings & Web Switch
    # ==================================================

    def _on_account_changed(self, engine: str, record: Optional[BaseSessionRecord]):
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

    def _get_current_max_files(self) -> int:
        try:
            executor_cls = ExecutorRegistry.get(self.current_web)
            if executor_cls and hasattr(executor_cls, "max_upload_files"):
                return int(executor_cls.max_upload_files)
        except Exception:
            pass
        return 120

    def _on_web_changed(self, new_web: str):
        self.current_web = new_web.strip().lower()
        cache.set("selected_web", new_web)
        self.drop_zone.set_url_error("")

        if self.current_web == "autohdr":
            self.autoenhance_auth_frame.pack_forget()
            self.autohdr_auth_frame.pack(side="left", fill="x", expand=True)
            self.cfg_autoenhance.pack_forget()
            self.cfg_autohdr.pack(fill="x", expand=True)
        else:
            self.autohdr_auth_frame.pack_forget()
            self.autoenhance_auth_frame.pack(side="left", fill="x", expand=True)
            self.cfg_autohdr.pack_forget()
            self.cfg_autoenhance.pack(fill="x", expand=True)

        self.job_list.filter_by_web(self.current_web)
        self.drop_zone.update_limit_display()

    def _open_settings_dialog(self, initial_tab: str = "storage"):
        SettingsDialog(
            self,
            app=self.app,
            current_download_dir=self.download_dir,
            on_save_storage=self._on_download_dir_changed,
            on_change_account=self._on_account_changed,
            initial_tab=initial_tab,
            initial_engine=self.current_web,
        )

    def _open_accounts_dialog(self, initial_engine: str = "autohdr"):
        self._open_settings_dialog(initial_tab="accounts")

    def _on_download_dir_changed(self, new_dir: str):
        self.download_dir = new_dir
        cache.set("download_dir", new_dir)

    def _on_mode_toggle_changed(self, mode: str):
        self._set_manual_mode(mode == "URL")

    def _set_manual_mode(self, manual: bool):
        self.is_manual_mode = manual
        cache.set("is_manual_mode", manual)
        if hasattr(self, "mode_toggle"):
            self.mode_toggle.set("URL" if manual else "FILE")
        self.drop_zone.set_mode(manual)

    # ==================================================
    # Khởi chạy Job qua JobRunner
    # ==================================================

    def _start_process_async(self):
        if self.is_manual_mode:
            return self._start_manual_process_async()

        if not self.drop_zone.selected_files:
            self.drop_zone.show_file_error("⚠️ Vui lòng chọn ít nhất 1 ảnh để bắt đầu")
            return

        self.drop_zone.update_limit_display()
        address = self.address_entry.get().strip() or "Untitled Project"

        if self.current_web == "autohdr" and not self.current_session:
            self.email_label.configure(text="Cần đăng nhập trước", text_color="#EF4444")
            return
        elif self.current_web == "autoenhance" and not self.current_ae_session:
            self.autoenhance_status_label.configure(text="Cần lưu API key trước", text_color="#EF4444")
            return

        self.btn_process.configure(state="disabled", text="⏳ Đang kiểm tra key...")
        self.update_idletasks()

        def _do_check_and_start():
            if not self.api.check_key():
                self.after(0, lambda: self.btn_process.configure(state="normal", text="BẮT ĐẦU XỬ LÝ"))
                self.after(0, lambda: self.app.show_key_screen(self.api.last_check_status))
                return
            self.after(0, lambda: self._create_and_start_job(address))

        threading.Thread(target=_do_check_and_start, daemon=True).start()

    def _start_manual_process_async(self):
        raw_input = self.drop_zone.get_url()
        if not raw_input:
            self.drop_zone.set_url_error("⚠️ Vui lòng dán link dự án")
            return

        address = self.address_entry.get().strip() or "Manual Project"
        handlers = {
            "autoenhance": self._handle_url_autoenhance,
            "autohdr": self._handle_url_autohdr,
        }
        handler = handlers.get(self.current_web)
        if handler:
            handler(raw_input, address)

    def _handle_url_autoenhance(self, raw_url: str, address: str):
        self.drop_zone.set_url_error("")
        valid, order_id, err = parse_autoenhance_url(raw_url)
        if not valid:
            self.drop_zone.set_url_error(f"⚠️ {err}")
            return

        if not self.current_ae_session or not getattr(self.current_ae_session, "api_key", ""):
            self.drop_zone.set_url_error("⚠️ Cần lưu API key Autoenhance trước khi tải")
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
        self.drop_zone.set_url_error("")
        valid, photoshoot_uuid, err = parse_autohdr_url(raw_url)
        if not valid:
            self.drop_zone.set_url_error(f"⚠️ {err}")
            return

        if not self.current_session:
            self.drop_zone.set_url_error("⚠️ Cần đăng nhập AutoHDR trước khi tải")
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
        cache.set("address", address)
        download_dir = getattr(self, "download_dir", "").strip() or os.path.expanduser("~/Downloads")
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

        display_name = address if address and address != "Manual Project" else f"Cloud: {order_id[:8]}"
        self.job_list.add_job_card(
            job_id=ctx.job_id,
            address=display_name,
            file_count=0,
            engine="autoenhance",
            status="running",
        )
        self._select_job(ctx.job_id)
        self.drop_zone.clear()
        self.btn_process.configure(state="normal", text="BẮT ĐẦU XỬ LÝ")

    def _create_and_start_manual_job(self, address: str, photoshoot_uuid: str):
        cache.set("address", address)
        download_dir = getattr(self, "download_dir", "").strip() or os.path.expanduser("~/Downloads")
        cookie = self.current_session.cookie if self.current_session else ""

        ctx = self.job_runner.submit_job(
            engine="autohdr_download",
            file_paths=[],
            output_dir=download_dir,
            credentials={"cookie": cookie},
            preferences={"photoshoot_uuid": photoshoot_uuid, "address": address},
            ui_callback_log=lambda lvl, msg: self.after(0, lambda l=lvl, m=msg: self._on_job_log(ctx.job_id, f"[{l}] {m}")),
            ui_callback_progress=lambda step, desc, pct: self.after(0, lambda s=step, d=desc, p=pct: self._on_job_progress(ctx.job_id, s, d, p)),
            ui_callback_finished=lambda c: self.after(0, lambda fin_c=c: self._on_job_finished(fin_c)),
        )

        display_name = address if address and address != "Manual Project" else f"Photoshoot: {photoshoot_uuid[:8]}"
        self.job_list.add_job_card(
            job_id=ctx.job_id,
            address=display_name,
            file_count=0,
            engine="autohdr",
            status="running",
        )
        self._select_job(ctx.job_id)
        self.drop_zone.clear()
        self.btn_process.configure(state="normal", text="BẮT ĐẦU XỬ LÝ")

    def _create_and_start_job(self, address: str):
        cache.set("address", address)
        download_dir = getattr(self, "download_dir", "").strip() or os.path.expanduser("~/Downloads")

        if self.current_web == "autohdr":
            preferences = self.cfg_autohdr.get_preferences()
            preferences["address"] = address
            credentials = {"cookie": self.current_session.cookie if self.current_session else ""}
        else:
            preferences = self.cfg_autoenhance.get_preferences()
            preferences["address"] = address
            credentials = {"api_key": self.current_ae_session.api_key if self.current_ae_session else ""}

        ctx = self.job_runner.submit_job(
            engine=self.current_web,
            file_paths=self.drop_zone.selected_files.copy(),
            output_dir=download_dir,
            credentials=credentials,
            preferences=preferences,
            ui_callback_log=lambda lvl, msg: self.after(0, lambda l=lvl, m=msg: self._on_job_log(ctx.job_id, f"[{l}] {m}")),
            ui_callback_progress=lambda step, desc, pct: self.after(0, lambda s=step, d=desc, p=pct: self._on_job_progress(ctx.job_id, s, d, p)),
            ui_callback_finished=lambda c: self.after(0, lambda fin_c=c: self._on_job_finished(fin_c)),
        )

        self.job_list.add_job_card(
            job_id=ctx.job_id,
            address=address,
            file_count=len(ctx.file_paths),
            engine=self.current_web,
            status="running",
        )
        self._select_job(ctx.job_id)
        self.drop_zone.clear()
        self.btn_process.configure(state="normal", text="BẮT ĐẦU XỬ LÝ")

    # ==================================================
    # Quản lý Danh sách Tác vụ & Right Panel Logs
    # ==================================================

    def _select_job(self, job_id: Optional[str]):
        self.job_list.set_selected_job(job_id)
        if not job_id:
            self.log_viewer.set_job(None)
            return

        ctx = self.job_runner.get_job(job_id)
        if ctx:
            desc = ctx.current_step if isinstance(ctx.current_step, str) else ""
            logs = self.log_viewer.job_logs.get(job_id)
            self.log_viewer.set_job(
                job_id=job_id,
                status=ctx.status,
                step_desc=desc,
                pct=100 if ctx.status == "completed" else -1,
                duration=ctx.duration_seconds,
                error_msg=ctx.error_message,
                existing_logs=logs,
            )
        else:
            self.log_viewer.set_job(job_id, status="running")

    def _on_job_log(self, job_id: str, message: str):
        self.log_viewer.append_log(job_id, message)

    def _on_job_progress(self, job_id: str, step_name: str, description: str, progress_pct: int):
        if not self.winfo_exists():
            return

        card = self.job_list.job_widgets.get(job_id)
        if card and hasattr(card, "status_label"):
            ctx = self.job_runner.get_job(job_id)
            count = len(ctx.file_paths) if ctx else 0
            pct_str = f" ({progress_pct}%)" if progress_pct >= 0 else ""
            card.status_label.configure(
                text=f"{count} ảnh | {description}{pct_str}",
                text_color="#9CA3AF"
            )

        self.log_viewer.update_progress(job_id, step_name, description, progress_pct)

    def _on_job_finished(self, ctx: ExecutionContext):
        if not self.winfo_exists():
            return

        job_id = ctx.job_id
        self.job_list.update_job_status(job_id, ctx.status)
        self.log_viewer.update_status(
            job_id, ctx.status, duration=ctx.duration_seconds, error_msg=ctx.error_message
        )
        if ctx.status == "completed":
            self._play_notification_sound()

    def _resume_job(self, job_id: str):
        ctx = self.job_runner.get_job(job_id)
        if not ctx:
            return

        self.job_list.update_job_status(job_id, "running")
        self.log_viewer.update_status(job_id, "running")
        resumed_ctx = self.job_runner.resume_job(
            job_id=job_id,
            ui_callback_log=lambda lvl, msg: self.after(0, lambda l=lvl, m=msg: self._on_job_log(job_id, f"[{l}] {m}")),
            ui_callback_progress=lambda step, desc, pct: self.after(0, lambda s=step, d=desc, p=pct: self._on_job_progress(job_id, s, d, p)),
            ui_callback_finished=lambda c: self.after(0, lambda fin_c=c: self._on_job_finished(fin_c)),
        )
        if resumed_ctx:
            self._select_job(job_id)

    def _stop_job(self, job_id: str):
        self.job_runner.stop_job(job_id)
        self.job_list.update_job_status(job_id, "stopped")
        self.log_viewer.update_status(job_id, "stopped")

    def _open_job_folder(self, job_id: Optional[str] = None):
        if job_id:
            ctx = self.job_runner.get_job(job_id)
            if ctx and ctx.output_dir and os.path.isdir(ctx.output_dir):
                open_folder(ctx.output_dir)
                return
        download_dir = getattr(self, "download_dir", "").strip() or os.path.expanduser("~/Downloads")
        open_folder(download_dir)

    def _scan_recoverable_jobs(self):
        failed_jobs = [j for j in self.job_runner.list_jobs() if j.status in ("failed", "cancelled", "stopped")]
        if failed_jobs:
            self._select_job(failed_jobs[0].job_id)

    def _clear_job_list(self):
        to_delete = []
        for jid in list(self.job_list.job_widgets.keys()):
            engine = self.job_list.job_engine_map.get(jid, "autohdr")
            if engine != self.current_web:
                continue

            ctx = self.job_runner.get_job(jid)
            if not ctx or ctx.status in ("completed", "failed", "cancelled", "stopped"):
                to_delete.append(jid)

        self.job_list.remove_jobs(to_delete)
        self.job_runner.clear_finished_jobs()

    def _refresh_job_list(self):
        if not self.winfo_exists():
            return

        for jid in list(self.job_list.job_widgets.keys()):
            ctx = self.job_runner.get_job(jid)
            if ctx:
                self.job_list.update_job_status(jid, ctx.status)

    def _play_notification_sound(self):
        try:
            self.bell()
        except Exception:
            pass
