"""
LogViewer Component — Quản lý bảng hiển thị Logs và tiến trình chi tiết của tác vụ bên phải.
"""
import customtkinter as ctk
from typing import Callable, Optional, List, Dict


class LogViewerFrame(ctk.CTkFrame):
    def __init__(
        self,
        master,
        on_stop_job: Optional[Callable[[str], None]] = None,
        on_resume_job: Optional[Callable[[str], None]] = None,
        on_open_folder: Optional[Callable[[str], None]] = None,
        **kwargs
    ):
        super().__init__(master, **kwargs)
        self.on_stop_job = on_stop_job
        self.on_resume_job = on_resume_job
        self.on_open_folder = on_open_folder

        self.selected_job_id: Optional[str] = None
        self.job_logs: Dict[str, List[str]] = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        self._build_ui()

    def _build_ui(self):
        # Row 0: Header with Job ID & Action buttons
        log_header = ctk.CTkFrame(self, fg_color="transparent")
        log_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 0))

        self.log_title_label = ctk.CTkLabel(
            log_header, text="Logs", font=("Arial", 18, "bold")
        )
        self.log_title_label.pack(side="left")

        self.btn_clear_log = ctk.CTkButton(
            log_header, text="Clear", width=70, height=30,
            command=self._handle_clear,
            fg_color="#DC2626", hover_color="#B91C1C",
            font=("Arial", 13, "bold"),
        )
        self.btn_clear_log.pack(side="right", padx=(4, 0))

        self.btn_stop_job = ctk.CTkButton(
            log_header, text="⏹ Dừng", width=70, height=30,
            command=self._handle_stop,
            fg_color="#B91C1C", hover_color="#991B1B",
            font=("Arial", 13, "bold"),
        )
        # Initially hidden

        self.btn_restart_job = ctk.CTkButton(
            log_header, text="🔄 Chạy lại", width=95, height=30,
            command=self._handle_resume,
            fg_color="#F59E0B", hover_color="#D97706",
            font=("Arial", 13, "bold"),
        )
        # Initially hidden

        self.btn_open_folder = ctk.CTkButton(
            log_header, text="📂 Mở thư mục", width=105, height=30,
            command=self._handle_open_folder,
            fg_color="#4B5563", hover_color="#374151",
            font=("Arial", 13, "bold"),
        )
        self.btn_open_folder.pack(side="right", padx=(4, 4))

        # Row 1: Progress step & percentage
        self.progress_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.progress_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(4, 2))

        self.step_label = ctk.CTkLabel(self.progress_frame, text="", font=("Arial", 12, "bold"))
        self.step_label.pack(side="left", anchor="w")

        self.progress_pct_label = ctk.CTkLabel(self.progress_frame, text="", font=("Arial", 12, "bold"), text_color="#3B82F6")
        self.progress_pct_label.pack(side="right", anchor="e")

        # Row 2: Progress bar
        self.progress_bar = ctk.CTkProgressBar(self, height=8)
        self.progress_bar.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 6))
        self.progress_bar.set(0)

        # Row 3: Log textbox
        self.log_textbox = ctk.CTkTextbox(self, font=("Consolas", 13))
        self.log_textbox.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.log_textbox.configure(state="disabled")

        # Linux mousewheel binding
        self.log_textbox.bind("<Button-4>", lambda e: self.log_textbox.yview_scroll(-1, "units"))
        self.log_textbox.bind("<Button-5>", lambda e: self.log_textbox.yview_scroll(1, "units"))

    def _handle_clear(self):
        if self.selected_job_id:
            self.job_logs[self.selected_job_id] = []
            self.log_textbox.configure(state="normal")
            self.log_textbox.delete("1.0", "end")
            self.log_textbox.configure(state="disabled")

    def _handle_stop(self):
        if self.selected_job_id and self.on_stop_job:
            self.on_stop_job(self.selected_job_id)

    def _handle_resume(self):
        if self.selected_job_id and self.on_resume_job:
            self.on_resume_job(self.selected_job_id)

    def _handle_open_folder(self):
        if self.on_open_folder:
            self.on_open_folder(self.selected_job_id)

    def set_job(
        self,
        job_id: Optional[str],
        status: str = "",
        step_desc: str = "",
        pct: int = -1,
        duration: Optional[float] = None,
        error_msg: str = "",
        existing_logs: Optional[List[str]] = None,
    ):
        """Cập nhật giao diện khi một job được chọn."""
        self.selected_job_id = job_id
        if not job_id:
            self.log_title_label.configure(text="Logs")
            self.btn_stop_job.pack_forget()
            self.btn_restart_job.pack_forget()
            self.step_label.configure(text="")
            self.progress_pct_label.configure(text="")
            self.progress_bar.set(0)
            self.log_textbox.configure(state="normal")
            self.log_textbox.delete("1.0", "end")
            self.log_textbox.configure(state="disabled")
            return

        clean_id = str(job_id).replace("job_", "")
        self.log_title_label.configure(text=f"Job {clean_id}")

        # Nút điều khiển
        if status in ("running", "processing"):
            self.btn_stop_job.pack(side="right", padx=(4, 0))
            self.btn_restart_job.pack_forget()
        elif status in ("failed", "cancelled", "stopped"):
            self.btn_stop_job.pack_forget()
            self.btn_restart_job.pack(side="right", padx=(4, 0))
        else:
            self.btn_stop_job.pack_forget()
            self.btn_restart_job.pack_forget()

        # Tiến trình & thông báo trạng thái
        if status == "completed":
            dur_str = f" ({duration:.1f}s)" if duration else ""
            self.step_label.configure(text=f"Hoàn thành{dur_str}")
            self.progress_pct_label.configure(text="100%", text_color="#22C55E")
            self.progress_bar.set(1.0)
        elif status == "failed":
            self.step_label.configure(text=f"Lỗi: {error_msg}")
            self.progress_pct_label.configure(text="Lỗi", text_color="#EF4444")
        elif status in ("cancelled", "stopped"):
            self.step_label.configure(text="Tác vụ đã dừng")
            self.progress_pct_label.configure(text="Đã dừng", text_color="#F59E0B")
        else:
            self.step_label.configure(text=step_desc or status)
            if pct >= 0:
                self.progress_pct_label.configure(text=f"{pct}%", text_color="#3B82F6")
                self.progress_bar.set(pct / 100.0)
            else:
                self.progress_pct_label.configure(text="")
                self.progress_bar.set(0)

        # Hiển thị Logs
        if existing_logs is not None:
            self.job_logs[job_id] = list(existing_logs)

        self._display_logs(job_id)

    def _display_logs(self, job_id: str):
        self.log_textbox.configure(state="normal")
        self.log_textbox.delete("1.0", "end")
        lines = self.job_logs.get(job_id, [])
        for line in lines:
            self.log_textbox.insert("end", line + "\n")
        self.log_textbox.see("end")
        self.log_textbox.configure(state="disabled")

    def append_log(self, job_id: str, message: str):
        """Ghi thêm log theo thời gian thực (xoay vòng FIFO tối đa 500 dòng)."""
        if job_id not in self.job_logs:
            self.job_logs[job_id] = []
        if len(self.job_logs[job_id]) >= 500:
            self.job_logs[job_id].pop(0)
        self.job_logs[job_id].append(message)

        if not self.winfo_exists():
            return

        if job_id == self.selected_job_id:
            try:
                self.log_textbox.configure(state="normal")
                # Giới hạn 500 dòng hiển thị tránh nặng RAM
                line_count = int(float(self.log_textbox.index("end-1c")))
                if line_count >= 500:
                    self.log_textbox.delete("1.0", "2.0")
                self.log_textbox.insert("end", message + "\n")
                self.log_textbox.see("end")
                self.log_textbox.configure(state="disabled")
            except Exception:
                pass

    def update_progress(self, job_id: str, step_name: str, description: str, progress_pct: int):
        """Cập nhật tiến trình theo thời gian thực."""
        if not self.winfo_exists() or job_id != self.selected_job_id:
            return

        self.step_label.configure(text=description)
        if progress_pct >= 0:
            self.progress_pct_label.configure(text=f"{progress_pct}%", text_color="#3B82F6")
            self.progress_bar.set(progress_pct / 100.0)

    def update_status(self, job_id: str, status: str, duration: Optional[float] = None, error_msg: str = ""):
        """Cập nhật trạng thái tác vụ hoàn thành, lỗi hoặc dừng."""
        if not self.winfo_exists() or job_id != self.selected_job_id:
            return

        self.btn_stop_job.pack_forget()
        self.btn_restart_job.pack_forget()

        if status == "completed":
            dur_str = f" ({duration:.1f}s)" if duration else ""
            self.step_label.configure(text=f"Hoàn thành{dur_str}")
            self.progress_pct_label.configure(text="100%", text_color="#22C55E")
            self.progress_bar.set(1.0)
        elif status == "failed":
            self.btn_restart_job.pack(side="right", padx=(4, 0))
            self.step_label.configure(text=f"Lỗi: {error_msg}")
            self.progress_pct_label.configure(text="Lỗi", text_color="#EF4444")
        elif status in ("cancelled", "stopped"):
            self.btn_restart_job.pack(side="right", padx=(4, 0))
            self.step_label.configure(text="Tác vụ đã dừng")
            self.progress_pct_label.configure(text="Đã dừng", text_color="#F59E0B")

