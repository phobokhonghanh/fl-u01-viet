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
fotello_running = False
fotello_task_thread: threading.Thread | None = None
license_client = LicenseClient()


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
        }

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
        global fotello_running, fotello_task_thread
        license_ok, license_msg = ensure_license_active()
        if not license_ok:
            return {"ok": False, "msg": license_msg or "License chưa active"}
        if fotello_running:
            return {"ok": False, "msg": "Fotello task đang chạy"}
        fotello_running = True

        def _run() -> None:
            global fotello_running
            try:
                count = fotello.fotello_batch_download(
                    listing_ids, savedir, js_log, js_progress, lambda: not fotello_running
                )
                js_log(f"Fotello tải xong {count} ảnh", "success")
                js_status("idle", "Hoàn tất")
            except Exception as exc:
                print_system_exception("main.Api.fotello_download job", exc)
                js_log(f"Fotello lỗi: {exc}", "error")
                js_status("idle", "Lỗi")
            finally:
                fotello_running = False

        def _start_job() -> None:
            global fotello_task_thread
            js_status("running", "Đang tải Fotello...")
            js_progress_reset()
            fotello_task_thread = threading.Thread(target=_run, daemon=True)
            fotello_task_thread.start()

        threading.Timer(0.25, _start_job).start()
        return {"ok": True}

    def fotello_upload(
        self,
        inputdir: str,
        savedir: str,
        preferences: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        global fotello_running, fotello_task_thread
        license_ok, license_msg = ensure_license_active()
        if not license_ok:
            return {"ok": False, "msg": license_msg or "License chưa active"}
        if fotello_running:
            return {"ok": False, "msg": "Fotello task đang chạy"}
        fotello_running = True

        def _run() -> None:
            global fotello_running
            try:
                results = fotello.fotello_upload_and_enhance(
                    inputdir,
                    savedir,
                    js_log,
                    js_progress,
                    lambda: not fotello_running,
                    preferences,
                    settings,
                )
                js_log(f"Fotello upload/download xong {len(results)} ảnh", "success")
                js_status("idle", "Hoàn tất")
            except Exception as exc:
                print_system_exception("main.Api.fotello_upload job", exc)
                js_log(f"Fotello lỗi: {exc}", "error")
                js_status("idle", "Lỗi")
            finally:
                fotello_running = False

        def _start_job() -> None:
            global fotello_task_thread
            js_status("running", "Đang upload/enhance Fotello...")
            js_progress_reset()
            fotello_task_thread = threading.Thread(target=_run, daemon=True)
            fotello_task_thread.start()

        threading.Timer(0.25, _start_job).start()
        return {"ok": True}

    def fotello_stop(self) -> dict[str, Any]:
        global fotello_running
        fotello_running = False
        js_log("Đã yêu cầu dừng Fotello task.", "warn")
        js_status("idle", "Đã dừng")
        js_progress_reset()
        return {"ok": True}


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
    webview.start(debug=False, gui=gui)


if __name__ == "__main__":
    main()
