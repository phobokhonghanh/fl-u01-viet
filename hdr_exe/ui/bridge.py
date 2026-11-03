"""Python-JavaScript Bridge API for hdr_exe pywebview Desktop UI.

Exposes native Python operations to JavaScript via window.pywebview.api:
- Native OS folder dialogs and file explorer opener
- Engine licensing (online verification, Lite vs Plus enforcement)
- Chrome CDP login & session management
- Firestore cursor listings retrieval
- Background sequential workflow execution & step-aware restarts
- Real-time logging & StepEvent dispatching to the frontend
"""
from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from core.shared.config import get_app_dir
from core.shared.events import StepEvent
from core.shared.jobs.models import BatchResult, JobPlan, JobResult
from core.shared.jobs.store import JobStore
from core.shared.licensing import activate, check, clear, require_access
from core.shared.licensing.machine import get_machine_id
from core.shared.licensing.models import LicensingAccessError
from core.shared.licensing.storage import get_key

logger = logging.getLogger("hdr_exe.ui.bridge")


class BridgeApi:
    """Bridge exposed to window.pywebview.api."""

    def __init__(self, window: Any | None = None) -> None:
        self._window = window
        self._lock = threading.Lock()
        self._running_tasks: dict[str, dict[str, Any]] = {}
        self._job_stores: dict[str, JobStore] = {
            "autoenhance": JobStore(),
            "fotello": JobStore(),
        }

    def set_window(self, window: Any) -> None:
        """Assign window reference after pywebview window creation."""
        self._window = window

    # -------------------------------------------------------------------------
    # Internal helpers for UI communication
    # -------------------------------------------------------------------------

    def _emit(self, js_call: str) -> None:
        """Safely evaluate JS in the pywebview window."""
        if self._window is not None:
            try:
                self._window.evaluate_js(js_call)
            except Exception as e:
                logger.debug("Failed to evaluate JS '%s': %s", js_call, e)

    def _notify_log(self, engine: str, message: str, level: str = "info") -> None:
        clean_msg = json.dumps(message)
        clean_lvl = json.dumps(level)
        self._emit(f"window.onEngineLog && window.onEngineLog('{engine}', {clean_msg}, {clean_lvl});")

    def _notify_step_event(self, engine: str, event: StepEvent) -> None:
        event_json = json.dumps(event.to_dict())
        self._emit(f"window.onJobStepEvent && window.onJobStepEvent('{engine}', {event_json});")

    def _notify_task_finished(self, engine: str, status: str, summary: dict[str, Any]) -> None:
        summary_json = json.dumps(summary)
        self._emit(f"window.onTaskFinished && window.onTaskFinished('{engine}', '{status}', {summary_json});")

    # -------------------------------------------------------------------------
    # 1. Native Folder & File Dialogs
    # -------------------------------------------------------------------------

    def select_folder(self, initial_dir: str | None = None) -> str | None:
        """Open native OS folder selection dialog."""
        if not self._window:
            return None
        import webview
        start_dir = initial_dir if (initial_dir and os.path.isdir(initial_dir)) else str(Path.home())
        result = self._window.create_file_dialog(
            webview.FOLDER_DIALOG,
            directory=start_dir,
        )
        if result and len(result) > 0:
            return result[0]
        return None

    def open_folder(self, path: str) -> dict[str, Any]:
        """Open folder in native OS file explorer."""
        p = Path(path).resolve()
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
        try:
            os_name = platform.system().lower()
            if "windows" in os_name:
                os.startfile(str(p))
            elif "darwin" in os_name:
                subprocess.Popen(["open", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p)])
            return {"success": True, "path": str(p)}
        except Exception as e:
            logger.error("Failed to open folder %s: %s", path, e)
            return {"success": False, "error": str(e), "path": str(p)}

    def get_default_folders(self) -> dict[str, str]:
        """Return standardized default directories."""
        home = Path.home()
        pics = home / "Pictures"
        if not pics.exists():
            pics = home
        ae_dir = pics / "Autoenhance_Out"
        fo_dir = pics / "Fotello_Downloads"
        return {
            "autoenhance": str(ae_dir),
            "fotello": str(fo_dir),
        }

    def inspect_input_folder(self, engine: str, folder_path: str, bracket_size: int = 3) -> dict[str, Any]:
        """Inspect input directory to count valid image files and estimated outputs."""
        p = Path(folder_path).resolve()
        if not p.is_dir():
            return {"valid": False, "error": "Thư mục không tồn tại.", "inputs": 0, "outputs": 0}
        eng = engine.strip().lower()
        try:
            if eng == "autoenhance":
                from core.autoenhance.workflow import build_autoenhance_outputs
                outputs = build_autoenhance_outputs(p)
                total_inputs = sum(len(o.input_files) for o in outputs)
                return {"valid": True, "inputs": total_inputs, "outputs": len(outputs), "bracket_size": 1}
            elif eng == "fotello":
                from core.fotello.brackets import build_bracket_outputs
                outputs = build_bracket_outputs([p], bracket_size=bracket_size)
                total_inputs = sum(len(o.input_files) for o in outputs)
                return {"valid": True, "inputs": total_inputs, "outputs": len(outputs), "bracket_size": bracket_size}
        except Exception as e:
            return {"valid": False, "error": str(e), "inputs": 0, "outputs": 0}
        return {"valid": False, "error": "Engine không hợp lệ", "inputs": 0, "outputs": 0}

    # -------------------------------------------------------------------------
    # 2. Licensing Management
    # -------------------------------------------------------------------------

    def get_license_status(self, engine: str) -> dict[str, Any]:
        """Check online license entitlement with locally stored key."""
        eng = engine.strip().lower()
        res = check(eng)
        stored_key = get_key(eng) or ""
        try:
            m_id = get_machine_id()
        except Exception:
            m_id = "unknown"

        return {
            "engine": eng,
            "valid": bool(res.valid),
            "level": res.level or "",
            "code": res.code,
            "message": res.message,
            "stored_key": stored_key,
            "machine_id": m_id,
        }

    def activate_license(self, engine: str, key: str) -> dict[str, Any]:
        """Activate license key online and store on success."""
        eng = engine.strip().lower()
        res = activate(eng, key)
        return {
            "engine": eng,
            "valid": bool(res.valid),
            "level": res.level or "",
            "code": res.code,
            "message": res.message,
        }

    def clear_license(self, engine: str) -> dict[str, Any]:
        """Remove stored license key."""
        eng = engine.strip().lower()
        clear(eng)
        return {"engine": eng, "success": True}

    # -------------------------------------------------------------------------
    # 3. Service Auth & Chrome CDP Login
    # -------------------------------------------------------------------------

    def get_auth_status(self, engine: str) -> dict[str, Any]:
        """Check if service credentials exist and are valid."""
        eng = engine.strip().lower()
        if eng == "autoenhance":
            from core.autoenhance.auth import load_api_key, validate_api_key
            key = load_api_key()
            if not key:
                return {"engine": eng, "connected": False, "message": "Chưa có API Key"}
            valid = validate_api_key(key)
            masked = f"{key[:4]}...{key[-4:]}" if len(key) >= 8 else "***"
            return {
                "engine": eng,
                "connected": valid,
                "message": f"Đã kết nối ({masked})" if valid else "API Key không hợp lệ hoặc hết hạn",
            }
        elif eng == "fotello":
            from core.fotello.auth import check_auth_session
            status, msg, details = check_auth_session()
            connected = status == "valid"
            team_id = details.get("team_id", "") if isinstance(details, dict) else ""
            return {
                "engine": eng,
                "connected": connected,
                "status": status,
                "team_id": team_id,
                "message": msg if connected else (msg or "Chưa đăng nhập qua Chrome"),
            }
        return {"engine": eng, "connected": False, "message": "Engine không hợp lệ"}

    def login_service_cdp(self, engine: str) -> dict[str, Any]:
        """Launch Chrome with remote debugging to extract service session."""
        eng = engine.strip().lower()
        if eng == "autoenhance":
            from core.autoenhance.cdp import extract_and_save_api_key
            self._notify_log(eng, "Đang mở Chrome để đăng nhập và trích xuất API Key Autoenhance...", "info")
            ok, msg = extract_and_save_api_key(log_fn=lambda m, l="info": self._notify_log(eng, m, l))
            return {"engine": eng, "success": ok, "message": msg}
        elif eng == "fotello":
            from core.fotello.cdp import login as fotello_login
            self._notify_log(eng, "Đang mở Chrome để đăng nhập và trích xuất phiên Fotello...", "info")
            res = fotello_login(log_fn=lambda m, l="info": self._notify_log(eng, m, l))
            ok = res.get("status") == "success"
            return {"engine": eng, "success": ok, "message": res.get("message", "")}
        return {"engine": eng, "success": False, "message": "Engine không hợp lệ"}

    def save_manual_token(self, engine: str, token_str: str) -> dict[str, Any]:
        """Manually save API key or JSON token payload."""
        eng = engine.strip().lower()
        tok = token_str.strip()
        if eng == "autoenhance":
            from core.autoenhance.auth import save_api_key, validate_api_key
            if not tok:
                return {"success": False, "message": "API key không được rỗng."}
            save_api_key(tok)
            valid = validate_api_key(tok)
            return {"success": valid, "message": "Đã lưu API key" if valid else "API key không hợp lệ"}
        elif eng == "fotello":
            from core.fotello.auth import save_tokens, validate_session
            try:
                data = json.loads(tok)
                if isinstance(data, dict):
                    save_tokens(data)
                    st, msg, _ = validate_session()
                    return {"success": st == "valid", "message": msg}
            except Exception as e:
                return {"success": False, "message": f"JSON token không hợp lệ: {e}"}
        return {"success": False, "message": "Engine không hợp lệ"}

    # -------------------------------------------------------------------------
    # 4. Fotello Listings (Firestore Cursor Pagination)
    # -------------------------------------------------------------------------

    def fetch_fotello_listings(self, limit: int | None = None) -> dict[str, Any]:
        """Fetch all listings from Firestore with cursor pagination."""
        try:
            from core.fotello.listings import list_listings
            self._notify_log("fotello", "Đang tải danh sách listing từ Firestore...", "info")
            listings_data = list_listings(
                limit=limit,
                paginate=True,
                log_fn=lambda m, l="info": self._notify_log("fotello", m, l),
            )
            items = []
            for item in listings_data:
                name = item.get("name") or "Chưa có thông tin"
                created = item.get("created_at") or ""
                outputs_count = item.get("enhances_count", 0)
                st = item.get("status", "completed")
                items.append({
                    "id": item.get("id", ""),
                    "name": name,
                    "date": created,
                    "outputsCount": outputs_count,
                    "status": st,
                    "statusText": "Hoàn tất" if st == "completed" else "Một phần",
                })
            return {
                "success": True,
                "listings": items,
                "total": len(items),
                "complete": getattr(listings_data, "complete", True),
            }
        except Exception as e:
            logger.error("Failed to fetch listings: %s", e)
            return {
                "success": False,
                "message": str(e),
                "listings": [],
                "total": 0,
            }

    # -------------------------------------------------------------------------
    # 5. Task Execution (Autoenhance & Fotello Workflow Runner)
    # -------------------------------------------------------------------------

    def is_task_running(self, engine: str) -> bool:
        """Check if an engine currently has an active running task."""
        eng = engine.strip().lower()
        with self._lock:
            info = self._running_tasks.get(eng)
            if info and info.get("thread") and info["thread"].is_alive():
                return True
            return False

    def start_engine_task(self, engine: str, options: dict[str, Any]) -> dict[str, Any]:
        """Start a new processing task (Single or Batch) for an engine."""
        eng = engine.strip().lower()
        if self.is_task_running(eng):
            return {"success": False, "message": f"Tác vụ {eng.upper()} đang chạy. Vui lòng chờ hoặc dừng tác vụ trước."}

        exec_mode = str(options.get("exec_mode", "single")).lower()

        # Check input directory exists and contains valid images
        input_dir = options.get("input_dir", "")
        if not input_dir or not Path(input_dir).is_dir():
            return {"success": False, "message": "Thư mục ảnh đầu vào không tồn tại hoặc chưa được chọn."}

        bracket_size = int(options.get("bracket_size", 3))
        inspect_res = self.inspect_input_folder(eng, input_dir, bracket_size=bracket_size)
        if not inspect_res.get("valid") or inspect_res.get("outputs", 0) == 0:
            err_msg = inspect_res.get("error") or f"Thư mục đầu vào không chứa ảnh hợp lệ cho {eng.upper()}."
            return {"success": False, "message": err_msg}

        # Enforce licensing entitlements before starting
        try:
            require_access(eng, exec_mode)
        except LicensingAccessError as le:
            return {"success": False, "message": str(le), "code": le.code}

        stop_event = threading.Event()
        run_id = f"run_{eng}_{int(time.time())}"

        def _worker():
            try:
                self._notify_log(eng, f"Bắt đầu phiên thực thi {run_id} (chế độ {exec_mode.upper()})...", "info")
                store = self._job_stores[eng]

                if eng == "autoenhance":
                    from core.autoenhance.workflow import run_workflow
                    batch_res = run_workflow(
                        input_dir=options.get("input_dir", ""),
                        output_dir=options.get("output_dir", ""),
                        mode=exec_mode,
                        options=options,
                        stop_event=stop_event,
                        event_fn=lambda ev: self._notify_step_event("autoenhance", ev),
                        log_fn=lambda m, l="info": self._notify_log("autoenhance", m, l),
                        store=store,
                        run_id=run_id,
                    )
                    self._notify_task_finished("autoenhance", batch_res.status, {
                        "run_id": run_id,
                        "status": batch_res.status,
                        "jobs_total": len(batch_res.job_results),
                        "succeeded": sum(1 for r in batch_res.job_results if r.status == "success"),
                    })

                elif eng == "fotello":
                    from core.fotello.workflow import run_workflow
                    batch_res = run_workflow(
                        input_dir=options.get("input_dir", ""),
                        output_dir=options.get("output_dir", ""),
                        mode=exec_mode,
                        bracket_size=int(options.get("bracket_size", 3)),
                        preferences=options,
                        stop_event=stop_event,
                        event_fn=lambda ev: self._notify_step_event("fotello", ev),
                        log_fn=lambda m, l="info": self._notify_log("fotello", m, l),
                        store=store,
                        run_id=run_id,
                    )
                    self._notify_task_finished("fotello", batch_res.status, {
                        "run_id": run_id,
                        "status": batch_res.status,
                        "jobs_total": len(batch_res.job_results),
                        "succeeded": sum(1 for r in batch_res.job_results if r.status == "success"),
                    })

            except Exception as e:
                logger.exception("Execution error in %s: %s", eng, e)
                self._notify_log(eng, f"Lỗi thực thi: {e}", "error")
                self._notify_task_finished(eng, "failed", {"run_id": run_id, "error": str(e)})
            finally:
                with self._lock:
                    self._running_tasks.pop(eng, None)

        worker_thread = threading.Thread(target=_worker, daemon=True)
        with self._lock:
            self._running_tasks[eng] = {
                "thread": worker_thread,
                "stop_event": stop_event,
                "run_id": run_id,
            }
        worker_thread.start()

        return {"success": True, "run_id": run_id, "engine": eng}

    # -------------------------------------------------------------------------
    # 6. Download Tasks (Unified as 1 Job)
    # -------------------------------------------------------------------------

    def start_download_task(self, engine: str, options: dict[str, Any]) -> dict[str, Any]:
        """Execute download of existing items as ONE tracked Job."""
        eng = engine.strip().lower()
        if self.is_task_running(eng):
            return {"success": False, "message": f"Tác vụ {eng.upper()} đang chạy."}

        stop_event = threading.Event()
        run_id = f"dl_run_{eng}_{int(time.time())}"

        def _worker():
            try:
                if eng == "fotello":
                    from core.fotello.download import download_multiple_listings
                    listing_ids = options.get("listing_ids", [])
                    out_dir = options.get("output_dir", "")
                    rendition = options.get("rendition", "highres")
                    upsize = options.get("upsize", "original")
                    prioritize_upsized = (upsize == "upsized_2x")

                    self._notify_log("fotello", f"Khởi tạo Job tải về cho {len(listing_ids)} listing...", "info")
                    res = download_multiple_listings(
                        listing_ids=listing_ids,
                        output_dir=out_dir,
                        prioritize_upsized=prioritize_upsized,
                        stop_event=stop_event,
                        log_fn=lambda m, l="info": self._notify_log("fotello", m, l),
                        event_fn=lambda ev: self._notify_step_event("fotello", ev),
                        run_id=run_id,
                    )
                    status = "success" if res.get("failed", 0) == 0 else ("partial" if res.get("succeeded", 0) > 0 else "failed")
                    self._notify_task_finished("fotello", status, res)

                elif eng == "autoenhance":
                    from core.autoenhance.auth import load_api_key
                    from core.autoenhance.download import batch_download
                    order_id = options.get("order_id", "").strip()
                    out_dir = Path(options.get("output_dir", "")).resolve() / order_id
                    out_dir.mkdir(parents=True, exist_ok=True)
                    api_key = load_api_key()

                    self._notify_log("autoenhance", f"Khởi tạo Job tải về cho đơn hàng {order_id}...", "info")
                    count = batch_download(
                        api_key=api_key,
                        order_ids=[order_id],
                        savedir=out_dir,
                        stop_event=stop_event,
                        log_fn=lambda m, l="info": self._notify_log("autoenhance", m, l),
                        event_fn=lambda ev: self._notify_step_event("autoenhance", ev),
                    )
                    status = "success" if count > 0 else "failed"
                    self._notify_task_finished("autoenhance", status, {"order_id": order_id, "downloaded": count})

            except Exception as e:
                logger.exception("Download error: %s", e)
                self._notify_log(eng, f"Lỗi tải về: {e}", "error")
                self._notify_task_finished(eng, "failed", {"error": str(e)})
            finally:
                with self._lock:
                    self._running_tasks.pop(eng, None)

        worker_thread = threading.Thread(target=_worker, daemon=True)
        with self._lock:
            self._running_tasks[eng] = {
                "thread": worker_thread,
                "stop_event": stop_event,
                "run_id": run_id,
            }
        worker_thread.start()
        return {"success": True, "run_id": run_id, "engine": eng}

    # -------------------------------------------------------------------------
    # 7. Task Control: Stop & Restart
    # -------------------------------------------------------------------------

    def stop_engine_task(self, engine: str) -> dict[str, Any]:
        """Signal stop event to interrupt running task immediately."""
        eng = engine.strip().lower()
        with self._lock:
            info = self._running_tasks.get(eng)
            if not info or not info.get("stop_event"):
                return {"success": False, "message": "Không có tác vụ nào đang chạy để dừng."}
            info["stop_event"].set()

        self._notify_log(eng, "Người dùng đã bấm Dừng tác vụ. Đang ngắt các tiến trình an toàn...", "warn")
        return {"success": True, "engine": eng}

    def restart_job(self, engine: str, run_id: str, job_id: str) -> dict[str, Any]:
        """Manually restart a specific failed/cancelled job."""
        eng = engine.strip().lower()
        if self.is_task_running(eng):
            return {"success": False, "message": "Có tác vụ đang chạy, không thể restart đồng thời."}

        stop_event = threading.Event()

        def _worker():
            try:
                store = self._job_stores[eng]
                if eng == "autoenhance":
                    from core.autoenhance.workflow import restart_workflow_job
                    res = restart_workflow_job(
                        run_id=run_id,
                        job_id=job_id,
                        stop_event=stop_event,
                        event_fn=lambda ev: self._notify_step_event("autoenhance", ev),
                        log_fn=lambda m, l="info": self._notify_log("autoenhance", m, l),
                        store=store,
                    )
                elif eng == "fotello":
                    from core.fotello.workflow import restart_workflow_job
                    res = restart_workflow_job(
                        run_id=run_id,
                        job_id=job_id,
                        stop_event=stop_event,
                        event_fn=lambda ev: self._notify_step_event("fotello", ev),
                        log_fn=lambda m, l="info": self._notify_log("fotello", m, l),
                        store=store,
                    )
                self._notify_task_finished(eng, res.status, {"job_id": job_id, "status": res.status})
            except Exception as e:
                self._notify_log(eng, f"Lỗi khi restart job {job_id}: {e}", "error")
            finally:
                with self._lock:
                    self._running_tasks.pop(eng, None)

        worker_thread = threading.Thread(target=_worker, daemon=True)
        with self._lock:
            self._running_tasks[eng] = {
                "thread": worker_thread,
                "stop_event": stop_event,
                "run_id": run_id,
            }
        worker_thread.start()
        return {"success": True, "engine": eng, "job_id": job_id}

    # -------------------------------------------------------------------------
    # 8. Manifest & History Retrieval
    # -------------------------------------------------------------------------

    def get_run_jobs(self, engine: str, run_id: str) -> list[dict[str, Any]]:
        """Fetch all planned jobs for a run, including formatted outputs list."""
        eng = engine.strip().lower()
        store = self._job_stores.get(eng)
        if not store:
            return []
        doc = store.load_run(run_id)
        if not doc or "jobs" not in doc:
            return []
        results = []
        for jid, jdata in doc["jobs"].items():
            raw_outputs = jdata.get("outputs", [])
            attempts = jdata.get("attempts", [])
            last_att = attempts[-1] if attempts else {}
            out_dir_path = Path(jdata.get("output_dir", ""))

            # Check for manifest file to retrieve downloaded items
            items_map = {}
            for att_no in (range(len(attempts), 0, -1) if attempts else [jdata.get("current_attempt", 1)]):
                for candidate in [
                    out_dir_path / f"manifest_{run_id}_{jid}_att{att_no}.json",
                    out_dir_path / f"{jid}_manifest.json",
                    out_dir_path / f"manifest_{jid}.json",
                ]:
                    if candidate.is_file():
                        try:
                            with open(candidate, "r", encoding="utf-8") as mf:
                                m_data = json.load(mf)
                                for item in m_data.get("items", []):
                                    if isinstance(item, dict):
                                        if "output_id" in item:
                                            items_map[item["output_id"]] = item
                                        if "filename" in item:
                                            items_map[item["filename"]] = item
                                            items_map[Path(item["filename"]).stem] = item
                            break
                        except Exception:
                            pass
                if items_map:
                    break

            succ_raw = last_att.get("outputs_succeeded", [])
            succ_ids = set(succ_raw) | {Path(s).stem for s in succ_raw}
            fail_map = {}
            for f in last_att.get("outputs_failed", []):
                if isinstance(f, dict):
                    fid = str(f.get("output_id", ""))
                    fail_map[fid] = f.get("error", "Lỗi")
                    fail_map[Path(fid).stem] = f.get("error", "Lỗi")

            job_status = jdata.get("status", "queued")
            formatted_outputs = []
            seen_outputs = set()
            for o in raw_outputs:
                oid = str(o.get("output_id", ""))
                in_files = o.get("input_files", [])
                in_names = [Path(p).name for p in in_files]

                is_succ = (
                    oid in succ_ids
                    or oid in items_map
                    or f"{oid}.jpg" in succ_ids
                    or (out_dir_path / f"{oid}.jpg").is_file()
                )

                if is_succ:
                    st = "success"
                elif oid in fail_map:
                    st = "failed"
                elif job_status in ("success", "completed"):
                    st = "success"
                elif job_status in ("failed", "cancelled"):
                    st = "failed"
                elif job_status == "running":
                    st = "running"
                elif job_status == "partial":
                    st = "success" if is_succ else "failed"
                else:
                    st = "queued"

                out_name = f"{oid}.jpg"
                if oid in items_map and items_map[oid].get("file_path"):
                    out_name = Path(items_map[oid]["file_path"]).name
                elif oid in items_map and items_map[oid].get("filename"):
                    out_name = items_map[oid]["filename"]
                elif (out_dir_path / f"{oid}.jpg").is_file():
                    out_name = f"{oid}.jpg"

                seen_outputs.add(oid)
                seen_outputs.add(out_name)
                seen_outputs.add(Path(out_name).stem)

                formatted_outputs.append({
                    "id": oid,
                    "output_id": oid,
                    "inputName": ", ".join(in_names) if in_names else oid,
                    "outputName": out_name,
                    "status": st,
                    "error": fail_map.get(oid),
                })

            for item in m_data.get("items", []) if "m_data" in locals() and isinstance(m_data, dict) else []:
                if isinstance(item, dict) and item.get("filename"):
                    fn = item["filename"]
                    stem = Path(fn).stem
                    if fn not in seen_outputs and stem not in seen_outputs:
                        seen_outputs.add(fn)
                        seen_outputs.add(stem)
                        formatted_outputs.append({
                            "id": stem,
                            "output_id": stem,
                            "inputName": "Auto-merge HDR",
                            "outputName": fn,
                            "status": "success",
                            "error": None,
                        })

            succ_count = len(last_att.get("outputs_succeeded", []))
            if job_status in ("success", "completed") and succ_count == 0:
                succ_count = len(raw_outputs)

            results.append({
                "job_id": jid,
                "run_id": run_id,
                "engine": eng,
                "job_index": jdata.get("job_index", 1),
                "job_total": jdata.get("job_total", 1),
                "status": job_status,
                "current_attempt": jdata.get("current_attempt", len(attempts) if attempts else 1),
                "latest_step": jdata.get("latest_step", "init"),
                "outputs_total": max(len(raw_outputs), len(formatted_outputs)),
                "outputs_succeeded": succ_count,
                "outputs_failed": len(last_att.get("outputs_failed", [])),
                "outputs": formatted_outputs,
                "error_message": jdata.get("error_message") or last_att.get("error_message"),
                "cancel_reason": jdata.get("cancel_reason") or last_att.get("cancel_reason"),
                "output_dir": str(jdata.get("output_dir", "")),
            })
        results.sort(key=lambda x: x["job_index"])
        return results

    def get_job_manifest(self, engine: str, run_id: str, job_id: str, attempt_no: int | None = None) -> dict[str, Any] | None:
        """Fetch manifest JSON for a completed job or specific attempt."""
        eng = engine.strip().lower()
        store = self._job_stores.get(eng)
        if not store:
            return None
        record = store.load_job_record(run_id, job_id)
        if not record:
            return None

        # Check if manifest file exists on disk
        target_att = attempt_no or record.current_attempt
        if record.output_dir:
            p_dir = Path(record.output_dir)
            possible_files = [
                p_dir / f"manifest_{run_id}_{job_id}_att{target_att}.json",
                p_dir / f"{job_id}_manifest.json",
                p_dir / f"manifest_{job_id}.json",
            ]
            for pf in possible_files:
                if pf.is_file():
                    try:
                        with open(pf, "r", encoding="utf-8") as f:
                            return json.load(f)
                    except Exception:
                        pass

        # Fallback to synthesizing manifest representation from record
        latest_att = record.attempts[-1] if record.attempts else None
        return {
            "run_id": run_id,
            "job_id": job_id,
            "attempt_no": target_att,
            "engine": eng,
            "status": record.status,
            "step": record.latest_step,
            "outputs_total": len(record.outputs),
            "outputs_succeeded": len(latest_att.outputs_succeeded) if latest_att else 0,
            "outputs_failed": len(latest_att.outputs_failed) if latest_att else 0,
            "error_message": record.error_message,
            "cancel_reason": record.cancel_reason,
            "server_resources": record.server_resources,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def get_job_history(self, engine: str, run_id: str, job_id: str) -> list[dict[str, Any]]:
        """Fetch attempt history for a job."""
        eng = engine.strip().lower()
        store = self._job_stores.get(eng)
        if not store:
            return []
        record = store.load_job_record(run_id, job_id)
        if not record:
            return []
        return [
            {
                "attempt_no": a.attempt_no,
                "step": a.step,
                "status": a.status,
                "started_at": a.started_at,
                "finished_at": a.finished_at,
                "cancel_reason": a.cancel_reason,
                "error_message": a.error_message,
                "outputs_succeeded": len(a.outputs_succeeded),
                "outputs_failed": len(a.outputs_failed),
            }
            for a in record.attempts
        ]

    # -------------------------------------------------------------------------
    # 9. App Configuration Persistence
    # -------------------------------------------------------------------------

    def get_app_config(self) -> dict[str, Any]:
        """Read UI configuration file from ~/.hdr_exe/ui_config.json."""
        config_file = get_app_dir() / "ui_config.json"
        defaults = self.get_default_folders()
        result = {
            "autoenhance_output_dir": defaults["autoenhance"],
            "fotello_output_dir": defaults["fotello"],
            "last_active_tab": "autoenhance",
        }
        if config_file.is_file():
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        result.update(data)
            except Exception as e:
                logger.warning("Error reading UI config: %s", e)
        return result

    def save_app_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Save UI configuration to ~/.hdr_exe/ui_config.json."""
        config_file = get_app_dir() / "ui_config.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            current = self.get_app_config()
            current.update(config)
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(current, f, indent=2, ensure_ascii=False)
            return {"success": True}
        except Exception as e:
            logger.error("Error saving UI config: %s", e)
            return {"success": False, "error": str(e)}
