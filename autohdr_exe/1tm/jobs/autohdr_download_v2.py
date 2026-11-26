"""
Job Executor v2 chuyên trách tải ảnh AutoHDR thông qua Photoshoot Resolver.
Độc lập 100% với AutoHDR v1 (không dùng Cookie, không kế thừa AutoHDRJob, không fallback).
Thông báo lỗi chỉ nêu rõ step xảy ra lỗi, tuyệt đối không hiển thị URL, IP hoặc domain.
"""
from __future__ import annotations

import gc
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

import requests
import urllib3

from core.stats_client import stats_client
from libs.constants.autohdr_v2 import (
    V2_ALLOWED_IMAGE_EXTENSIONS,
    V2_CHUNK_SIZE,
    V2_CRED_HWID,
    V2_CRED_RESOLVER_KEY,
    V2_CRED_SERVER_URL,
    V2_DEFAULT_DOWNLOAD_WORKERS,
    V2_DEFAULT_FOLDER_NAME,
    V2_DEFAULT_IMAGE_EXT,
    V2_DEFAULT_IMAGE_NAME_PREFIX,
    V2_DEFAULT_URL_SCHEME,
    V2_DOWNLOAD_TIMEOUT,
    V2_ENDPOINT_RESOLVE,
    V2_EXTRA_DOWNLOAD_MAP,
    V2_EXTRA_FOLDER,
    V2_EXTRA_PHOTOSHOOT_ID,
    V2_EXTRA_TITLE,
    V2_EXTRA_UUID,
    V2_ITEM_FILENAME,
    V2_ITEM_URLS,
    V2_MAX_DOWNLOAD_WORKERS,
    V2_PART_SUFFIX,
    V2_PREF_PHOTOSHOOT_URL,
    V2_PROGRESS_COMPLETE,
    V2_PROGRESS_DOWNLOAD_START,
    V2_PROGRESS_PREPARE,
    V2_PROGRESS_RESOLVE,
    V2_REQ_HWID,
    V2_REQ_KEY,
    V2_REQ_URL,
    V2_RESOLVE_TIMEOUT,
    V2_RESP_ERROR,
    V2_RESP_FOLDER,
    V2_RESP_ITEMS,
    V2_RESP_PHOTOSHOOT_ID,
    V2_RESP_SUCCESS,
    V2_RESP_TITLE,
    V2_RESP_UUID,
    V2_STEP_DOWNLOAD,
    V2_STEP_PREPARE,
    V2_STEP_RESOLVE,
    V2_USER_AGENT,
    V2_VERIFY_SSL,
)
from libs.executor.base import BaseExecutor, ExecutionContext
from libs.utils import make_progress_reporter, run_parallel

# Tắt cảnh báo InsecureRequestWarning khi server dùng SSL self-signed
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_URL_OR_HOST_PATTERN = re.compile(
    r"(?:https?://\S+|"
    r"www\.\S+|"
    r"host=['\"][^'\"]+['\"]|"
    r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b|"
    r"\b[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+(?::\d+)?(?:/\S*)?)",
    re.IGNORECASE,
)
_INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitize_error_detail(detail: str) -> str:
    """Loại bỏ hoàn toàn URL, IP và domain khỏi thông điệp lỗi (giữ nguyên tên file ảnh nếu có)."""
    if not detail:
        return ""

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        lower = token.lower()
        if not lower.startswith(("http://", "https://", "www.")) and "/" not in token and ":" not in token:
            dot_idx = lower.rfind(".")
            if dot_idx != -1 and lower[dot_idx:] in V2_ALLOWED_IMAGE_EXTENSIONS:
                return token
        return ""

    cleaned = _URL_OR_HOST_PATTERN.sub(_replace, str(detail))
    return re.sub(r"\s+", " ", cleaned).strip(" .:-")


def _sanitize_folder_name(name: str, default: str = V2_DEFAULT_FOLDER_NAME) -> str:
    """Chuẩn hóa tên thư mục đầu ra an toàn trên Windows/POSIX."""
    cleaned = _INVALID_PATH_CHARS.sub("_", (name or "").strip()).strip(". ")
    if not cleaned or cleaned in (".", ".."):
        return default
    return cleaned


def _normalize_server_url(server_url: str) -> str:
    """Chuẩn hóa địa chỉ resolver trước khi gửi request."""
    clean = (server_url or "").strip().rstrip("/")
    if clean and not (clean.startswith("http://") or clean.startswith("https://")):
        clean = f"{V2_DEFAULT_URL_SCHEME}{clean}"
    return clean


class AutoHDRDownloadV2Job(BaseExecutor):
    """
    Executor phiên bản 2 tải ảnh AutoHDR qua Photoshoot Resolver.
    - Độc lập hoàn toàn, không lưu server_url hay resolver_key trong client.
    - Không fallback: mọi tham số đọc từ 1 nguồn duy nhất, chỉ tải primary URL (urls[0]).
    - Lỗi chỉ báo rõ theo step, không lộ URL hay domain.
    """

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        max_download_workers: int = V2_DEFAULT_DOWNLOAD_WORKERS,
        verify_ssl: bool = V2_VERIFY_SSL,
    ):
        self.session = session or requests.Session()
        self.max_download_workers = max(1, min(int(max_download_workers), V2_MAX_DOWNLOAD_WORKERS))
        self.verify_ssl = verify_ssl

    def pre_transform(self, context: ExecutionContext) -> bool:
        """Khởi tạo và xác nhận thư mục lưu trữ đầu ra mà không yêu cầu file nguồn."""
        context.set_step(V2_STEP_PREPARE, "Đang khởi tạo thư mục lưu trữ...", progress_pct=V2_PROGRESS_PREPARE)
        out_path = Path(context.output_dir)
        try:
            out_path.mkdir(parents=True, exist_ok=True)
            context.log("INFO", f"Thư mục lưu trữ: {out_path}")
            return True
        except Exception:
            context.error_message = f"Lỗi ở bước {V2_STEP_PREPARE}: Không thể tạo thư mục lưu trữ đầu ra."
            return False

    def _validate_pre_transform(self, context: ExecutionContext) -> bool:
        """Hậu kiểm riêng cho DownloadJob v2: đảm bảo thư mục đầu ra tồn tại trên ổ cứng."""
        out_p = Path(context.output_dir).resolve()
        if not out_p.is_dir():
            context.error_message = f"Lỗi ở bước {V2_STEP_PREPARE}: Thư mục đầu ra không tồn tại."
            return False
        return True

    def _step_resolve(
        self,
        context: ExecutionContext,
        ps_input: str,
        server_url: str,
        resolver_key: str,
        hwid: str,
    ) -> Optional[Dict[str, Any]]:
        """Bước resolve: Gửi yêu cầu giải mã photoshoot tới máy chủ resolver."""
        if context.is_cancelled():
            return None

        context.set_step(V2_STEP_RESOLVE, "Đang giải mã dữ liệu photoshoot...", progress_pct=V2_PROGRESS_RESOLVE)
        resolve_api = f"{server_url}{V2_ENDPOINT_RESOLVE}"
        payload = {
            V2_REQ_KEY: resolver_key,
            V2_REQ_HWID: hwid,
            V2_REQ_URL: ps_input,
        }
        headers = {
            "Content-Type": "application/json",
            "User-Agent": V2_USER_AGENT,
        }

        try:
            res = self.session.post(
                resolve_api,
                json=payload,
                headers=headers,
                timeout=V2_RESOLVE_TIMEOUT,
                verify=self.verify_ssl,
            )
        except requests.Timeout:
            context.error_message = (
                f"Lỗi ở bước {V2_STEP_RESOLVE}: Máy chủ không phản hồi trong {V2_RESOLVE_TIMEOUT}s."
            )
            return None
        except requests.RequestException:
            context.error_message = f"Lỗi ở bước {V2_STEP_RESOLVE}: Không thể kết nối tới máy chủ xử lý."
            return None
        except Exception:
            context.error_message = f"Lỗi ở bước {V2_STEP_RESOLVE}: Lỗi không xác định khi gửi yêu cầu."
            return None

        if res.status_code != 200:
            detail = ""
            try:
                err_json = res.json()
                if isinstance(err_json, dict) and err_json.get(V2_RESP_ERROR):
                    sanitized = _sanitize_error_detail(str(err_json[V2_RESP_ERROR]))
                    if sanitized:
                        detail = f" - {sanitized}"
            except Exception:
                pass
            context.error_message = (
                f"Lỗi ở bước {V2_STEP_RESOLVE}: Máy chủ từ chối yêu cầu (HTTP {res.status_code}){detail}"
            )
            return None

        try:
            result = res.json()
        except Exception:
            context.error_message = f"Lỗi ở bước {V2_STEP_RESOLVE}: Dữ liệu phản hồi không đúng định dạng JSON."
            return None

        if not isinstance(result, dict):
            context.error_message = f"Lỗi ở bước {V2_STEP_RESOLVE}: Cấu trúc dữ liệu phản hồi không hợp lệ."
            return None

        if not result.get(V2_RESP_SUCCESS):
            raw_err = str(result.get(V2_RESP_ERROR) or "")
            sanitized = _sanitize_error_detail(raw_err)
            detail = f": {sanitized}" if sanitized else "."
            context.error_message = f"Lỗi ở bước {V2_STEP_RESOLVE}: Giải mã thất bại{detail}"
            return None

        return result

    def _build_download_tasks(
        self,
        context: ExecutionContext,
        items: List[Dict[str, Any]],
        target_dir: Path,
    ) -> List[Tuple[int, Dict[str, Any], Path]]:
        """Tạo danh sách tác vụ tải xuống với tên file duy nhất chống ghi đè."""
        download_map: Dict[str, str] = context.extra_data.setdefault(V2_EXTRA_DOWNLOAD_MAP, {})
        used_names: set[str] = set()
        tasks: List[Tuple[int, Dict[str, Any], Path]] = []

        for idx, item in enumerate(items):
            default_name = f"{V2_DEFAULT_IMAGE_NAME_PREFIX}_{idx}{V2_DEFAULT_IMAGE_EXT}"
            raw_name = Path(str(item.get(V2_ITEM_FILENAME) or default_name)).name
            item_key = f"{idx}:{raw_name}"

            if item_key in download_map:
                assigned_name = download_map[item_key]
            else:
                stem = Path(raw_name).stem.strip() or f"{V2_DEFAULT_IMAGE_NAME_PREFIX}_{idx}"
                ext = Path(raw_name).suffix.lower() or V2_DEFAULT_IMAGE_EXT
                candidate = f"{stem}{ext}"
                counter = 1
                while candidate in used_names:
                    candidate = f"{stem}_{counter}{ext}"
                    counter += 1
                assigned_name = candidate
                download_map[item_key] = assigned_name

            used_names.add(assigned_name)
            tasks.append((idx, item, target_dir / assigned_name))

        return tasks

    def _step_download(
        self,
        context: ExecutionContext,
        items: List[Dict[str, Any]],
        target_dir: Path,
    ) -> bool:
        """Bước download: Tải song song ảnh thành phẩm từ primary URL duy nhất."""
        if context.is_cancelled():
            return False

        target_dir.mkdir(parents=True, exist_ok=True)
        total = len(items)
        context.set_step(
            V2_STEP_DOWNLOAD,
            f"Bắt đầu tải {total} ảnh thành phẩm...",
            progress_pct=V2_PROGRESS_DOWNLOAD_START,
        )

        download_tasks = self._build_download_tasks(context, items, target_dir)

        def _download_one(task: Tuple[int, Dict[str, Any], Path]) -> str:
            _, item, dest = task
            filename = dest.name

            if context.is_cancelled():
                raise IOError("Tác vụ đã bị hủy.")

            if dest.is_file() and dest.stat().st_size > 0:
                context.log("INFO", f"[RESUME] Bỏ qua {filename} (đã tồn tại trên máy).")
                return str(dest)

            urls = item.get(V2_ITEM_URLS)
            if not isinstance(urls, list) or not urls or not urls[0]:
                raise ValueError(f"Thiếu đường dẫn tải cho file {filename}")

            primary_url = str(urls[0])
            tmp_dest = dest.with_suffix(dest.suffix + V2_PART_SUFFIX)

            try:
                resp = requests.get(
                    primary_url,
                    stream=True,
                    headers={"User-Agent": V2_USER_AGENT},
                    timeout=V2_DOWNLOAD_TIMEOUT,
                    verify=self.verify_ssl,
                )
            except requests.Timeout as exc:
                raise IOError(f"Quá thời gian tải file {filename}") from exc
            except requests.RequestException as exc:
                raise IOError(f"Lỗi kết nối khi tải file {filename}") from exc
            except Exception as exc:
                raise IOError(f"Lỗi không xác định khi tải file {filename}") from exc

            try:
                if resp.status_code != 200:
                    raise IOError(f"Không thể tải file {filename} (HTTP {resp.status_code})")

                with open(tmp_dest, "wb") as f_out:
                    for chunk in resp.iter_content(chunk_size=V2_CHUNK_SIZE):
                        if context.is_cancelled():
                            raise IOError("Tác vụ tải ảnh bị dừng bởi người dùng.")
                        if chunk:
                            f_out.write(chunk)

                tmp_dest.replace(dest)
                return str(dest)
            except OSError as exc:
                tmp_dest.unlink(missing_ok=True)
                if context.is_cancelled():
                    raise IOError("Tác vụ tải ảnh bị dừng bởi người dùng.") from exc
                raise IOError(f"Lỗi lưu tệp {filename}") from exc
            except Exception:
                tmp_dest.unlink(missing_ok=True)
                raise
            finally:
                try:
                    resp.close()
                except Exception:
                    pass

        batch_res = run_parallel(
            items=download_tasks,
            worker_fn=_download_one,
            max_workers=self.max_download_workers,
            stop_event=context.stop_event,
            on_progress=make_progress_reporter(
                context,
                V2_STEP_DOWNLOAD,
                "tải",
                V2_PROGRESS_DOWNLOAD_START,
                V2_PROGRESS_COMPLETE,
                lambda t: t[2].name,
            ),
        )

        context.downloaded_files = [res for _, res in batch_res.succeeded if res]

        if not batch_res.all_succeeded:
            if batch_res.is_cancelled or context.is_cancelled():
                context.error_message = f"Tác vụ ở bước {V2_STEP_DOWNLOAD} bị dừng bởi người dùng."
            elif batch_res.failed:
                first_err = _sanitize_error_detail(str(batch_res.failed[0][1]))
                detail = first_err or "Không thể tải dữ liệu ảnh."
                fail_count = len(batch_res.failed)
                suffix = f" ({fail_count}/{total} file lỗi)" if fail_count > 1 else ""
                context.error_message = f"Lỗi ở bước {V2_STEP_DOWNLOAD}: {detail}{suffix}"
            else:
                context.error_message = f"Lỗi ở bước {V2_STEP_DOWNLOAD}: Không tải được đầy đủ ảnh thành phẩm."
            return False

        context.log("INFO", f"Hoàn tất tải {len(context.downloaded_files)}/{total} ảnh thành phẩm.")
        return True

    def transform(self, context: ExecutionContext) -> bool:
        """Thực thi quy trình tải ảnh v2: resolve photoshoot -> tải ảnh song song."""
        ps_input = str(context.preferences.get(V2_PREF_PHOTOSHOOT_URL) or "").strip()
        if not ps_input:
            context.error_message = f"Lỗi ở bước {V2_STEP_RESOLVE}: Thiếu thông tin photoshoot đầu vào."
            return False

        server_url = _normalize_server_url(str(context.credentials.get(V2_CRED_SERVER_URL) or ""))
        if not server_url:
            context.error_message = f"Lỗi ở bước {V2_STEP_RESOLVE}: Thiếu cấu hình máy chủ xử lý."
            return False

        resolver_key = str(context.credentials.get(V2_CRED_RESOLVER_KEY) or "").strip()
        if not resolver_key:
            context.error_message = f"Lỗi ở bước {V2_STEP_RESOLVE}: Thiếu thông tin xác thực máy chủ."
            return False

        hwid = str(context.credentials.get(V2_CRED_HWID) or "").strip()
        if not hwid:
            context.error_message = f"Lỗi ở bước {V2_STEP_RESOLVE}: Thiếu mã định danh thiết bị."
            return False

        resolved = self._step_resolve(context, ps_input, server_url, resolver_key, hwid)
        if not resolved:
            return False

        ps_uuid = str(resolved.get(V2_RESP_UUID) or "")
        title = str(resolved.get(V2_RESP_TITLE) or V2_DEFAULT_FOLDER_NAME)
        folder_name = _sanitize_folder_name(str(resolved.get(V2_RESP_FOLDER) or title))
        items = resolved.get(V2_RESP_ITEMS) or []
        photoshoot_id = resolved.get(V2_RESP_PHOTOSHOOT_ID)

        context.remote_job_id = str(photoshoot_id or ps_uuid or "")
        context.extra_data[V2_EXTRA_UUID] = ps_uuid
        context.extra_data[V2_EXTRA_TITLE] = title
        context.extra_data[V2_EXTRA_FOLDER] = folder_name
        if photoshoot_id is not None:
            context.extra_data[V2_EXTRA_PHOTOSHOOT_ID] = photoshoot_id

        if not isinstance(items, list) or len(items) == 0:
            context.error_message = f"Lỗi ở bước {V2_STEP_RESOLVE}: Không có ảnh nào trong photoshoot này."
            return False

        tag = f"[{ps_uuid[:8]}] " if ps_uuid else ""
        context.log("INFO", f"{tag}Đã giải mã '{title}' ({len(items)} ảnh).")

        target_dir = Path(context.output_dir) / folder_name
        download_ok = self._step_download(context, items, target_dir)

        if download_ok:
            try:
                stats_client.append_runtime(ps_uuid or context.remote_job_id or ps_input)
            except Exception:
                pass

        gc.collect()
        return download_ok

    def post_transform(self, context: ExecutionContext) -> bool:
        """Dọn dẹp thông tin xác thực tạm trong RAM sau khi job hoàn tất."""
        for key in (V2_CRED_RESOLVER_KEY, V2_CRED_HWID, V2_CRED_SERVER_URL):
            context.credentials.pop(key, None)
        return super().post_transform(context)
