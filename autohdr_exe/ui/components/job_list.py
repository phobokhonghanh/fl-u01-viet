"""
JobList Component — Quản lý danh sách tác vụ dạng cuộn bên cột trái.
"""
import customtkinter as ctk
from typing import Dict, Any, Callable, Optional


class JobListFrame(ctk.CTkFrame):
    def __init__(
        self,
        master,
        on_select_job: Callable[[str], None],
        on_clear_jobs: Callable[[], None],
        on_scan_failed: Callable[[], None],
        on_stop_job: Callable[[str], None],
        on_resume_job: Callable[[str], None],
        on_open_folder: Callable[[str], None],
        **kwargs
    ):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.on_select_job = on_select_job
        self.on_clear_jobs = on_clear_jobs
        self.on_scan_failed = on_scan_failed
        self.on_stop_job = on_stop_job
        self.on_resume_job = on_resume_job
        self.on_open_folder = on_open_folder

        self.job_widgets: Dict[str, ctk.CTkFrame] = {}
        self.job_engine_map: Dict[str, str] = {}
        self.selected_job_id: Optional[str] = None
        self.current_web: str = "autohdr"

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._build_ui()

    def _build_ui(self):
        # Header: Danh sách | Clear | Quét job lỗi
        job_header = ctk.CTkFrame(self, fg_color="transparent")
        job_header.grid(row=0, column=0, sticky="ew", padx=2, pady=(2, 2))
        ctk.CTkLabel(job_header, text="Danh sách", font=("Arial", 16, "bold")).pack(side="left", anchor="w")

        ctk.CTkButton(
            job_header, text="Clear", width=65, height=26,
            command=self.on_clear_jobs, fg_color="#DC2626", hover_color="#B91C1C", font=("Arial", 12, "bold")
        ).pack(side="right", padx=3)

        ctk.CTkButton(
            job_header, text="Quét job lỗi", width=85, height=26,
            command=self.on_scan_failed, fg_color="#4B5563", hover_color="#374151", font=("Arial", 11, "bold")
        ).pack(side="right", padx=3)

        # Scrollable list
        self.list_frame = ctk.CTkScrollableFrame(self, label_text="", height=140)
        self.list_frame.grid(row=1, column=0, sticky="nsew", padx=2, pady=(2, 4))
        self.list_frame.bind_all("<Button-4>", lambda e: self._on_mousewheel(e, self.list_frame))
        self.list_frame.bind_all("<Button-5>", lambda e: self._on_mousewheel(e, self.list_frame))

    def _on_mousewheel(self, event, widget):
        try:
            x, y = event.x_root, event.y_root
            hovered = widget.winfo_containing(x, y)
            if hovered and (str(widget) in str(hovered) or str(hovered) in str(widget)):
                if event.num == 4:
                    widget._parent_canvas.yview_scroll(-1, "units")
                elif event.num == 5:
                    widget._parent_canvas.yview_scroll(1, "units")
                else:
                    widget._parent_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            pass

    def add_job_card(self, job_id: str, address: str = "", file_count: int = 0, engine: str = "autohdr", status: str = "running"):
        """Tạo card tiến trình cho một tác vụ."""
        job_id = str(job_id)
        self.job_engine_map[job_id] = engine

        job_row = ctk.CTkFrame(self.list_frame, cursor="hand2")
        job_row.job_id = job_id
        job_row.bind("<Button-1>", lambda e, j=job_id: self.select_job(j))

        info_frame = ctk.CTkFrame(job_row, fg_color="transparent")
        info_frame.pack(side="left", fill="x", expand=True, padx=5, pady=3)
        info_frame.bind("<Button-1>", lambda e, j=job_id: self.select_job(j))

        clean_id = job_id.replace("job_", "")
        name_label = ctk.CTkLabel(
            info_frame,
            text=f"Job {clean_id} | {address[:25]}",
            font=("Arial", 14, "bold"),
        )
        name_label.pack(anchor="w")
        name_label.bind("<Button-1>", lambda e, j=job_id: self.select_job(j))

        status_text, color = self._get_status_label_info(status, file_count)
        status_label = ctk.CTkLabel(
            info_frame,
            text=status_text,
            font=("Arial", 12),
            text_color=color,
        )
        status_label.pack(anchor="w")
        status_label.bind("<Button-1>", lambda e, j=job_id: self.select_job(j))
        job_row.status_label = status_label
        job_row.file_count = file_count

        # Các nút hành động trên card
        job_row.stop_btn = ctk.CTkButton(
            job_row, text="⏹", width=36, height=28,
            command=lambda jid=job_id: self.on_stop_job(jid),
            fg_color="#DC2626", hover_color="#B91C1C", font=("Arial", 12, "bold")
        )
        job_row.restart_btn = ctk.CTkButton(
            job_row, text="🔄", width=36, height=28,
            command=lambda jid=job_id: self.on_resume_job(jid),
            fg_color="#F59E0B", hover_color="#D97706", font=("Arial", 12, "bold")
        )
        job_row.folder_btn = ctk.CTkButton(
            job_row, text="📂", width=36, height=28,
            command=lambda jid=job_id: self.on_open_folder(jid),
            fg_color="#6B7280", hover_color="#4B5563", font=("Arial", 12)
        )

        self._update_card_buttons(job_row, status)

        self.job_widgets[job_id] = job_row

        if engine == self.current_web:
            job_row.pack(fill="x", pady=2, padx=2)

        try:
            self.list_frame._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _get_status_label_info(self, status: str, file_count: int):
        if status in ("running", "processing"):
            return f"{file_count} ảnh | Đang xử lý...", "#9CA3AF"
        elif status == "completed":
            return f"{file_count} ảnh | Hoàn thành", "#22C55E"
        elif status == "failed":
            return f"{file_count} ảnh | Lỗi", "#EF4444"
        elif status in ("stopped", "cancelled"):
            return f"{file_count} ảnh | Đã dừng", "#F59E0B"
        return f"{file_count} ảnh | {status}", "gray"

    def _update_card_buttons(self, job_row, status: str):
        job_row.stop_btn.pack_forget()
        job_row.restart_btn.pack_forget()
        job_row.folder_btn.pack_forget()

        if status in ("running", "processing"):
            job_row.stop_btn.pack(side="right", padx=5, pady=3)
        elif status == "completed":
            job_row.folder_btn.pack(side="right", padx=5, pady=3)
        elif status in ("failed", "cancelled", "stopped"):
            job_row.restart_btn.pack(side="right", padx=5, pady=3)

    def set_selected_job(self, job_id: Optional[str]):
        """Cập nhật card được chọn mà không kích hoạt callback on_select_job."""
        self.selected_job_id = job_id
        for jid, card in self.job_widgets.items():
            if jid == job_id:
                card.configure(fg_color=("#D1D5DB", "#374151"))
            else:
                card.configure(fg_color=("gray90", "gray17"))

    def select_job(self, job_id: str):
        self.set_selected_job(job_id)
        self.on_select_job(job_id)

    def update_job_status(self, job_id: str, status: str):
        card = self.job_widgets.get(job_id)
        if card:
            file_count = getattr(card, "file_count", 0)
            text, color = self._get_status_label_info(status, file_count)
            card.status_label.configure(text=text, text_color=color)
            self._update_card_buttons(card, status)

    def filter_by_web(self, new_web: str):
        self.current_web = new_web
        first_visible = None
        for jid, card in self.job_widgets.items():
            if self.job_engine_map.get(jid) == new_web:
                card.pack(fill="x", pady=2, padx=2)
                if not first_visible:
                    first_visible = jid
            else:
                card.pack_forget()

        if self.selected_job_id and self.job_engine_map.get(self.selected_job_id) != new_web:
            if first_visible:
                self.select_job(first_visible)
            else:
                self.selected_job_id = None
                self.on_select_job(None)

    def remove_jobs(self, job_ids: list):
        for jid in job_ids:
            card = self.job_widgets.pop(jid, None)
            if card:
                card.destroy()
            self.job_engine_map.pop(jid, None)
        if self.selected_job_id in job_ids:
            remaining = [jid for jid in self.job_widgets if self.job_engine_map.get(jid) == self.current_web]
            if remaining:
                self.select_job(remaining[-1])
            else:
                self.selected_job_id = None
                self.on_select_job(None)
