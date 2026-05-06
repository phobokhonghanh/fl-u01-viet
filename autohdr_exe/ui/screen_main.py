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
import json
import threading
import datetime
import platform
import subprocess
import customtkinter as ctk
from tkinter import filedialog
from tkinterdnd2 import DND_FILES

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


class ScreenMain(ctk.CTkFrame):
    def __init__(self, master, app, **kwargs):
        super().__init__(master, **kwargs)
        self.app = app
        self.api = ApiClient()
        self.pipeline_mgr = self.app.pipeline_mgr  # Use manager from App
        self.selected_files = []
        self.allowed_extensions = {'.jpg', '.jpeg', '.png', '.webp'}
        self.current_session = None
        self.selected_job_id = None  # Currently selected job for log viewing
        self.job_widgets = {}  # job_id -> widget reference

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
        """Try to restore session from cache."""
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

    def _build_ui(self):
        """Build the full main screen layout."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ============================================================
        # TOP BAR: Cookie input + Init | Key button
        # ============================================================
        top_bar = ctk.CTkFrame(self)
        top_bar.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))

        # Left side: Cookie + Init
        cookie_frame = ctk.CTkFrame(top_bar, fg_color="transparent")
        cookie_frame.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(cookie_frame, text="Cookie:", font=("Arial", 14, "bold")).pack(side="left", padx=(5, 3))
        self.cookie_entry = ctk.CTkEntry(cookie_frame, placeholder_text="Nhập cookie hoặc chọn file...", height=35, font=("Arial", 14))
        self.cookie_entry.pack(side="left", fill="x", expand=True, padx=3)

        ctk.CTkButton(
            cookie_frame, text="📂", width=40, height=35,
            command=self._load_cookie_file,
            fg_color="#6B7280", hover_color="#4B5563",
            font=("Arial", 14)
        ).pack(side="left", padx=2)

        self.btn_init = ctk.CTkButton(
            cookie_frame, text="Khởi tạo", width=70, height=35,
            command=self._init_session_async,
            fg_color="#059669", hover_color="#047857",
            font=("Arial", 14, "bold")
        )
        self.btn_init.pack(side="left", padx=3)

        # Loading spinner for init
        self.init_spinner = ctk.CTkLabel(cookie_frame, text="", font=("Arial", 11))
        self.init_spinner.pack(side="left", padx=2)

        # Email status
        self.email_label = ctk.CTkLabel(cookie_frame, text="", font=("Arial", 14), text_color="gray")
        self.email_label.pack(side="left", padx=8)
        if self.current_session:
            self.email_label.configure(text=f"Email: {self.current_session.email}", text_color="#22C55E")

        # Right side: Key button
        ctk.CTkButton(
            top_bar, text="🔑 Nhập Key", width=120, height=35,
            command=lambda: self.app.show_key_screen(),
            fg_color="#7C3AED", hover_color="#6D28D9",
            font=("Arial", 14, "bold")
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
        left_panel.grid_rowconfigure(0, weight=1)  # Job list takes top half
        left_panel.grid_rowconfigure(1, weight=1)  # Drop zone takes bottom half
        left_panel.grid_rowconfigure(2, weight=0)  # Controls

        # --- Job List ---
        job_header = ctk.CTkFrame(left_panel, fg_color="transparent")
        job_header.grid(row=0, column=0, sticky="new", padx=8, pady=(8, 0))
        ctk.CTkLabel(job_header, text="Danh sách Jobs", font=("Arial", 18, "bold")).pack(side="left", anchor="w")
        
        # Scan failed jobs button
        self.btn_scan_failed = ctk.CTkButton(
            job_header, text="Quét job lỗi", width=80, height=28,
            command=self._scan_recoverable_jobs,
            fg_color="#4B5563", hover_color="#374151",
            font=("Arial", 12, "bold")
        )
        self.btn_scan_failed.pack(side="right", padx=5)

        self.job_list_frame = ctk.CTkScrollableFrame(left_panel, label_text="")
        self.job_list_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=(40, 5))
        
        # Bind mouse wheel for Linux
        self.job_list_frame.bind_all("<Button-4>", lambda e: self._on_mousewheel(e, self.job_list_frame))
        self.job_list_frame.bind_all("<Button-5>", lambda e: self._on_mousewheel(e, self.job_list_frame))

        # --- Drop Zone ---
        drop_container = ctk.CTkFrame(left_panel, fg_color="transparent")
        drop_container.grid(row=1, column=0, sticky="nsew", padx=8, pady=5)
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
            font=("Arial", 16, "bold"),
        )
        self.drop_label.pack(expand=True, pady=30)
        self.drop_label.bind("<Button-1>", lambda e: self._select_images())

        # Register Drag and Drop on container, frame and label for maximum sensitivity
        for widget in [drop_container, self.drop_frame, self.drop_label]:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind('<<Drop>>', self._on_drop)

        self.img_count_label = ctk.CTkLabel(drop_container, text="0 files đã chọn", font=("Arial", 15, "bold"))
        self.img_count_label.grid(row=1, column=0, sticky="w", padx=5, pady=5)

        # --- Controls ---
        controls_frame = ctk.CTkFrame(left_panel, fg_color="transparent")
        controls_frame.grid(row=2, column=0, sticky="sew", padx=8, pady=(0, 8))
        controls_frame.grid_columnconfigure(0, weight=1, uniform="col")
        controls_frame.grid_columnconfigure(1, weight=1, uniform="col")

        # Container for Left side (Dir, Address, Process Button)
        controls_left = ctk.CTkFrame(controls_frame, fg_color="transparent")
        controls_left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        
        # Container for Right side (Proxy)
        controls_right = ctk.CTkFrame(controls_frame, fg_color="transparent")
        controls_right.grid(row=0, column=1, sticky="nsew", padx=(4, 0))

        # --- LEFT: Download dir & Address ---
        dir_frame = ctk.CTkFrame(controls_left, fg_color="transparent")
        dir_frame.pack(fill="x", pady=(0, 3))

        ctk.CTkLabel(dir_frame, text="📂 Lưu:", font=("Arial", 13)).pack(side="left", padx=2)
        self.dir_entry = ctk.CTkEntry(dir_frame, height=30, font=("Arial", 13))
        self.dir_entry.pack(side="left", fill="x", expand=True, padx=2)
        cached_dir = cache.get("download_dir", os.path.expanduser("~/Downloads"))
        self.dir_entry.insert(0, cached_dir)

        ctk.CTkButton(dir_frame, text="...", width=30, height=30, command=self._select_dir, font=("Arial", 13, "bold")).pack(side="left", padx=2)

        addr_frame = ctk.CTkFrame(controls_left, fg_color="transparent")
        addr_frame.pack(fill="x", pady=(3, 8))
        ctk.CTkLabel(addr_frame, text="Project:", font=("Arial", 13)).pack(side="left", padx=2)
        
        self.address_entry = ctk.CTkEntry(addr_frame, placeholder_text="Nhập Project name...", height=30, font=("Arial", 13))
        self.address_entry.pack(side="left", fill="x", expand=True, padx=2)
        self.address_entry.insert(0, cache.get("address", "Demo Project"))

        mode_frame = ctk.CTkFrame(controls_left, fg_color="transparent")
        mode_frame.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(mode_frame, text="Mode:", font=("Arial", 13)).pack(side="left", padx=2)
        
        self.mode_var = ctk.StringVar(value=cache.get("mode", "Lisa"))
        self.mode_selector = ctk.CTkSegmentedButton(
            mode_frame, 
            values=["Classic", "Lisa", "No Sky"],
            variable=self.mode_var,
            command=lambda v: cache.set("mode", v),
            height=30
        )
        self.mode_selector.pack(side="left", fill="x", expand=True, padx=2)

        # --- RIGHT: Proxy Settings ---
        proxy_row1 = ctk.CTkFrame(controls_right, fg_color="transparent")
        proxy_row1.pack(fill="x", pady=2)
        
        self.proxy_ip_entry = ctk.CTkEntry(proxy_row1, placeholder_text="IP proxy...", height=30, font=("Arial", 13))
        self.proxy_ip_entry.pack(side="left", fill="x", expand=True, padx=(0, 3))
        if cache.get("proxy_ip", ""):
            self.proxy_ip_entry.insert(0, cache.get("proxy_ip", ""))
        
        self.proxy_port_entry = ctk.CTkEntry(proxy_row1, placeholder_text="Port...", height=30, font=("Arial", 13))
        self.proxy_port_entry.pack(side="left", padx=(0, 0))
        if cache.get("proxy_port", ""):
            self.proxy_port_entry.insert(0, cache.get("proxy_port", ""))

        proxy_row2 = ctk.CTkFrame(controls_right, fg_color="transparent")
        proxy_row2.pack(fill="x", pady=3)
        
        self.proxy_user_entry = ctk.CTkEntry(proxy_row2, placeholder_text="Username...", height=30, font=("Arial", 13))
        self.proxy_user_entry.pack(side="left", fill="x", expand=True, padx=(0, 3))
        if cache.get("proxy_user", ""):
            self.proxy_user_entry.insert(0, cache.get("proxy_user", ""))
        
        self.proxy_pass_entry = ctk.CTkEntry(proxy_row2, placeholder_text="Password...", height=30, font=("Arial", 13), show="*")
        self.proxy_pass_entry.pack(side="left", fill="x", expand=True, padx=(0, 0))
        if cache.get("proxy_pass", ""):
            self.proxy_pass_entry.insert(0, cache.get("proxy_pass", ""))
        
        proxy_footer = ctk.CTkFrame(controls_right, fg_color="transparent")
        proxy_footer.pack(fill="x", pady=(2, 0))
        self.proxy_status_label = ctk.CTkLabel(proxy_footer, text="Proxy chưa sử dụng", font=("Arial", 12), text_color="gray")
        self.proxy_status_label.pack(side="left")

        ctk.CTkButton(
            proxy_footer, text="Test", width=45, height=25,
            command=self._test_proxy_async,
            fg_color="#6B7280", hover_color="#4B5563",
            font=("Arial", 12, "bold")
        ).pack(side="left", padx=(8, 0))

        # Process button (Full Width spanning both columns)
        self.btn_process = ctk.CTkButton(
            controls_frame,
            text="BẮT ĐẦU XỬ LÝ",
            command=self._start_process_async,
            fg_color="#16A34A",
            hover_color="#15803D",
            height=40,
            font=("Arial", 16, "bold"),
        )
        self.btn_process.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        # ============================
        # RIGHT PANEL — Per-Job Log
        # ============================
        right_panel = ctk.CTkFrame(content_frame)
        right_panel.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        right_panel.grid_columnconfigure(0, weight=1)
        right_panel.grid_rowconfigure(1, weight=1)

        log_header = ctk.CTkFrame(right_panel, fg_color="transparent")
        log_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 0))

        self.log_title_label = ctk.CTkLabel(
            log_header, text="Logs", font=("Arial", 18, "bold"))
        self.log_title_label.pack(side="left")

        self.btn_clear_log = ctk.CTkButton(
            log_header, text="Clear", width=80, height=30,
            command=self._clear_selected_job_log,
            fg_color="#DC2626", hover_color="#B91C1C",
            font=("Arial", 14, "bold"),
        )
        self.btn_clear_log.pack(side="right")

        self.log_textbox = ctk.CTkTextbox(right_panel, font=("Consolas", 13))
        self.log_textbox.grid(row=1, column=0, sticky="nsew", padx=10, pady=(5, 10))
        self.log_textbox.configure(state="disabled")

        # Bind mouse wheel for Linux on log textbox
        self.log_textbox.bind("<Button-4>", lambda e: self.log_textbox.yview_scroll(-1, "units"))
        self.log_textbox.bind("<Button-5>", lambda e: self.log_textbox.yview_scroll(1, "units"))

    # ==================================================
    # Cookie & Session
    # ==================================================

    def _load_cookie_file(self):
        """Load cookie from file."""
        file_path = filedialog.askopenfilename(
            title="Chọn file Cookie",
            filetypes=[("JSON/TEXT files", "*.json *.txt"), ("All files", "*.*")],
        )
        if not file_path:
            return

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

            self.cookie_entry.delete(0, "end")
            self.cookie_entry.insert(0, content)

    def _init_session_async(self):
        """Init session in background thread to avoid UI freeze."""
        self.btn_init.configure(state="disabled", text="...")
        self.init_spinner.configure(text="⏳")
        self.update_idletasks()

        def _do_init():
            try:
                result = self._init_session()
            finally:
                self.after(0, lambda: self.btn_init.configure(state="normal", text="Init"))
                self.after(0, lambda: self.init_spinner.configure(text=""))

        thread = threading.Thread(target=_do_init, daemon=True)
        thread.start()

    def _init_session(self):
        """Initialize session with cookie (calls autohdr.com directly)."""
        cookie = self.cookie_entry.get().strip()
        cached_email = cache.get("email")

        if not cookie and not cached_email:
            self.after(0, lambda: self.email_label.configure(text="Nhập cookie", text_color="#EF4444"))
            return

        try:
            client = HttpClient()
            session = step0_session.execute(
                client,
                cookie=cookie if cookie else None,
                email=cached_email if not cookie else None,
            )

            if session:
                self.current_session = session
                self.after(0, lambda: self.email_label.configure(
                    text=f"{session.email}", text_color="#22C55E"))
                cache.set("email", session.email)
                cache.set("cookie", session.cookie)
                cache.set("user_id", session.user_id)
                cache.set("firstname", session.firstname)
                cache.set("lastname", session.lastname)
                cache.set("expires", session.expires)
            else:
                self.after(0, lambda: self.email_label.configure(
                    text="Cookie lỗi", text_color="#EF4444"))
        except Exception as e:
            self.after(0, lambda: self.email_label.configure(
                text=f"Lỗi: {str(e)[:30]}", text_color="#EF4444"))

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
                    self.dir_entry.delete(0, "end")
                    self.dir_entry.insert(0, dir_path)
                    cache.set("download_dir", dir_path)
                return # Exit early out of fallback
            except Exception:
                pass # Fallback to tkinter
                
        dir_path = filedialog.askdirectory(title="Chọn thư mục lưu ảnh")
        if dir_path:
            self.dir_entry.delete(0, "end")
            self.dir_entry.insert(0, dir_path)
            cache.set("download_dir", dir_path)

    # ==================================================
    # Process Pipeline
    # ==================================================

    def _start_process_async(self):
        """Validate and start pipeline in background to avoid UI lag."""
        if not self.selected_files:
            return

        key = cache.get("active_key")
        if not key:
            return

        if not self.current_session:
            return

        address = self.address_entry.get().strip()
        if not address:
            return

        # Show loading state
        self.btn_process.configure(state="disabled", text="⏳ Đang kiểm tra key...")
        self.update_idletasks()

        def _do_check_and_start():
            # Re-check key in background
            if not self.api.check_key(key, self.app.hwid):
                self.after(0, lambda: self.btn_process.configure(state="normal", text="BẮT ĐẦU XỬ LÝ"))
                cache.delete("active_key")
                self.after(0, lambda: self.app.show_key_screen())
                return

            # Start job on main thread
            self.after(0, lambda: self._create_and_start_job(address))

        thread = threading.Thread(target=_do_check_and_start, daemon=True)
        thread.start()

    def _get_proxy_config(self) -> dict:
        """Get proxy configuration from UI fields. Returns empty dict if not set."""
        ip = self.proxy_ip_entry.get().strip()
        port = self.proxy_port_entry.get().strip()
        user = self.proxy_user_entry.get().strip()
        password = self.proxy_pass_entry.get().strip()

        if ip and port:
            # Save to cache
            cache.set("proxy_ip", ip)
            cache.set("proxy_port", port)
            cache.set("proxy_user", user)
            cache.set("proxy_pass", password)
            return {"ip": ip, "port": port, "user": user, "password": password}
        return {}

    def _test_proxy_async(self):
        """Test proxy in background thread."""
        ip = self.proxy_ip_entry.get().strip()
        port = self.proxy_port_entry.get().strip()
        user = self.proxy_user_entry.get().strip()
        password = self.proxy_pass_entry.get().strip()

        if not ip or not port:
            self.proxy_status_label.configure(text="Không sử dụng", text_color="gray")
            return

        self.proxy_status_label.configure(text="Đang test...", text_color="#F59E0B")

        def _do_test():
            success, message = HttpClient.validate_proxy(ip, port, user, password)
            if success:
                self.after(0, lambda: self.proxy_status_label.configure(
                    text=f"{message}", text_color="#22C55E"))
            else:
                self.after(0, lambda: self.proxy_status_label.configure(
                    text=f"{message}", text_color="#EF4444"))

        thread = threading.Thread(target=_do_test, daemon=True)
        thread.start()

    def _create_and_start_job(self, address: str):
        """Create job and add to UI (must run on main thread)."""
        cache.set("address", address)
        download_dir = self.dir_entry.get().strip()
        if not download_dir:
            download_dir = os.path.expanduser("~/Downloads")

        # Get proxy config
        proxy_config = self._get_proxy_config()

        # Get model IDs from mode selection
        mode = self.mode_var.get()
        indoor_model_id = 3
        outdoor_model_id = None

        if mode == "Classic":
            indoor_model_id = 1
        elif mode == "No Sky":
            indoor_model_id = 4
            outdoor_model_id = 4

        # Create job
        job = self.pipeline_mgr.create_job(
            session=self.current_session,
            file_paths=self.selected_files.copy(),
            address=address,
            download_dir=download_dir,
            indoor_model_id=indoor_model_id,
            outdoor_model_id=outdoor_model_id,
            on_log=lambda jid, msg: self.after(0, lambda j=jid, m=msg: self._on_job_log(j, m)),
            on_job_update=lambda j: self.after(0, lambda: self._refresh_job_list()),
            proxy_config=proxy_config,
        )

        # Add job to UI
        self._add_job_to_list(job)

        # Auto-select this job for log viewing
        self._select_job(job.job_id)

        # Clear selection
        self.selected_files = []
        self.img_count_label.configure(text="0 files đã chọn")

        # Reset button
        self.btn_process.configure(state="normal", text="BẮT ĐẦU XỬ LÝ")

    def _add_job_to_list(self, job: Job):
        """Add a job widget to the job list."""
        job_row = ctk.CTkFrame(self.job_list_frame, cursor="hand2")
        job_row.pack(fill="x", pady=2, padx=2)
        job_row.job_id = job.job_id

        # Make entire row clickable
        job_row.bind("<Button-1>", lambda e, jid=job.job_id: self._select_job(jid))

        # Job info
        info_frame = ctk.CTkFrame(job_row, fg_color="transparent")
        info_frame.pack(side="left", fill="x", expand=True, padx=5, pady=3)
        info_frame.bind("<Button-1>", lambda e, jid=job.job_id: self._select_job(jid))

        name_label = ctk.CTkLabel(
            info_frame,
            text=f"Job {job.job_id} | {job.address[:20]}",
            font=("Arial", 16, "bold"),
        )
        name_label.pack(anchor="w")
        name_label.bind("<Button-1>", lambda e, jid=job.job_id: self._select_job(jid))

        status_label = ctk.CTkLabel(
            info_frame,
            text=f"{job.file_count} ảnh | Đang xử lý...",
            font=("Arial", 14),
            text_color="#9CA3AF",
        )
        status_label.pack(anchor="w")
        status_label.bind("<Button-1>", lambda e, jid=job.job_id: self._select_job(jid))
        job_row.status_label = status_label

        # Stop button — only shown during processing
        stop_btn = ctk.CTkButton(
            job_row, text="⏹", width=40, height=32,
            command=lambda jid=job.job_id: self._stop_job(jid),
            fg_color="#DC2626", hover_color="#B91C1C",
        )
        stop_btn.pack(side="right", padx=5, pady=3)
        job_row.stop_btn = stop_btn

        # Restart button — only shown if checkpoint exists
        restart_btn = ctk.CTkButton(
            job_row, text="🔄", width=40, height=32,
            command=lambda jid=job.job_id: self._resume_job(jid),
            fg_color="#F59E0B", hover_color="#D97706",
        )
        # Initially hidden, shown by _refresh_job_list if checkpoint exists
        job_row.restart_btn = restart_btn

        # Folder button — only shown when completed
        folder_btn = ctk.CTkButton(
            job_row, text="📂", width=40, height=32,
            command=lambda jid=job.job_id: self._open_job_folder(jid),
            fg_color="#6B7280", hover_color="#4B5563",
        )
        # Initially hide it
        job_row.folder_btn = folder_btn

        # Store widget reference
        self.job_widgets[job.job_id] = job_row

        # Auto-scroll to bottom
        try:
            self.job_list_frame._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _select_job(self, job_id: str):
        """Select a job and display its logs in the right panel."""
        self.selected_job_id = job_id

        # Update visual selection state
        for jid, widget in self.job_widgets.items():
            if jid == job_id:
                widget.configure(border_width=2, border_color="#3B82F6")
            else:
                widget.configure(border_width=0)

        # Update log panel title
        job = self.pipeline_mgr.get_job(job_id)
        if job:
            status_emoji = {"processing": "⏳", "completed": "✅", "failed": "❌", "stopped": "⏹"}.get(job.status, "")
            self.log_title_label.configure(text=f"Log: Job {job_id} {status_emoji}")

        # Load logs for this job
        self._display_job_logs(job_id)

    def _display_job_logs(self, job_id: str):
        """Display all logs for a specific job."""
        self.log_textbox.configure(state="normal")
        self.log_textbox.delete("1.0", "end")

        logs = self.pipeline_mgr.get_job_logs(job_id)
        for line in logs:
            self.log_textbox.insert("end", line + "\n")

        self.log_textbox.see("end")
        self.log_textbox.configure(state="disabled")

    def _on_job_log(self, job_id: str, message: str):
        """Handle a new log line from a job. Only display if this job is selected."""
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

    def _resume_job(self, job_id: str):
        """Resume a job from checkpoint."""
        # Use existing callbacks
        on_log = lambda jid, msg: self.after(0, lambda j=jid, m=msg: self._on_job_log(j, m))
        on_job_update = lambda j: self.after(0, lambda: self._refresh_job_list())
        
        # Call resume
        new_job = self.pipeline_mgr.resume_job(job_id, on_log, on_job_update)
        if new_job:
            self._select_job(job_id)
            self._refresh_job_list()

    def _stop_job(self, job_id: str):
        """Stop a running job."""
        self.pipeline_mgr.stop_job(job_id)

    def _scan_recoverable_jobs(self):
        """Scan for checkpoints and add them to the UI list."""
        new_jobs = self.pipeline_mgr.load_recoverable_jobs()
        if not new_jobs:
            return

        for job in new_jobs:
            self._add_job_to_list(job)
            # Attach callbacks
            self.pipeline_mgr.update_callbacks(
                job.job_id,
                on_log=lambda jid, msg: self.after(0, lambda j=jid, m=msg: self._on_job_log(j, m)),
                on_job_update=lambda j: self.after(0, lambda: self._refresh_job_list()),
            )
        
        self._refresh_job_list()
        # Optionally select the first newly found job
        if new_jobs:
            self._select_job(new_jobs[0].job_id)

    def _refresh_job_list(self):
        """Refresh job status in the UI."""
        if not self.winfo_exists():
            return

        for jid, widget in self.job_widgets.items():
            try:
                job = self.pipeline_mgr.get_job(jid)
                if not job or not widget.winfo_exists():
                    continue

                if job.status == "completed":
                    widget.status_label.configure(
                        text=f"{job.file_count} ảnh | Hoàn thành ({job.downloaded_count} đã tải)",
                        text_color="#22C55E",
                        font=("Arial", 14, "bold")
                    )
                    # Hide stop button
                    if hasattr(widget, 'stop_btn'):
                        widget.stop_btn.pack_forget()
                    # Show folder button
                    if hasattr(widget, 'folder_btn'):
                        widget.folder_btn.pack(side="right", padx=5, pady=3)
                    # Play notification sound
                    self._play_notification_sound()
                    # Update log title if this job is selected
                    if jid == self.selected_job_id:
                        self.log_title_label.configure(text=f"Log: Job {jid} ✅")

                elif job.status == "failed":
                    widget.status_label.configure(
                        text=f"{job.file_count} ảnh | Lỗi: {job.error[:40]}",
                        text_color="#EF4444",
                    )
                    if hasattr(widget, 'stop_btn'):
                        widget.stop_btn.pack_forget()
                    if jid == self.selected_job_id:
                        self.log_title_label.configure(text=f"Log: Job {jid} ❌")

                elif job.status == "stopped":
                    widget.status_label.configure(
                        text=f"{job.file_count} ảnh | Đã dừng",
                        text_color="#F59E0B",
                    )
                    if hasattr(widget, 'stop_btn'):
                        widget.stop_btn.pack_forget()
                    if jid == self.selected_job_id:
                        self.log_title_label.configure(text=f"Log: Job {jid} ⏹")

                # Check for checkpoints to show/hide restart button
                checkpoint_ids = self.pipeline_mgr.get_available_checkpoint_ids()
                if jid in checkpoint_ids and job.status != "processing":
                    if hasattr(widget, 'restart_btn'):
                        widget.restart_btn.pack(side="right", padx=5, pady=3)
                else:
                    if hasattr(widget, 'restart_btn'):
                        widget.restart_btn.pack_forget()
            except Exception:
                pass

    def _open_job_folder(self, job_id: str):
        """Open the download folder for a job."""
        job = self.pipeline_mgr.get_job(job_id)
        if job and job.output_path:
            open_folder(job.output_path)
        else:
            # Fallback for older jobs without output_path or just in case
            # This is not perfect but a good safety net
            download_dir = self.dir_entry.get().strip()
            if not download_dir:
                download_dir = os.path.expanduser("~/Downloads")
            date_str = datetime.datetime.now().strftime("%Y-%m-%d")
            possible_path = os.path.join(download_dir, date_str, job_id)
            open_folder(possible_path)


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
