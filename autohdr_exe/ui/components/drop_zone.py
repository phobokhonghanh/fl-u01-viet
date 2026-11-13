"""
DropZone Component — Quản lý vùng thả ảnh (FILE) và ô nhập link (URL).
"""
import os
import customtkinter as ctk
from tkinter import filedialog
from tkinterdnd2 import DND_FILES
from typing import List, Callable, Optional


SUPPORTED_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".tif", ".tiff",
    ".dng", ".raw", ".arw", ".cr2", ".cr3", ".nef"
)


class DropZoneFrame(ctk.CTkFrame):
    def __init__(
        self,
        master,
        get_max_files_fn: Callable[[], int],
        on_files_changed: Optional[Callable[[int], None]] = None,
        **kwargs
    ):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.get_max_files_fn = get_max_files_fn
        self.on_files_changed = on_files_changed
        self.selected_files: List[str] = []
        self.is_manual_mode: bool = False

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._build_ui()

    def _build_ui(self):
        # 1. Khung kéo thả FILE
        self.drop_frame = ctk.CTkFrame(
            self,
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

        for widget in [self, self.drop_frame, self.drop_label]:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._on_drop)

        # Hàng hiển thị số lượng file và nút hủy chọn
        self.count_row = ctk.CTkFrame(self, fg_color="transparent")
        self.count_row.grid(row=1, column=0, sticky="ew", padx=2, pady=3)

        self.img_count_label = ctk.CTkLabel(self.count_row, text="0 files đã chọn", font=("Arial", 14, "bold"))
        self.img_count_label.pack(side="left", padx=3)

        self.btn_clear_files = ctk.CTkButton(
            self.count_row,
            text="✕ Hủy chọn",
            width=75,
            height=24,
            font=("Arial", 11),
            fg_color="transparent",
            text_color="#EF4444",
            hover_color=("#FEE2E2", "#374151"),
            command=self.clear,
        )
        # Ẩn ban đầu khi chưa có file

        # 2. Ô dán link URL
        self.share_url_entry = ctk.CTkEntry(self, placeholder_text="Dán link dự án...", height=34, font=("Arial", 13))
        self.share_url_entry.bind("<KeyRelease>", lambda e: self.url_status_label.configure(text=""))
        self.url_status_label = ctk.CTkLabel(
            self,
            text="",
            font=("Arial", 12),
            text_color="#EF4444",
            wraplength=350,
            justify="left",
        )

    def set_mode(self, is_manual: bool):
        """Chuyển đổi hiển thị giữa chế độ FILE và URL."""
        self.is_manual_mode = is_manual
        if is_manual:
            self.drop_frame.grid_remove()
            self.count_row.grid_remove()
            self.share_url_entry.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
            self.url_status_label.grid(row=1, column=0, sticky="w", padx=8, pady=(0, 4))
        else:
            self.share_url_entry.grid_remove()
            self.url_status_label.grid_remove()
            self.drop_frame.grid(row=0, column=0, sticky="nsew")
            self.count_row.grid(row=1, column=0, sticky="ew", padx=2, pady=3)

    def show_file_error(self, err_msg: str):
        """Hiển thị thông báo cảnh báo màu đỏ tại khu vực chọn file."""
        self.img_count_label.configure(text=err_msg, text_color="#EF4444")

    def _select_images(self):
        ext_str = ";".join(f"*{ext}" for ext in SUPPORTED_EXTENSIONS)
        files = filedialog.askopenfilenames(
            title="Chọn ảnh",
            filetypes=[("Image files", ext_str), ("All files", "*.*")],
        )
        if files:
            self.add_files(list(files))

    def _on_drop(self, event):
        paths = self.tk.splitlist(event.data)
        gathered = []
        for p in paths:
            if os.path.isfile(p):
                if p.lower().endswith(SUPPORTED_EXTENSIONS):
                    gathered.append(p)
            elif os.path.isdir(p):
                for root, _, fnames in os.walk(p):
                    for fn in fnames:
                        if fn.lower().endswith(SUPPORTED_EXTENSIONS):
                            gathered.append(os.path.join(root, fn))
        if gathered:
            self.add_files(gathered)

    def add_files(self, new_files: List[str]):
        """Thêm files mới và áp dụng kiểm tra giới hạn upload của Provider."""
        max_limit = self.get_max_files_fn()
        combined = list(dict.fromkeys(self.selected_files + new_files))
        if len(combined) > max_limit:
            self.selected_files = combined[:max_limit]
            self.img_count_label.configure(
                text=f"⚠️ {len(self.selected_files)}/{max_limit} files (Đã đạt giới hạn tối đa)",
                text_color=("#D97706", "#F59E0B")
            )
        else:
            self.selected_files = combined
            self.img_count_label.configure(
                text=f"{len(self.selected_files)} files đã chọn",
                text_color=("gray10", "gray90")
            )

        if len(self.selected_files) > 0:
            self.btn_clear_files.pack(side="left", padx=6)
        else:
            self.btn_clear_files.pack_forget()

        if self.on_files_changed:
            self.on_files_changed(len(self.selected_files))

    def update_limit_display(self):
        """Cập nhật lại nhãn đếm và cắt bớt file nếu engine mới có limit thấp hơn."""
        max_limit = self.get_max_files_fn()
        if len(self.selected_files) > max_limit:
            self.selected_files = self.selected_files[:max_limit]
        self.img_count_label.configure(
            text=f"{len(self.selected_files)} files đã chọn",
            text_color=("gray10", "gray90")
        )
        if len(self.selected_files) > 0:
            self.btn_clear_files.pack(side="left", padx=6)
        else:
            self.btn_clear_files.pack_forget()

    def clear(self):
        self.selected_files = []
        self.img_count_label.configure(text="0 files đã chọn", text_color=("gray10", "gray90"))
        self.btn_clear_files.pack_forget()
        self.share_url_entry.delete(0, "end")
        self.url_status_label.configure(text="")
        if self.on_files_changed:
            self.on_files_changed(0)

    def get_url(self) -> str:
        return self.share_url_entry.get().strip()

    def set_url_error(self, err_msg: str):
        self.url_status_label.configure(text=err_msg, text_color="#EF4444")

