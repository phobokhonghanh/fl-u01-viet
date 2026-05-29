from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests


LOG_FN = Callable[[str, str], None] | None
PROGRESS_FN = Callable[[int, int], None] | None

CFT_ENDPOINT = (
    "https://googlechromelabs.github.io/chrome-for-testing/"
    "last-known-good-versions-with-downloads.json"
)
APP_DIR_NAME = "FotelloClient"
LOCK_TIMEOUT_SECONDS = 180
CHUNK_SIZE = 1024 * 256


@dataclass
class BrowserResolution:
    path: str
    source: str
    channel: str
    version: str = ""
    metadata_path: str = ""
    runtime_dir: str = ""


class BrowserRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _log(log_fn: LOG_FN, msg: str, msg_type: str = "info") -> None:
    if log_fn:
        log_fn(msg, msg_type)


def _progress(progress_fn: PROGRESS_FN, current: int, total: int) -> None:
    if progress_fn:
        progress_fn(current, total)


def get_appdata_root() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_DIR_NAME
        return Path.home() / "AppData" / "Local" / APP_DIR_NAME
    return Path.home() / ".local" / "share" / APP_DIR_NAME


def get_runtime_root(settings: dict[str, Any]) -> Path:
    override = str(settings.get("browser_cache_dir") or "").strip()
    if override:
        return Path(os.path.expandvars(os.path.expanduser(override)))
    return get_appdata_root() / "runtime"


def get_profiles_root() -> Path:
    return get_appdata_root() / "chrome_profiles" / "fotello"


def get_browser_metadata_path(settings: dict[str, Any]) -> Path:
    return get_runtime_root(settings) / "browser.json"


def _platform_name() -> str:
    if os.name == "nt":
        return "win64" if platform.machine().endswith("64") else "win32"
    if platform.system() == "Darwin":
        return "mac-arm64" if platform.machine().lower() in ("arm64", "aarch64") else "mac-x64"
    return "linux64"


def _browser_binary_relpath(platform_name: str) -> str:
    mapping = {
        "win32": "chrome-win32/chrome.exe",
        "win64": "chrome-win64/chrome.exe",
        "linux64": "chrome-linux64/chrome",
        "mac-x64": "chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        "mac-arm64": "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    }
    return mapping[platform_name]


def _candidate_system_browsers() -> list[tuple[str, Path]]:
    if os.name == "nt":
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local_appdata = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return [
            ("Google Chrome", Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe"),
            ("Google Chrome", Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe"),
            ("Google Chrome", Path(local_appdata) / "Google" / "Chrome" / "Application" / "chrome.exe"),
            ("Microsoft Edge", Path(program_files) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
            ("Microsoft Edge", Path(program_files_x86) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
            ("Chromium", Path(program_files) / "Chromium" / "Application" / "chrome.exe"),
            ("Chromium", Path(program_files_x86) / "Chromium" / "Application" / "chrome.exe"),
        ]
    return [
        ("Google Chrome", Path(p))
        for p in filter(
            None,
            (
                shutil.which("google-chrome"),
                shutil.which("google-chrome-stable"),
                shutil.which("chromium"),
                shutil.which("chromium-browser"),
                shutil.which("microsoft-edge"),
            ),
        )
    ]


def detect_system_browser(log_fn: LOG_FN = None) -> BrowserResolution | None:
    for label, candidate in _candidate_system_browsers():
        if candidate.exists():
            _log(log_fn, f"Dung browser he thong: {label} -> {candidate}", "info")
            return BrowserResolution(
                path=str(candidate),
                source="system",
                channel="system",
            )
    _log(log_fn, "Khong tim thay browser he thong phu hop.", "warn")
    return None


def _metadata_payload(resolution: BrowserResolution) -> dict[str, Any]:
    return {
        "path": resolution.path,
        "source": resolution.source,
        "channel": resolution.channel,
        "version": resolution.version,
        "runtime_dir": resolution.runtime_dir,
        "updated_at": int(time.time()),
    }


def _write_metadata(settings: dict[str, Any], resolution: BrowserResolution) -> None:
    path = get_browser_metadata_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(_metadata_payload(resolution), handle, indent=2, ensure_ascii=False)


def _load_metadata(settings: dict[str, Any]) -> dict[str, Any]:
    path = get_browser_metadata_path(settings)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _is_executable(path: Path) -> bool:
    return path.exists() and path.is_file()


def _validate_cached_browser(path: Path) -> bool:
    if not _is_executable(path):
        return False
    if os.name != "nt":
        try:
            mode = path.stat().st_mode
            if not mode & stat.S_IXUSR:
                path.chmod(mode | stat.S_IXUSR)
        except Exception:
            return False
    return True


def _runtime_resolution(version: str, runtime_dir: Path, platform_name: str, channel: str) -> BrowserResolution:
    browser_path = runtime_dir / _browser_binary_relpath(platform_name)
    return BrowserResolution(
        path=str(browser_path),
        source="downloaded",
        channel=channel,
        version=version,
        metadata_path=str(runtime_dir.parent / "browser.json"),
        runtime_dir=str(runtime_dir),
    )


def _load_cached_runtime(settings: dict[str, Any], log_fn: LOG_FN = None) -> BrowserResolution | None:
    metadata = _load_metadata(settings)
    if not metadata:
        return None
    browser_path = Path(str(metadata.get("path") or ""))
    if not browser_path:
        return None
    if _validate_cached_browser(browser_path):
        _log(log_fn, f"Dung Chrome runtime da tai truoc do: {browser_path}", "info")
        return BrowserResolution(
            path=str(browser_path),
            source=str(metadata.get("source") or "downloaded"),
            channel=str(metadata.get("channel") or "Stable"),
            version=str(metadata.get("version") or ""),
            metadata_path=str(get_browser_metadata_path(settings)),
            runtime_dir=str(metadata.get("runtime_dir") or ""),
        )
    _log(log_fn, "Cache runtime ton tai nhung khong hop le, se tai lai.", "warn")
    runtime_dir = metadata.get("runtime_dir")
    if runtime_dir:
        shutil.rmtree(runtime_dir, ignore_errors=True)
    return None


def _fetch_cft_payload(log_fn: LOG_FN = None) -> dict[str, Any]:
    _log(log_fn, f"Tai metadata Chrome runtime tu {CFT_ENDPOINT}", "info")
    try:
        response = requests.get(CFT_ENDPOINT, timeout=30)
        if response.status_code != 200:
            raise BrowserRuntimeError(
                "DL_NET",
                f"Metadata endpoint tra ve status={response.status_code}: {CFT_ENDPOINT}",
            )
        return response.json()
    except BrowserRuntimeError:
        raise
    except requests.RequestException as exc:
        raise BrowserRuntimeError("DL_NET", f"Khong tai duoc metadata Chrome runtime: {exc}") from exc
    except ValueError as exc:
        raise BrowserRuntimeError("DL_META", f"Metadata Chrome runtime khong hop le: {exc}") from exc


def _select_download(payload: dict[str, Any], channel: str, platform_name: str) -> tuple[str, str]:
    channels = payload.get("channels") or {}
    channel_data = channels.get(channel)
    if not isinstance(channel_data, dict):
        raise BrowserRuntimeError("DL_META", f"Khong tim thay channel {channel}")
    downloads = ((channel_data.get("downloads") or {}).get("chrome") or [])
    for item in downloads:
        if item.get("platform") == platform_name and item.get("url"):
            return str(channel_data.get("version") or ""), str(item["url"])
    raise BrowserRuntimeError(
        "DL_META",
        f"Khong tim thay ban Chrome runtime cho {channel}/{platform_name}",
    )


class FileLock:
    def __init__(self, path: Path, timeout_seconds: int = LOCK_TIMEOUT_SECONDS):
        self.path = path
        self.timeout_seconds = timeout_seconds

    def __enter__(self) -> "FileLock":
        start = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(str(os.getpid()))
                return self
            except FileExistsError:
                if time.time() - start > self.timeout_seconds:
                    try:
                        age = time.time() - self.path.stat().st_mtime
                    except FileNotFoundError:
                        continue
                    if age > self.timeout_seconds:
                        self.path.unlink(missing_ok=True)
                        continue
                    raise BrowserRuntimeError(
                        "DL_LOCK",
                        "Khong lay duoc lock tai runtime. Co the mot instance khac dang bootstrap.",
                    )
                time.sleep(0.5)

    def __exit__(self, exc_type, exc, tb) -> None:
        self.path.unlink(missing_ok=True)


def _estimate_eta(start_time: float, done: int, total: int) -> str:
    if done <= 0 or total <= 0:
        return "--:--"
    elapsed = max(time.time() - start_time, 0.001)
    speed = done / elapsed
    if speed <= 0:
        return "--:--"
    remaining = max(total - done, 0) / speed
    minutes, seconds = divmod(int(remaining), 60)
    return f"{minutes:02d}:{seconds:02d}"


def _human_size(num_bytes: float) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(num_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{num_bytes}B"


def _download_zip(url: str, dest: Path, log_fn: LOG_FN = None, progress_fn: PROGRESS_FN = None) -> None:
    _log(log_fn, f"Bat dau tai Chrome runtime: {url}", "info")
    try:
        with requests.get(url, timeout=60, stream=True) as response:
            if response.status_code != 200:
                raise BrowserRuntimeError(
                    "DL_NET",
                    f"Tep runtime tra ve status={response.status_code}: {url}",
                )
            total = int(response.headers.get("content-length") or 0)
            start = time.time()
            done = 0
            last_log_at = 0.0
            with dest.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    done += len(chunk)
                    _progress(progress_fn, done, total)
                    now = time.time()
                    if now - last_log_at >= 1.0:
                        elapsed = max(now - start, 0.001)
                        speed = done / elapsed
                        pct = int(done * 100 / total) if total else 0
                        eta = _estimate_eta(start, done, total)
                        _log(
                            log_fn,
                            (
                                f"Dang tai Chrome runtime: {pct}% "
                                f"({_human_size(done)}/{_human_size(total) if total else '?'}) "
                                f"{_human_size(speed)}/s ETA {eta}"
                            ),
                            "info",
                        )
                        last_log_at = now
    except BrowserRuntimeError:
        raise
    except requests.RequestException as exc:
        raise BrowserRuntimeError("DL_NET", f"Tai Chrome runtime that bai: {exc}") from exc


def _verify_zip(path: Path) -> None:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            bad_name = archive.testzip()
            if bad_name:
                raise BrowserRuntimeError("DL_EXTRACT", f"Zip runtime hong tai file {bad_name}")
    except zipfile.BadZipFile as exc:
        raise BrowserRuntimeError("DL_EXTRACT", f"Zip runtime khong hop le: {exc}") from exc


def _sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_zip(zip_path: Path, target_dir: Path, log_fn: LOG_FN = None) -> None:
    temp_dir = target_dir.with_name(target_dir.name + ".extracting")
    shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            archive.extractall(temp_dir)
        shutil.rmtree(target_dir, ignore_errors=True)
        temp_dir.replace(target_dir)
    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise BrowserRuntimeError("DL_EXTRACT", f"Giai nen runtime that bai: {exc}") from exc
    _log(log_fn, f"Da giai nen Chrome runtime vao {target_dir}", "success")


def _download_runtime(
    settings: dict[str, Any],
    channel: str,
    platform_name: str,
    log_fn: LOG_FN = None,
    progress_fn: PROGRESS_FN = None,
) -> BrowserResolution:
    runtime_root = get_runtime_root(settings)
    runtime_root.mkdir(parents=True, exist_ok=True)
    payload = _fetch_cft_payload(log_fn)
    version, url = _select_download(payload, channel, platform_name)
    version_dir = runtime_root / "chrome-for-testing" / channel.lower() / version
    resolution = _runtime_resolution(version, version_dir, platform_name, channel)
    if _validate_cached_browser(Path(resolution.path)):
        _log(log_fn, f"Chrome runtime da co san: {resolution.path}", "info")
        _write_metadata(settings, resolution)
        return resolution

    lock_path = runtime_root / "chrome-for-testing" / channel.lower() / ".download.lock"
    with FileLock(lock_path):
        if _validate_cached_browser(Path(resolution.path)):
            _log(log_fn, f"Chrome runtime vua duoc instance khac tai xong: {resolution.path}", "info")
            _write_metadata(settings, resolution)
            return resolution

        version_dir.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix="chrome-runtime-", suffix=".zip", dir=str(runtime_root))
        os.close(fd)
        zip_path = Path(tmp_name)
        try:
            _download_zip(url, zip_path, log_fn, progress_fn)
            sha256_value = _sha256_of_file(zip_path)
            _log(log_fn, f"Da tai xong Chrome runtime, sha256={sha256_value}", "info")
            _verify_zip(zip_path)
            _extract_zip(zip_path, version_dir, log_fn)
        finally:
            zip_path.unlink(missing_ok=True)

    browser_path = Path(resolution.path)
    if not _validate_cached_browser(browser_path):
        shutil.rmtree(version_dir, ignore_errors=True)
        raise BrowserRuntimeError(
            "DL_EXTRACT",
            f"Khong tim thay executable sau khi giai nen: {browser_path}",
        )
    _write_metadata(settings, resolution)
    return resolution


def clear_downloaded_runtime(settings: dict[str, Any], log_fn: LOG_FN = None) -> bool:
    metadata = _load_metadata(settings)
    runtime_root = get_runtime_root(settings)
    removed = False
    runtime_dir = Path(str(metadata.get("runtime_dir") or ""))
    if runtime_dir.exists():
        shutil.rmtree(runtime_dir, ignore_errors=True)
        removed = True
    cft_dir = runtime_root / "chrome-for-testing"
    if cft_dir.exists():
        shutil.rmtree(cft_dir, ignore_errors=True)
        removed = True
    metadata_path = get_browser_metadata_path(settings)
    metadata_path.unlink(missing_ok=True)
    if removed:
        _log(log_fn, "Da xoa runtime cache de bootstrap lai.", "warn")
    return removed


def resolve_browser(
    settings: dict[str, Any],
    log_fn: LOG_FN = None,
    progress_fn: PROGRESS_FN = None,
    force_download: bool = False,
) -> BrowserResolution:
    override = str(settings.get("browser_path_override") or "").strip()
    if override:
        override_path = Path(os.path.expandvars(os.path.expanduser(override)))
        if _validate_cached_browser(override_path):
            resolution = BrowserResolution(
                path=str(override_path),
                source="override",
                channel="override",
            )
            _write_metadata(settings, resolution)
            _log(log_fn, f"Dung browser override: {override_path}", "info")
            return resolution
        raise BrowserRuntimeError("BR_NOT_FOUND", f"Browser override khong ton tai: {override_path}")

    strategy = str(settings.get("browser_strategy") or "system_then_download")
    channel = str(settings.get("browser_channel") or "Stable")

    if not force_download and strategy == "system_then_download":
        system_browser = detect_system_browser(log_fn)
        if system_browser:
            _write_metadata(settings, system_browser)
            return system_browser

    if not force_download:
        cached = _load_cached_runtime(settings, log_fn)
        if cached:
            return cached

    if strategy not in ("system_then_download", "download_only"):
        raise BrowserRuntimeError("BR_STRATEGY", f"Browser strategy khong ho tro: {strategy}")

    platform_name = _platform_name()
    _log(log_fn, f"Khong co browser he thong, se tai Chrome runtime ({channel}/{platform_name}).", "warn")
    try:
        return _download_runtime(settings, channel, platform_name, log_fn, progress_fn)
    except BrowserRuntimeError:
        raise
    except Exception as exc:
        raise BrowserRuntimeError("DL_UNKNOWN", f"Loi khong xac dinh khi tai runtime: {exc}") from exc


def inspect_browser(settings: dict[str, Any]) -> BrowserResolution | None:
    override = str(settings.get("browser_path_override") or "").strip()
    if override:
        override_path = Path(os.path.expandvars(os.path.expanduser(override)))
        if _validate_cached_browser(override_path):
            return BrowserResolution(
                path=str(override_path),
                source="override",
                channel="override",
            )
        return None
    cached = _load_cached_runtime(settings)
    if cached:
        return cached
    return detect_system_browser()
