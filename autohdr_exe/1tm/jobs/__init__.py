"""
Package Jobs: Chứa các Job Executor cụ thể cho từng Engine/Provider:
- AutoHDRJob
- AutoenhanceJob
- JobRunner (Bộ điều phối tác vụ chạy nền cho UI)

Tự động đăng ký các job vào ExecutorRegistry để sử dụng linh hoạt theo Strategy/Registry Pattern.
"""
from libs.executor.base import ExecutorRegistry
from .autohdr import AutoHDRJob
from .autohdr_download import AutoHDRDownloadJob
from .autoenhance import AutoenhanceJob
from .autoenhance_download import AutoenhanceDownloadJob
from .runner import JobRunner

# Tự động đăng ký các Provider vào Registry
ExecutorRegistry.register("autohdr", AutoHDRJob)
ExecutorRegistry.register("autohdr_download", AutoHDRDownloadJob)
ExecutorRegistry.register("autoenhance", AutoenhanceJob)
ExecutorRegistry.register("autoenhance_download", AutoenhanceDownloadJob)

__all__ = [
    "AutoHDRJob",
    "AutoHDRDownloadJob",
    "AutoenhanceJob",
    "AutoenhanceDownloadJob",
    "JobRunner",
]
