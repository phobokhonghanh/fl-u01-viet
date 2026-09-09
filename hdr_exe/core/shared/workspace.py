"""Temporary Workspace Management for HDR Engines.

Provides an isolated, auto-cleaning temporary working directory for each workflow run,
ensuring temporary artifacts are cleaned up on success, error, or cancellation.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any


class TemporaryWorkspace:
    """Context manager quản lý thư mục tạm cho mỗi lần chạy.

    - Tạo thư mục tạm riêng biệt với prefix đã chỉ định.
    - Dọn dẹp an toàn trong __exit__ khi hoàn thành, lỗi hoặc dừng tác vụ.
    - Chỉ xóa tài nguyên thuộc về phiên làm việc này.
    """

    def __init__(self, prefix: str = "hdr_workspace_", base_dir: Path | str | None = None) -> None:
        self.prefix = prefix
        self.base_dir = Path(base_dir) if base_dir else None
        self.path: Path | None = None

    def __enter__(self) -> Path:
        base = str(self.base_dir) if self.base_dir else None
        tmp_dir = tempfile.mkdtemp(prefix=self.prefix, dir=base)
        self.path = Path(tmp_dir)
        return self.path

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        """Dọn dẹp thư mục tạm và toàn bộ file bên trong."""
        if self.path and self.path.exists():
            shutil.rmtree(self.path, ignore_errors=True)
            self.path = None
