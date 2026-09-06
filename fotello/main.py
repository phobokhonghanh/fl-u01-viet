"""
New Fotello desktop client entrypoint.

This is the active Fotello entrypoint. The old pipeline is archived in arch/.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import webview
except Exception:
    webview = None

import browser_runtime
from backend.client import print_system_exception
from backend.license import LicenseClient, get_machine_id
from backend import service as fotello


def get_base_dir() -> str:
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def get_exe_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_base_dir()
EXE_DIR = get_exe_dir()
UI_DIR = os.path.join(BASE_DIR, "ui")
RUNTIME_DIR = os.path.join(EXE_DIR, "runtime")
APP_ICON = os.path.join(UI_DIR, "logo.ico")

settings: dict[str, Any] = {
    "port": 9222,
    "fotello_url": "https://app.fotello.co",
    "timeout": 30,
    "delay": 0.3,
    "browser_strategy": "system_then_download",
    "browser_channel": "Stable",
    "browser_cache_dir": "",
    "browser_path_override": "",
    "poll_initial_interval": 60,
    "poll_later_interval": 30,
    "poll_initial_attempts": 10,
    "poll_ready_divisor": 2,
    "poll_timeout": 1800,
}

browser_state: dict[str, Any] = {"path": "", "source": "", "channel": "", "version": ""}
chrome_process: subprocess.Popen | None = None
window: Any = None
license_client = LicenseClient()
MAX_JOB_LOG_LINES = 500
JOB_SUMMARY_STATUSES = {"success", "partial", "failed", "stopped"}


@dataclass
class FotelloJob:
    job_id: str
    type: str
    name: str
    status: str = "pending"
    total_count: int = 0
    done_count: int = 0
    uploaded_count: int = 0
    # downloaded_count is the number of raw variants written to disk.  Keep it
    # separate from the cleaner counters below so a job with two raw variants
    # does not look like it produced two final images.
    downloaded_count: int = 0
    output_path: str = ""
    error: str = ""
    stop_requested: bool = False
    logs: list[str] = field(default_factory=list)
    raw_downloaded_count: int = 0
    target_count: int = 0
    cleaned_count: int = 0
    pending_count: int = 0
    preview_count: int = 0
    failed_count: int = 0
    unresolved_count: int = 0
    attempt: int = 0
    family_id: str = ""
    manifest_path: str = ""

    def snapshot(self, include_logs: bool = False) -> dict[str, Any]:
        data = {
            "job_id": self.job_id,
            "type": self.type,
            "name": self.name,
            "status": self.status,
            "total_count": self.total_count,
            "done_count": self.done_count,
            "uploaded_count": self.uploaded_count,
            "downloaded_count": self.downloaded_count,
            "raw_downloaded_count": self.raw_downloaded_count or self.downloaded_count,
            "target_count": self.target_count,
            "cleaned_count": self.cleaned_count,
            "pending_count": self.pending_count,
            "preview_count": self.preview_count,
            "failed_count": self.failed_count,
            "unresolved_count": self.unresolved_count,
            "attempt": self.attempt,
            "family_id": self.family_id,
            "manifest_path": self.manifest_path,
            "output_path": self.output_path,
            "error": self.error,
        }
        if include_logs:
            data["logs"] = list(self.logs)
        return data


class FotelloJobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, FotelloJob] = {}
        self._lock = threading.Lock()

    def create(self, job_type: str, name: str, output_path: str = "") -> FotelloJob:
        job = FotelloJob(job_id=str(uuid.uuid4())[:8], type=job_type, name=name, output_path=output_path)
        with self._lock:
            self.jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> FotelloJob | None:
        with self._lock:
            return self.jobs.get(job_id)

    def all(self) -> list[FotelloJob]:
        with self._lock:
            return list(self.jobs.values())

    def stop(self, job_id: str | None = None) -> list[FotelloJob]:
        with self._lock:
            if job_id:
                job = self.jobs.get(job_id)
                jobs = [job] if job else []
            else:
                jobs = list(self.jobs.values())
            for job in jobs:
                if job.status in ("pending", "running"):
                    job.stop_requested = True
                    job.status = "stopped"
            return jobs

    def append_log(self, job: FotelloJob, msg: str, msg_type: str = "") -> str:
        prefix = f"[{msg_type.upper()}] " if msg_type else ""
        line = f"[{time.strftime('%H:%M:%S')}] {prefix}{msg}"
        with self._lock:
            if len(job.logs) >= MAX_JOB_LOG_LINES:
                job.logs.pop(0)
            job.logs.append(line)
        return line


fotello_jobs = FotelloJobManager()


def _settings_path() -> str:
    return os.path.join(EXE_DIR, "settings.json")


def load_settings() -> dict[str, Any]:
    for path in (_settings_path(), os.path.join(BASE_DIR, "settings.json")):
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    settings.update(data)
                    break
        except Exception:
            pass
    return settings


def save_settings(new_settings: dict[str, Any]) -> bool:
    settings.update(new_settings or {})
    try:
        with open(_settings_path(), "w", encoding="utf-8") as handle:
            json.dump(settings, handle, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


def _js_string(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _js_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def js_call(function_name: str, *args: Any) -> None:
    if window:
        try:
            params = ", ".join(_js_json(arg) for arg in args)
            window.evaluate_js(f"{function_name}({params})")
        except Exception:
            pass


def js_log(msg: str, msg_type: str = "") -> None:
    try:
        with open(os.path.join(EXE_DIR, "fotello_debug.log"), "a", encoding="utf-8") as writer:
            writer.write(f"[{msg_type}] {msg}\n")
    except Exception:
        pass
    if window:
        try:
            window.evaluate_js(f"addLog({_js_string(msg)}, {_js_string(msg_type)})")
        except Exception:
            pass


fotello.set_request_logger(js_log)


def js_status(state: str, text: str) -> None:
    if window:
        try:
            window.evaluate_js(f"setStatus({_js_string(state)}, {_js_string(text)})")
        except Exception:
            pass


def js_progress(current: int, total: int) -> None:
    pct = int((current / total) * 100) if total > 0 else 0
    if window:
        try:
            window.evaluate_js(f"updateProgress({int(current)}, {int(total)}, {int(pct)})")
        except Exception:
            pass


def js_progress_reset() -> None:
    if window:
        try:
            window.evaluate_js("resetProgress()")
        except Exception:
            pass


def js_job_update(job: FotelloJob) -> None:
    js_call("jobStatus", job.snapshot())


def js_job_log(job: FotelloJob, msg: str, msg_type: str = "") -> None:
    line = fotello_jobs.append_log(job, msg, msg_type)
    js_call("jobLog", job.job_id, line)


def js_job_progress(job: FotelloJob, current: int, total: int) -> None:
    job.done_count = int(current)
    job.total_count = int(total)
    js_call("jobProgress", job.snapshot())


def js_job_counts(job: FotelloJob, uploaded: int | None = None, downloaded: int | None = None) -> None:
    if uploaded is not None:
        job.uploaded_count = int(uploaded)
    if downloaded is not None:
        job.downloaded_count = int(downloaded)
        job.raw_downloaded_count = int(downloaded)
    js_call("jobProgress", job.snapshot())


def _summary_int(value: Any, default: int = 0) -> int:
    """Read a summary counter without allowing malformed backend data to stop a job."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _coerce_job_summary(result: Any, job: FotelloJob) -> dict[str, Any]:
    """Use the shared workflow summary; raw download counts never imply clean success."""
    if isinstance(result, dict):
        return dict(result)
    downloaded = len(result) if isinstance(result, (list, tuple)) else _summary_int(result)
    target = job.target_count or job.total_count
    return {
        "target_count": target, "downloaded_count": downloaded,
        "cleaned_count": 0, "pending_count": target,
        "preview_count": 0, "failed_count": 0,
        "attempt": job.attempt, "family_id": job.family_id,
        "manifest_path": job.manifest_path, "output_path": job.output_path,
        "status": "partial" if downloaded or target else "failed",
    }

def _apply_job_summary(job: FotelloJob, result: Any, *, live: bool = False) -> dict[str, Any]:
    """Copy cleaner progress into a job without letting live status end it."""
    summary = _coerce_job_summary(result, job)

    counter_fields = (
        "target_count",
        "cleaned_count",
        "pending_count",
        "preview_count",
        "failed_count",
        "unresolved_count",
        "attempt",
    )
    for field_name in counter_fields:
        if field_name in summary and summary[field_name] is not None:
            setattr(job, field_name, _summary_int(summary[field_name]))

    if "downloaded_count" in summary and summary["downloaded_count"] is not None:
        job.downloaded_count = _summary_int(summary["downloaded_count"])
        job.raw_downloaded_count = job.downloaded_count
    elif "raw_downloaded_count" in summary and summary["raw_downloaded_count"] is not None:
        job.raw_downloaded_count = _summary_int(summary["raw_downloaded_count"])
        job.downloaded_count = job.raw_downloaded_count

    for field_name in ("family_id", "manifest_path", "output_path"):
        value = summary.get(field_name)
        if value:
            setattr(job, field_name, str(value))

    # Keep a useful progress denominator for callers that still consume the
    # legacy progress fields.  The dedicated target/cleaned counters remain
    # authoritative in the UI.
    if job.target_count > 0 and live:
        job.done_count = min(job.target_count, job.cleaned_count)
        job.total_count = job.target_count

    if not live:
        status = str(summary.get("status") or "").lower()
        if status in JOB_SUMMARY_STATUSES:
            job.status = status

    return summary


def js_job_summary(job: FotelloJob, summary: Any) -> None:
    """Handle a live summary callback from either watermark workflow."""
    _apply_job_summary(job, summary, live=True)
    js_job_update(job)


def _summary_callback(job: FotelloJob):
    """Build a callback compatible with summary emitters using one payload."""
    def callback(summary: Any = None, **fields: Any) -> None:
        payload = summary if summary is not None else fields
        js_job_summary(job, payload)

    return callback


def _finish_job(job: FotelloJob, result: Any) -> dict[str, Any]:
    """Apply the final workflow summary, preserving an explicit stop request."""
    summary = _apply_job_summary(job, result)
    if job.stop_requested:
        job.status = "stopped"
        return summary

    status = str(summary.get("status") or "").lower()
    if status not in JOB_SUMMARY_STATUSES:
        target = job.target_count or _summary_int(summary.get("target_count"))
        cleaned = job.cleaned_count
        pending = job.pending_count
        failed = job.failed_count
        status = "success" if target > 0 and cleaned >= target and pending == 0 and failed == 0 else "partial"
    if status == "success" and (
        job.target_count < 1 or job.cleaned_count != job.target_count
        or job.pending_count or job.failed_count or job.preview_count or job.unresolved_count
    ):
        status = "partial"
    job.status = status
    return summary


def open_ui_page(filename: str) -> bool:
    if not window:
        return False
    path = os.path.join(UI_DIR, filename)
    try:
        window.load_url(Path(path).resolve().as_uri())
        return True
    except Exception as exc:
        js_log(f"Không load được UI {filename}: {exc}", "error")
        return False


def ensure_license_active() -> tuple[bool, str]:
    result = license_client.check_key(use_cache=True)
    if result.ok:
        return True, result.msg
    js_log(result.msg or "License chưa active", "error")
    threading.Timer(0.8, lambda: open_ui_page("license.html")).start()
    return False, result.msg


def _browser_dict(resolution: browser_runtime.BrowserResolution | None) -> dict[str, Any]:
    if resolution is None:
        return {"path": "", "source": "", "channel": "", "version": ""}
    return {
        "path": resolution.path,
        "source": resolution.source,
        "channel": resolution.channel,
        "version": resolution.version,
    }


def _set_browser_state(resolution: browser_runtime.BrowserResolution | None) -> dict[str, Any]:
    browser_state.update(_browser_dict(resolution))
    return dict(browser_state)


def inspect_browser_state() -> dict[str, Any]:
    return _set_browser_state(browser_runtime.inspect_browser(settings))


def resolve_browser_state(force_download: bool = False) -> dict[str, Any]:
    resolution = browser_runtime.resolve_browser(
        settings,
        log_fn=js_log,
        progress_fn=js_progress,
        force_download=force_download,
    )
    return _set_browser_state(resolution)


def get_profile_dir() -> str:
    return str(browser_runtime.get_profiles_root())


def ensure_profile() -> None:
    Path(get_profile_dir()).mkdir(parents=True, exist_ok=True)


def _launch_browser(path: str, target_url: str) -> subprocess.Popen[Any]:
    cmd = [
        path,
        f"--remote-debugging-port={settings.get('port', 9222)}",
        f"--user-data-dir={get_profile_dir()}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-infobars",
        target_url,
    ]
    if sys.platform.startswith("linux"):
        cmd.insert(-1, "--remote-allow-origins=*")
    return subprocess.Popen(cmd)


def do_open_chrome(target_url: str | None = None, force_download: bool = False) -> bool:
    global chrome_process
    ensure_profile()
    target_url = target_url or settings.get("fotello_url", "https://app.fotello.co")
    js_progress_reset()
    try:
        current = resolve_browser_state(force_download=force_download)
    except browser_runtime.BrowserRuntimeError as exc:
        js_log(f"{exc.code}: {exc.message}", "error")
        js_status("idle", "Không mở được browser")
        return False
    browser_path = current.get("path", "")
    if not browser_path:
        js_log("BR_NOT_FOUND: Không xác định được browser để mở.", "error")
        js_status("idle", "Không có browser")
        return False
    try:
        if chrome_process and chrome_process.poll() is None:
            js_log("Chrome đang mở.", "info")
            return True
        js_log(f"Mở browser ({current.get('source') or 'unknown'}) tại {browser_path}", "info")
        chrome_process = _launch_browser(browser_path, target_url)
        js_log(f"Đã mở Chrome PID {chrome_process.pid}", "success")
        return True
    except Exception as exc:
        js_log(f"BR_LAUNCH: Mở browser thất bại ({browser_path}): {exc}", "error")
        if current.get("source") == "system" and not force_download:
            js_log("Thử fallback sang Chrome runtime tự động.", "warn")
            return do_open_chrome(target_url=target_url, force_download=True)
        js_status("idle", "Mở browser thất bại")
        return False


def shutil_which(command: str) -> str:
    import shutil

    return shutil.which(command) or ""


def get_driver() -> Any:
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
    except Exception as exc:
        raise RuntimeError(f"Thiếu selenium: {exc}") from exc

    current = dict(browser_state)
    browser_path = current.get("path", "")
    options = Options()
    options.add_experimental_option("debuggerAddress", f"127.0.0.1:{settings.get('port', 9222)}")
    if browser_path and os.path.exists(browser_path):
        options.binary_location = browser_path

    driver_path = ""
    if sys.platform.startswith("win"):
        local_driver = os.path.join(RUNTIME_DIR, "bin", "chromedriver.exe")
        if os.path.exists(local_driver):
            driver_path = local_driver
    else:
        driver_path = shutil_which("chromedriver")
    try:
        js_log(f"Đang attach Selenium vào browser 127.0.0.1:{settings.get('port', 9222)}", "info")
        if driver_path:
            return webdriver.Chrome(service=Service(driver_path), options=options)
        return webdriver.Chrome(options=options)
    except Exception as exc:
        js_log(f"DRV_ATTACH: Selenium attach thất bại: {exc}", "error")
        raise RuntimeError(f"Lỗi kết nối Selenium/Chrome: {exc}") from exc


class Api:
    def license_status(self) -> dict[str, Any]:
        result = license_client.check_key(use_cache=True)
        return {
            "ok": result.ok,
            "has_key": bool(result.key),
            "machine_id": result.machine_id,
            "key": result.key,
            "cached": result.cached,
            "message": result.msg,
            "last_check": (result.data or {}).get("license_last_check", 0) if isinstance(result.data, dict) else 0,
            "level": result.level,
        }

    def get_pricing(self) -> dict[str, str]:
        return license_client.get_pricing()

    def license_get_machine_id(self) -> str:
        return get_machine_id()

    def license_activate(self, key: str) -> dict[str, Any]:
        result = license_client.check_key(key, use_cache=False)
        if result.ok:
            threading.Timer(0.8, lambda: open_ui_page("index.html")).start()
        return {
            "ok": result.ok,
            "msg": result.msg,
            "cached": result.cached,
            "machine_id": result.machine_id,
        }

    def license_open_main(self) -> bool:
        ok, _msg = ensure_license_active()
        if not ok:
            return False
        # Defer navigation so PyWebView can resolve the JS API callback first.
        threading.Timer(0.8, lambda: open_ui_page("index.html")).start()
        return True

    def open_url(self, url: str) -> None:
        import webbrowser
        try:
            webbrowser.open(url)
        except Exception as exc:
            print_system_exception("main.Api.open_url", exc)

    def get_chrome_path(self) -> str:
        return inspect_browser_state().get("path", "")

    def get_browser_info(self) -> dict[str, Any]:
        return inspect_browser_state()

    def repair_browser_runtime(self) -> dict[str, Any]:
        try:
            browser_runtime.clear_downloaded_runtime(settings, js_log)
            return {"ok": True, "browser": resolve_browser_state(force_download=True)}
        except browser_runtime.BrowserRuntimeError as exc:
            print_system_exception("main.Api.repair_browser_runtime", exc)
            js_log(f"{exc.code}: {exc.message}", "error")
            return {"ok": False, "msg": f"{exc.code}: {exc.message}"}

    def get_settings(self) -> dict[str, Any]:
        return load_settings()

    def save_settings(self, new_settings: dict[str, Any]) -> bool:
        ok = save_settings(new_settings)
        inspect_browser_state()
        return ok

    def browse_folder(self) -> str:
        if webview is None:
            return ""
        try:
            result = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
            if isinstance(result, (list, tuple)) and result:
                return result[0]
            return result or ""
        except Exception as exc:
            print_system_exception("main.Api.browse_folder", exc)
            return ""

    def fotello_open_chrome(self) -> bool:
        js_log("Mở Chrome tới Fotello.", "info")
        return do_open_chrome(settings.get("fotello_url", "https://app.fotello.co"))

    def fotello_connect(self) -> dict[str, Any]:
        try:
            if not self.fotello_open_chrome():
                return {"ok": False, "msg": "Không mở được browser để kết nối Fotello"}
            time.sleep(float(settings.get("delay", 0.3)) + 0.7)
            ok = fotello.fotello_grab_tokens_from_browser(get_driver(), js_log)
            status = fotello.fotello_get_status()
            js_status("idle", "Đã kết nối Fotello" if ok else "Chưa kết nối")
            return {"ok": bool(ok), "status": status}
        except Exception as exc:
            print_system_exception("main.Api.fotello_connect", exc)
            js_log(f"Kết nối Fotello lỗi: {exc}", "error")
            js_status("idle", "Lỗi kết nối")
            return {"ok": False, "msg": str(exc)}

    def fotello_reconnect(self) -> dict[str, Any]:
        ok = fotello.fotello_reconnect_saved(js_log)
        return {"ok": bool(ok), "status": fotello.fotello_get_status()}

    def fotello_status(self) -> dict[str, Any]:
        return fotello.fotello_get_status()

    def fotello_list_listings(self) -> dict[str, Any]:
        try:
            return {"ok": True, "listings": fotello.fotello_list_listings(js_log)}
        except Exception as exc:
            print_system_exception("main.Api.fotello_list_listings", exc)
            js_log(f"Fotello lỗi: {exc}", "error")
            return {"ok": False, "msg": str(exc), "listings": []}

    def fotello_download(self, listing_ids: list[str], savedir: str) -> dict[str, Any]:
        license_ok, license_msg = ensure_license_active()
        if not license_ok:
            return {"ok": False, "msg": license_msg or "License chưa active"}
        if not listing_ids:
            return {"ok": False, "msg": "Chưa chọn listing nào"}
        if not savedir:
            return {"ok": False, "msg": "Chưa chọn thư mục lưu"}

        job = fotello_jobs.create("download", f"Download {len(listing_ids)} listings")
        job_output_dir = Path(savedir) / job.job_id
        job.output_path = str(job_output_dir)
        try:
            job_output_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Let the worker report the actionable write error while keeping
            # the job visible in the UI.
            pass
        job.status = "running"
        js_job_update(job)

        def _run() -> None:
            js_job_update(job)
            try:
                result = fotello.fotello_batch_download(
                    listing_ids,
                    str(job_output_dir),
                    lambda msg, msg_type="": js_job_log(job, msg, msg_type),
                    lambda cur, total: js_job_progress(job, cur, total),
                    lambda: job.stop_requested,
                    summary_fn=_summary_callback(job),
                )
                summary = _finish_job(job, result)
                if job.status == "stopped":
                    js_job_log(job, "Đã dừng job tải.", "warn")
                elif job.status == "success":
                    js_job_log(job, f"Fotello tải và xóa watermark xong {job.cleaned_count}/{job.target_count} ảnh", "success")
                elif job.status == "partial":
                    js_job_log(job, f"Fotello hoàn tất một phần: sạch {job.cleaned_count}/{job.target_count}, còn chờ {job.pending_count} ảnh", "warn")
                else:
                    js_job_log(job, f"Fotello kết thúc với trạng thái {job.status}.", "error")
            except Exception as exc:
                print_system_exception("main.Api.fotello_download job", exc)
                job.status = "failed"
                job.error = str(exc)
                js_job_log(job, f"Fotello lỗi: {exc}", "error")
            finally:
                js_job_update(job)
                self._set_jobs_status()

        threading.Thread(target=_run, daemon=True).start()
        self._set_jobs_status()
        return {"ok": True, "job": job.snapshot(include_logs=True)}

    def fotello_upload(
        self,
        inputdir: str,
        savedir: str,
        preferences: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        license_ok, license_msg = ensure_license_active()
        if not license_ok:
            return {"ok": False, "msg": license_msg or "License chưa active"}
        if not inputdir:
            return {"ok": False, "msg": "Chưa chọn thư mục ảnh gốc"}
        if not savedir:
            return {"ok": False, "msg": "Chưa chọn thư mục lưu kết quả"}
        preferences = preferences or {}
        listing_prefix = str(preferences.get("listing_name_prefix") or "").strip() or "AutoHDR Upload"
        job_name = listing_prefix + " - " + time.strftime("%d %m, %Y %H:%M")
        job = fotello_jobs.create("upload", job_name)
        job_output_dir = Path(savedir) / job.job_id
        job.output_path = str(job_output_dir)
        try:
            job_output_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        job.status = "running"
        js_job_update(job)

        license_result = license_client.check_key(use_cache=True)
        license_level = license_result.level if license_result.ok else "lite"

        def _run() -> None:
            js_job_update(job)
            try:
                count = fotello.fotello_upload_and_enhance(
                    inputdir,
                    str(job_output_dir),
                    lambda msg, msg_type="": js_job_log(job, msg, msg_type),
                    lambda cur, total: js_job_progress(job, cur, total),
                    lambda: job.stop_requested,
                    preferences,
                    settings,
                    lambda uploaded=None, downloaded=None: js_job_counts(job, uploaded, downloaded),
                    license_level=license_level,
                    summary_fn=_summary_callback(job),
                )
                summary = _finish_job(job, count)
                if job.status == "stopped":
                    js_job_log(job, "Đã dừng job upload/enhance.", "warn")
                elif job.status == "success":
                    js_job_log(job, f"Upload và xóa watermark xong {job.cleaned_count}/{job.target_count} ảnh", "success")
                elif job.status == "partial":
                    js_job_log(job, f"Upload hoàn tất một phần: sạch {job.cleaned_count}/{job.target_count}, còn chờ {job.pending_count} ảnh", "warn")
                else:
                    js_job_log(job, f"Fotello kết thúc với trạng thái {job.status}.", "error")
            except Exception as exc:
                print_system_exception("main.Api.fotello_upload job", exc)
                job.status = "failed"
                job.error = str(exc)
                js_job_log(job, f"Fotello lỗi: {exc}", "error")
            finally:
                js_job_update(job)
                self._set_jobs_status()

        threading.Thread(target=_run, daemon=True).start()
        self._set_jobs_status()
        return {"ok": True, "job": job.snapshot(include_logs=True)}

    def fotello_jobs(self) -> list[dict[str, Any]]:
        return [job.snapshot() for job in fotello_jobs.all()]

    def fotello_job_logs(self, job_id: str) -> list[str]:
        job = fotello_jobs.get(job_id)
        return list(job.logs) if job else []

    def fotello_open_job_folder(self, job_id: str) -> dict[str, Any]:
        job = fotello_jobs.get(job_id)
        if not job or not job.output_path:
            return {"ok": False, "msg": "Job chưa có thư mục output"}
        path = os.path.abspath(job.output_path)
        if not os.path.exists(path):
            return {"ok": False, "msg": "Thư mục output không tồn tại"}
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
            return {"ok": True}
        except Exception as exc:
            print_system_exception("main.Api.fotello_open_job_folder", exc)
            return {"ok": False, "msg": str(exc)}

    def fotello_stop(self, job_id: str | None = None) -> dict[str, Any]:
        stopped = fotello_jobs.stop(job_id)
        for job in stopped:
            js_job_log(job, "Đã yêu cầu dừng Fotello job.", "warn")
            js_job_update(job)
        self._set_jobs_status()
        return {"ok": True, "jobs": [job.snapshot() for job in stopped]}

    def _running_job_count(self) -> int:
        return sum(1 for job in fotello_jobs.all() if job.status == "running")

    def _set_jobs_status(self) -> None:
        running = self._running_job_count()
        if running:
            js_status("running", f"{running} job đang chạy")
        else:
            js_status("idle", "Sẵn sàng")


def main() -> None:
    global window
    if "--smoke-test" in sys.argv:
        load_settings()
        print("Fotello smoke test OK")
        return
    load_settings()
    inspect_browser_state()
    if webview is None:
        raise RuntimeError("pywebview chưa được cài. Chạy: pip install -r requirements.txt")
    start_page = "index.html" if license_client.check_key(use_cache=True).ok else "license.html"
    index = os.path.join(UI_DIR, start_page)
    window = webview.create_window("Fotello Client", index, width=1180, height=760, js_api=Api())
    gui = "qt" if sys.platform.startswith("linux") else None
    webview.start(debug=False, gui=gui, icon=APP_ICON if os.path.exists(APP_ICON) else None)


if __name__ == "__main__":
    main()
