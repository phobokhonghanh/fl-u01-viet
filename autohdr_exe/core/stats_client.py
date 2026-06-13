"""Best-effort background delivery for snapshot and runtime statistics."""

import glob
import os
import threading
import time
import uuid
from typing import Optional

import requests

from core.cache import cache
from core.utils import get_hwid, get_logs_dir


PRODUCT = "autohdr"
MAX_RETRIES = 3
REQUEST_TIMEOUT = (5, 15)
RETRY_DELAY_SECONDS = 1

RUNTIME_FILE = os.path.join(get_logs_dir(), "runtime.log")
PENDING_PATTERN = os.path.join(get_logs_dir(), "runtime.pending.*.log")


class StatsClient:
    """Collect and silently deliver stats without blocking application flows."""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (
            base_url
            or os.getenv("AUTOHDR_API_BASE", "https://u01-viet-backend.up.railway.app")
        ).rstrip("/")
        self._runtime_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._worker_lock = threading.Lock()
        self._worker_running = False

    def mark_session(self, user_id: str) -> None:
        """Create a pending snapshot when the authenticated user changes."""
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            return

        try:
            with self._state_lock:
                if str(cache.get("stats_user_id", "")).strip() == normalized_user_id:
                    return
                cache.set("stats_user_id", normalized_user_id)
                cache.set("stats_snapshot_pending", True)
                cache.set("stats_snapshot_event_id", uuid.uuid4().hex)
        except Exception:
            pass

    def append_runtime(self, runtime_id: str) -> None:
        """Append one completed job identifier to the active runtime file."""
        normalized_runtime_id = str(runtime_id or "").strip()
        if not normalized_runtime_id or "\n" in normalized_runtime_id or "\r" in normalized_runtime_id:
            return

        try:
            with self._runtime_lock:
                os.makedirs(os.path.dirname(RUNTIME_FILE), exist_ok=True)
                with open(RUNTIME_FILE, "a", encoding="utf-8") as runtime_file:
                    runtime_file.write(normalized_runtime_id + "\n")
                    runtime_file.flush()
        except Exception:
            pass

    def dispatch_async(self) -> None:
        """Start one silent stats worker if another worker is not already active."""
        with self._worker_lock:
            if self._worker_running:
                return
            self._worker_running = True

        try:
            thread = threading.Thread(target=self._run_worker, daemon=True)
            thread.start()
        except Exception:
            with self._worker_lock:
                self._worker_running = False

    def _run_worker(self) -> None:
        try:
            self._rotate_runtime_file()
            self._deliver_pending()
        except Exception:
            pass
        finally:
            with self._worker_lock:
                self._worker_running = False

    def _rotate_runtime_file(self) -> None:
        """Move the active runtime file to an immutable pending batch."""
        try:
            with self._runtime_lock:
                if not os.path.isfile(RUNTIME_FILE) or os.path.getsize(RUNTIME_FILE) == 0:
                    return
                batch_id = uuid.uuid4().hex
                pending_path = os.path.join(
                    get_logs_dir(), f"runtime.pending.{batch_id}.log"
                )
                os.replace(RUNTIME_FILE, pending_path)
        except Exception:
            pass

    def _deliver_pending(self) -> None:
        pending_paths = sorted(glob.glob(PENDING_PATTERN))
        snapshot = self._get_snapshot()

        if not pending_paths:
            if snapshot:
                self._send_snapshot_only(snapshot)
            return

        for pending_path in pending_paths:
            batch_id = self._batch_id_from_path(pending_path)
            if not batch_id:
                continue

            attached_snapshot = snapshot
            if not self._send_request(
                snapshot=attached_snapshot,
                pending_path=pending_path,
                batch_id=batch_id,
            ):
                return

            try:
                os.remove(pending_path)
            except OSError:
                return

            if attached_snapshot:
                self._clear_snapshot(attached_snapshot["event_id"])
                snapshot = None

    def _send_snapshot_only(self, snapshot: dict) -> None:
        if self._send_request(snapshot=snapshot):
            self._clear_snapshot(snapshot["event_id"])

    def _get_snapshot(self) -> Optional[dict]:
        try:
            with self._state_lock:
                if not bool(cache.get("stats_snapshot_pending", False)):
                    return None
                event_id = str(cache.get("stats_snapshot_event_id", "")).strip()
                user_id = str(cache.get("stats_user_id", "")).strip()
                if not event_id or not user_id:
                    return None
                return {"event_id": event_id, "user_id": user_id}
        except Exception:
            return None

    def _clear_snapshot(self, event_id: str) -> None:
        try:
            with self._state_lock:
                current_event_id = str(
                    cache.get("stats_snapshot_event_id", "")
                ).strip()
                if current_event_id != event_id:
                    return
                cache.set("stats_snapshot_pending", False)
                cache.delete("stats_snapshot_event_id")
        except Exception:
            pass

    def _send_request(
        self,
        snapshot: Optional[dict],
        pending_path: Optional[str] = None,
        batch_id: Optional[str] = None,
    ) -> bool:
        params = {
            "product": PRODUCT,
            "user": snapshot["user_id"] if snapshot else "",
            "machine_id": get_hwid(),
            "snapshot": str(bool(snapshot)).lower(),
            "runtime": str(bool(pending_path)).lower(),
        }
        if snapshot:
            params["snapshot_event_id"] = snapshot["event_id"]
        if batch_id:
            params["batch_id"] = batch_id

        for attempt in range(MAX_RETRIES):
            try:
                if pending_path:
                    with open(pending_path, "rb") as runtime_file:
                        response = requests.post(
                            f"{self.base_url}/api/stats",
                            params=params,
                            files={"file": (os.path.basename(pending_path), runtime_file, "text/plain")},
                            timeout=REQUEST_TIMEOUT,
                        )
                else:
                    response = requests.post(
                        f"{self.base_url}/api/stats",
                        params=params,
                        timeout=REQUEST_TIMEOUT,
                    )

                if 200 <= response.status_code < 300:
                    return True
            except Exception:
                pass

            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_SECONDS * (attempt + 1))

        return False

    @staticmethod
    def _batch_id_from_path(path: str) -> str:
        filename = os.path.basename(path)
        prefix = "runtime.pending."
        suffix = ".log"
        if not filename.startswith(prefix) or not filename.endswith(suffix):
            return ""
        return filename[len(prefix):-len(suffix)]


stats_client = StatsClient()
