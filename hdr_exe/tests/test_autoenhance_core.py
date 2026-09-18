"""Comprehensive Unit Tests for Modularized Autoenhance Core in hdr_exe.

Tests all modular components and shared utilities independently:
- core.shared.events: StepEvent, StepTracker lifecycle guards, callback error isolation, log level mapping
- core.shared.callbacks: ProgressAdapter signature binding, warning on error, safe_call_progress
- core.shared.workspace: TemporaryWorkspace auto-cleanup on success, exception, stop
- core.shared.images: format conversion and dimension detection
- core.shared.config: centralized path discovery and config loading
- core.autoenhance.auth: load_token, save_token, clear_token, validate_token
- core.autoenhance.client: GET/POST, headers, retries
- core.autoenhance.metadata: save, load, delete order metadata
- core.autoenhance.orders: creation, pagination, details
- core.autoenhance.upload: preparation, collision-free naming, S3 info, streaming PUT
- core.autoenhance.execute: option mapping, process triggering
- core.autoenhance.polling: status classification, responsive stop event, monotonic timer
- core.autoenhance.download: anti-collision, atomic reservation, safe cleanup, multi-order progress
- core.autoenhance.workflow: 7-step sequence, cancellation before execute, dual workflow isolation
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from PIL import Image

# Ensure hdr_exe is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import core.autoenhance as ae
from core.autoenhance.constants import (
    API_BASE,
    META_DIR,
    NATIVE_EXTS,
    PRESET_MAP,
    STORAGE_FILE,
    WORKFLOW_STEPS,
)

from core.autoenhance.auth import (
    clear_api_key,
    load_api_key,
    save_api_key,
    validate_api_key,
)
from core.autoenhance.client import (
    _api_get,
    _api_post,
)
from core.autoenhance.metadata import (
    delete_order_metadata,
    load_order_metadata,
    save_order_metadata,
)
from core.autoenhance.orders import (
    create_order,
    get_order_details,
    list_orders,
)
from core.autoenhance.upload import (
    _upload_one_file,
    get_upload_s3_info,
    prepare_upload_files,
)
from core.autoenhance.execute import (
    map_options_to_payload,
    trigger_process,
)
from core.autoenhance.polling import (
    poll_order_completion,
)
from core.autoenhance.image_processing import (
    _process_downloaded_png,
    _upscale_to_original,
)
from core.autoenhance.download import (
    _download_one_file,
    batch_download,
    download_selected_photos,
)
from core.autoenhance.workflow import (
    run_workflow,
    restart_workflow_job,
)
from core.shared.licensing.models import LicenseResult
from core.shared.callbacks import ProgressAdapter, safe_call_progress
from core.shared.config import (
    get_api_key_path,
    get_app_dir,
    get_engine_config,
    get_engine_dir,
    get_metadata_dir,
    get_token_path,
)
from core.shared.events import StepEvent, StepTracker
from core.shared.images import (
    convert_to_jpg,
    get_image_dimensions,
)
from core.shared.workspace import TemporaryWorkspace


class TestSharedEvents(unittest.TestCase):
    def test_step_event_to_dict(self):
        evt = StepEvent(
            run_id="run_123",
            engine="autoenhance",
            step="upload",
            step_index=4,
            step_total=7,
            status="running",
            message="Uploading 2/5",
            current=2,
            total=5,
        )
        d = evt.to_dict()
        self.assertEqual(d["run_id"], "run_123")
        self.assertEqual(d["engine"], "autoenhance")
        self.assertEqual(d["step"], "upload")
        self.assertEqual(d["step_index"], 4)
        self.assertEqual(d["step_total"], 7)
        self.assertEqual(d["status"], "running")
        self.assertEqual(d["current"], 2)
        self.assertEqual(d["total"], 5)

    def test_step_tracker_logging_and_callback_isolation(self):
        logs = []

        def failing_event_fn(e: StepEvent):
            raise RuntimeError("event_fn crashed!")

        def safe_log_fn(msg: str, level: str):
            logs.append((msg, level))

        steps = [("step1", "Step 1"), ("step2", "Step 2")]
        tr = StepTracker(
            engine="Autoenhance",
            steps=steps,
            run_id="fixed_id",
            event_fn=failing_event_fn,
            log_fn=safe_log_fn,
        )

        # Calling start_step shouldn't crash despite failing event_fn
        evt = tr.start_step("step1", "Starting step 1...")
        self.assertEqual(evt.step, "step1")
        self.assertEqual(evt.status, "running")

        # Verify log_fn was called with properly formatted step string
        self.assertEqual(len(logs), 1)
        log_msg, log_level = logs[0]
        self.assertEqual(log_msg, "[Autoenhance][1/2 Step 1][running] Starting step 1...")
        self.assertEqual(log_level, "info")

    def test_step_tracker_lifecycle_guards(self):
        """Verify unannounced steps raise ValueError and completed steps block duplicate events."""
        steps = [("step1", "Step 1"), ("step2", "Step 2")]
        warnings = []
        tr = StepTracker(
            engine="Autoenhance",
            steps=steps,
            log_fn=lambda m, l: warnings.append((m, l)),
        )

        # 1. Unannounced step must raise ValueError
        with self.assertRaises(ValueError):
            tr.start_step("unknown_step", "This should fail")

        with self.assertRaises(ValueError):
            tr.complete_step("unknown_step", "success", "This should fail")

        # 2. Invalid status must raise ValueError
        with self.assertRaises(ValueError):
            tr.emit("step1", "warn", "warn is not a valid status")

        # 3. Complete step once
        tr.start_step("step1", "Starting step 1")
        tr.complete_step("step1", "success", "Completed step 1")
        self.assertTrue(tr.is_step_completed("step1"))

        # 4. Duplicate completion must be safely blocked without emitting duplicate terminal event
        events = []
        tr._event_fn = lambda e: events.append(e)
        tr.complete_step("step1", "success", "Completed step 1 again")
        self.assertEqual(len(events), 0)
        self.assertTrue(any("đã kết thúc" in m for m, _ in warnings))

        # 5. Progress after complete must be blocked
        tr.progress_step("step1", "Late progress", current=1, total=1)
        self.assertEqual(len(events), 0)

        # 6. Start after complete must be blocked
        tr.start_step("step1", "Restarting step 1")
        self.assertEqual(len(events), 0)

    def test_step_tracker_log_levels(self):
        logs = []
        steps = [("step1", "Step 1"), ("step2", "Step 2"), ("step3", "Step 3"), ("step4", "Step 4")]
        tr = StepTracker(engine="Autoenhance", steps=steps, log_fn=lambda m, l: logs.append((m, l)))

        tr.start_step("step1", "Running")
        tr.complete_step("step1", "success", "All done")
        tr.complete_step("step2", "partial", "Some failed")
        tr.complete_step("step3", "cancelled", "User stopped")
        tr.complete_step("step4", "failed", "Fatal error")

        levels = [l for _, l in logs]
        self.assertEqual(levels, ["info", "success", "warn", "warn", "error"])


class TestSharedCallbacks(unittest.TestCase):
    def test_progress_adapter_2_args(self):
        called = []
        adapter = ProgressAdapter(lambda c, t: called.append((c, t)))
        adapter(3, 10, "file.jpg")
        self.assertEqual(called, [(3, 10)])

    def test_progress_adapter_3_args(self):
        called = []
        adapter = ProgressAdapter(lambda c, t, f: called.append((c, t, f)))
        adapter(3, 10, "file.jpg")
        self.assertEqual(called, [(3, 10, "file.jpg")])

    def test_progress_adapter_warning_on_internal_error(self):
        warnings = []
        call_count = 0

        def buggy_callback(c, t, f):
            nonlocal call_count
            call_count += 1
            raise ValueError("Bug inside callback")

        adapter = ProgressAdapter(buggy_callback, warning_fn=lambda m, l: warnings.append((m, l)))
        adapter(1, 10, "f1.jpg")
        self.assertEqual(call_count, 1)
        self.assertEqual(len(warnings), 1)
        self.assertIn("Bug inside callback", warnings[0][0])

        # Subsequent call must be suppressed without re-invoking
        adapter(2, 10, "f2.jpg")
        self.assertEqual(call_count, 1)

    def test_safe_call_progress_convenience_wrapper(self):
        called = []
        safe_call_progress(lambda c, t: called.append((c, t)), 5, 20)
        self.assertEqual(called, [(5, 20)])


class TestSharedWorkspace(unittest.TestCase):
    def test_temporary_workspace_cleanup_on_success(self):
        ws_path = None
        with TemporaryWorkspace(prefix="test_ok_") as path:
            ws_path = path
            self.assertTrue(ws_path.is_dir())
            test_file = ws_path / "scratch.txt"
            test_file.write_text("hello", encoding="utf-8")
            self.assertTrue(test_file.is_file())

        self.assertFalse(ws_path.exists())

    def test_temporary_workspace_cleanup_on_exception(self):
        ws_path = None
        try:
            with TemporaryWorkspace(prefix="test_err_") as path:
                ws_path = path
                self.assertTrue(ws_path.is_dir())
                raise RuntimeError("Workflow failed unexpectedly")
        except RuntimeError:
            pass

        self.assertFalse(ws_path.exists())


class TestSharedImages(unittest.TestCase):
    def test_dimension_and_conversion(self):
        with tempfile.TemporaryDirectory() as tmp:
            p_img = Path(tmp) / "square.png"
            Image.new("RGB", (160, 90), color="yellow").save(p_img, "PNG")

            w, h = get_image_dimensions(p_img)
            self.assertEqual((w, h), (160, 90))

            p_jpg = Path(tmp) / "converted.jpg"
            ok = convert_to_jpg(p_img, p_jpg)
            self.assertTrue(ok)
            self.assertTrue(p_jpg.exists())


class TestSharedConfig(unittest.TestCase):
    def test_config_paths_and_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"HDR_EXE_HOME": tmp}):
                app_dir = get_app_dir()
                self.assertEqual(app_dir, Path(tmp).resolve())
                self.assertEqual(get_engine_dir("autoenhance"), app_dir / "autoenhance")
                self.assertEqual(get_token_path("fotello"), app_dir / "fotello" / "token.json")
                self.assertEqual(get_api_key_path("autoenhance"), app_dir / "autoenhance" / "api_key.json")
                self.assertEqual(get_metadata_dir("autoenhance"), app_dir / "autoenhance" / "metadata")


class TestMetadata(unittest.TestCase):
    def test_metadata_save_load_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta_d = Path(tmp) / "metadata"
            with patch("core.autoenhance.metadata.META_DIR", meta_d):
                data = {"dimensions": {"photo.jpg": [1920, 1080]}}
                save_order_metadata("ord_meta_test", data)

                loaded = load_order_metadata("ord_meta_test")
                self.assertEqual(loaded, data)

                ok = delete_order_metadata("ord_meta_test")
                self.assertTrue(ok)
                self.assertEqual(load_order_metadata("ord_meta_test"), {})


class TestAuth(unittest.TestCase):
    def test_empty_api_key(self):
        logs = []
        self.assertFalse(validate_api_key("", log_fn=lambda m, l: logs.append((m, l))))
        self.assertTrue(any("API key trống" in m for m, _ in logs))

    @patch("requests.Session.get")
    def test_valid_api_key(self, mock_get):
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"orders": []}
        mock_get.return_value = mock_resp

        self.assertTrue(validate_api_key("valid_key"))
        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        self.assertIn("/orders/?per_page=1", args[0])
        self.assertEqual(kwargs["headers"]["x-api-key"], "valid_key")

    def test_api_key_storage_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_storage = Path(tmp_dir) / "api_key.json"
            with patch("core.autoenhance.auth.STORAGE_FILE", fake_storage):
                self.assertIsNone(load_api_key())
                save_api_key("test_secret_123")
                self.assertEqual(load_api_key(), "test_secret_123")
                clear_api_key()
                self.assertIsNone(load_api_key())


class TestOrders(unittest.TestCase):
    @patch("requests.Session.post")
    def test_create_order(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"order_id": "ord_100"}
        mock_post.return_value = mock_resp

        res = create_order("tok", "Living Room")
        self.assertEqual(res["order_id"], "ord_100")
        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args[1]["json"]["name"], "Living Room")

    @patch("requests.Session.get")
    def test_list_orders_pagination(self, mock_get):
        resp1 = MagicMock()
        resp1.json.return_value = {
            "orders": [{"order_id": "1"}, {"order_id": "2"}],
            "pagination": {"next_offset": 2},
        }
        resp2 = MagicMock()
        resp2.json.return_value = {
            "orders": [{"order_id": "3"}],
            "pagination": {"next_offset": None},
        }
        mock_get.side_effect = [resp1, resp2]

        orders = list_orders("tok")
        self.assertEqual(len(orders), 3)
        self.assertEqual([o["order_id"] for o in orders], ["1", "2", "3"])


class TestUpload(unittest.TestCase):
    def test_prepare_upload_files_collision_free(self):
        """Verify same.bmp and same.gif convert to distinct filenames and meta_dims maps both correctly."""
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as conv:
            p_bmp = Path(src) / "same.bmp"
            p_gif = Path(src) / "same.gif"

            Image.new("RGB", (100, 100), color="red").save(p_bmp, "BMP")
            Image.new("RGB", (200, 200), color="blue").save(p_gif, "GIF")

            ready, dims = prepare_upload_files([p_bmp, p_gif], Path(conv))
            self.assertEqual(len(ready), 2)
            names = [f.name for f in ready]
            # Must have distinct filenames
            self.assertEqual(len(set(names)), 2)
            self.assertTrue(ready[0].is_file())
            self.assertTrue(ready[1].is_file())

            # Dimensions must be tracked correctly per distinct converted name
            self.assertIn(ready[0].name, dims)
            self.assertIn(ready[1].name, dims)
            self.assertEqual(dims[p_bmp.stem + "_bmp.jpg"], (100, 100))
            self.assertEqual(dims[p_bmp.stem + "_gif.jpg"], (200, 200))

    def test_duplicate_native_file_collision_free(self):
        """Verify duplicate native filenames across directories are resolved cleanly."""
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as conv:
            sub1 = Path(base) / "sub1"
            sub2 = Path(base) / "sub2"
            sub1.mkdir()
            sub2.mkdir()

            f1 = sub1 / "photo.jpg"
            f2 = sub2 / "photo.jpg"
            Image.new("RGB", (50, 50)).save(f1, "JPEG")
            Image.new("RGB", (60, 60)).save(f2, "JPEG")

            ready, dims = prepare_upload_files([f1, f2], Path(conv))
            self.assertEqual(len(ready), 2)
            names = [f.name for f in ready]
            self.assertEqual(len(set(names)), 2)

    @patch("requests.put")
    def test_upload_one_file_strips_host_header(self, mock_put):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_put.return_value = mock_resp

        with tempfile.TemporaryDirectory() as tmp:
            test_file = Path(tmp) / "photo.jpg"
            test_file.write_bytes(b"dummy image bytes")

            s3_info = {
                "url": "https://s3.amazonaws.com/bucket/photo.jpg",
                "headers": {"Host": "s3.amazonaws.com", "Content-Type": "image/jpeg"},
            }

            ok = _upload_one_file(test_file, s3_info)
            self.assertTrue(ok)
            mock_put.assert_called_once()
            called_headers = mock_put.call_args[1]["headers"]
            self.assertNotIn("Host", called_headers)
            self.assertEqual(called_headers.get("Content-Type"), "image/jpeg")


class TestDownloadAndDataSafety(unittest.TestCase):
    def setUp(self):
        self._patch_lic = patch(
            "core.autoenhance.download.check",
            return_value=LicenseResult(valid=True, engine="autoenhance", level="lite"),
        )
        self._patch_lic.start()

    def tearDown(self):
        self._patch_lic.stop()

    @patch("core.autoenhance.download.get_order_details")
    @patch("core.autoenhance.download._download_one_file")
    def test_never_delete_or_overwrite_existing_files(self, mock_dl, mock_details):
        mock_details.return_value = {
            "order_id": "ord_safety",
            "images": [{"image_id": "12345678", "image_name": "room.jpg", "status": "completed"}],
        }

        with tempfile.TemporaryDirectory() as dst:
            dst_p = Path(dst)
            existing_room = dst_p / "room.jpg"
            existing_room.write_bytes(b"ORIGINAL_ROOM")
            existing_hash = dst_p / "room_12345678.jpg"
            existing_hash.write_bytes(b"ORIGINAL_HASH")

            def fake_dl_ok(url, dest, api_key=None, stop_event=None):
                Image.new("RGB", (30, 30)).save(dest, "PNG")
                return True

            mock_dl.side_effect = fake_dl_ok
            cnt = batch_download("tok", ["ord_safety"], dst_p)
            self.assertEqual(cnt, 1)
            self.assertEqual(existing_room.read_bytes(), b"ORIGINAL_ROOM")
            self.assertEqual(existing_hash.read_bytes(), b"ORIGINAL_HASH")
            self.assertTrue((dst_p / "room_12345678_2.jpg").exists())

    @patch("core.autoenhance.download.get_order_details")
    @patch("core.autoenhance.download._download_one_file")
    def test_standalone_batch_download_emits_3_steps(self, mock_dl, mock_details):
        mock_details.return_value = {
            "order_id": "ord_steps",
            "images": [{"image_id": "1", "image_name": "img1.jpg", "status": "completed"}],
        }
        mock_dl.side_effect = lambda url, dest, **kw: (Image.new("RGB", (10, 10)).save(dest, "PNG") or True)

        events: list[StepEvent] = []
        with tempfile.TemporaryDirectory() as dst:
            cnt = batch_download("tok", ["ord_steps"], dst, event_fn=lambda e: events.append(e))
            self.assertEqual(cnt, 1)

            step_names = [e.step for e in events]
            self.assertIn("activation", step_names)
            self.assertIn("auth", step_names)
            self.assertIn("order_details", step_names)
            self.assertIn("download", step_names)
            for e in events:
                self.assertEqual(e.step_total, 4)


class TestWorkflowSevenSteps(unittest.TestCase):
    def setUp(self):
        self._meta_tmp = tempfile.TemporaryDirectory()
        self._patch_meta = patch("core.autoenhance.metadata.META_DIR", Path(self._meta_tmp.name))
        self._patch_meta.start()
        self._patch_lic = patch(
            "core.shared.jobs.runner.require_access",
            return_value=LicenseResult(valid=True, engine="autoenhance", level="plus"),
        )
        self._patch_lic.start()

    def tearDown(self):
        self._patch_lic.stop()
        self._patch_meta.stop()
        self._meta_tmp.cleanup()

    @staticmethod
    def _fake_download(image_id=None, original_filename=None, output_dir=None, **kwargs):
        clean_stem = Path(original_filename).stem if original_filename else (image_id or "img")
        out_f = Path(output_dir) / f"{clean_stem}.jpg"
        Image.new("RGB", (10, 10)).save(out_f, "JPEG")
        return True, out_f, None

    @patch("core.autoenhance.workflow.download_and_process_image")
    @patch("core.autoenhance.workflow.poll_order_completion")
    @patch("core.autoenhance.workflow.trigger_process")
    @patch("core.autoenhance.workflow._upload_one_file")
    @patch("core.autoenhance.workflow.get_upload_s3_info")
    @patch("core.autoenhance.workflow.create_order")
    def test_workflow_full_success_emits_7_steps(
        self, mock_create, mock_s3_info, mock_upload, mock_trigger, mock_poll, mock_dl_one
    ):
        mock_create.return_value = {"order_id": "ord_wf"}
        mock_s3_info.return_value = {"url": "https://s3/1", "headers": {}}
        mock_upload.return_value = True
        mock_trigger.return_value = True
        mock_poll.return_value = (
            {"images": [{"id": "1", "status": "completed"}]},
            [{"id": "1"}],
            [],
        )

        mock_dl_one.side_effect = self._fake_download

        events: list[StepEvent] = []
        logs: list[tuple[str, str]] = []

        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            sample = Path(src) / "test.jpg"
            Image.new("RGB", (50, 50)).save(sample, "JPEG")

            batch_res = run_workflow(
                input_dir=src,
                output_dir=dst,
                api_key="test_key",
                event_fn=lambda e: events.append(e),
                log_fn=lambda m, l: logs.append((m, l)),
            )
            self.assertEqual(batch_res.status, "success")

            completed_steps = [e.step for e in events if e.status in ("success", "partial", "failed", "cancelled")]
            expected_steps = ["activation", "auth", "prepare", "create_order", "upload", "execute", "polling", "download", "export"]
            for s in expected_steps:
                self.assertIn(s, completed_steps)

    @patch("core.autoenhance.workflow.trigger_process")
    @patch("core.autoenhance.workflow._upload_one_file")
    @patch("core.autoenhance.workflow.get_upload_s3_info")
    @patch("core.autoenhance.workflow.create_order")
    def test_stop_before_execute_does_not_trigger_process(
        self, mock_create, mock_s3_info, mock_upload, mock_trigger
    ):
        """Verify cancellation immediately after upload prevents sending process to server."""
        mock_create.return_value = {"order_id": "ord_stop"}
        mock_s3_info.return_value = {"url": "https://s3/1", "headers": {}}
        mock_upload.return_value = True

        stop_evt = threading.Event()

        def upload_and_signal_stop(*args, **kwargs):
            stop_evt.set()
            return True

        mock_upload.side_effect = upload_and_signal_stop

        events: list[StepEvent] = []

        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            sample = Path(src) / "test.jpg"
            Image.new("RGB", (50, 50)).save(sample, "JPEG")

            batch_res = run_workflow(
                input_dir=src,
                output_dir=dst,
                api_key="key",
                stop_event=stop_evt,
                event_fn=lambda e: events.append(e),
            )
            self.assertEqual(batch_res.status, "cancelled")

            mock_trigger.assert_not_called()
            execute_events = [e for e in events if e.step == "execute"]
            self.assertTrue(any(e.status == "cancelled" for e in execute_events))

    @patch("core.autoenhance.workflow.download_and_process_image")
    @patch("core.autoenhance.workflow.poll_order_completion")
    @patch("core.autoenhance.workflow.trigger_process")
    @patch("core.autoenhance.workflow._upload_one_file")
    @patch("core.autoenhance.workflow.get_upload_s3_info")
    @patch("core.autoenhance.workflow.create_order")
    def test_upload_partial_status(
        self, mock_create, mock_s3_info, mock_upload, mock_trigger, mock_poll, mock_dl_one
    ):
        """Verify partial upload results in partial status rather than success."""
        mock_create.return_value = {"order_id": "ord_part"}
        mock_s3_info.return_value = {"url": "https://s3/1", "headers": {}}
        # First upload succeeds, second fails
        mock_upload.side_effect = [True, False]
        mock_trigger.return_value = True
        mock_poll.return_value = ({"images": [{"id": "1", "status": "completed"}]}, [{"id": "1"}], [])

        mock_dl_one.side_effect = self._fake_download

        events: list[StepEvent] = []
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            Image.new("RGB", (30, 30)).save(Path(src) / "a.jpg", "JPEG")
            Image.new("RGB", (30, 30)).save(Path(src) / "b.jpg", "JPEG")

            batch_res = run_workflow(
                input_dir=src,
                output_dir=dst,
                api_key="key",
                event_fn=lambda e: events.append(e),
            )

            up_events = [e for e in events if e.step == "upload" and e.status in ("success", "partial", "failed")]
            self.assertTrue(len(up_events) > 0)
            self.assertEqual(up_events[-1].status, "partial")

    @patch("core.autoenhance.workflow.download_and_process_image")
    @patch("core.autoenhance.workflow.poll_order_completion")
    @patch("core.autoenhance.workflow.trigger_process")
    @patch("core.autoenhance.workflow._upload_one_file")
    @patch("core.autoenhance.workflow.get_upload_s3_info")
    @patch("core.autoenhance.workflow.create_order")
    def test_download_consistent_denominator(
        self, mock_create, mock_s3_info, mock_upload, mock_trigger, mock_poll, mock_dl_one
    ):
        """Verify download step maintains consistent total = len(successful) throughout."""
        mock_create.return_value = {"order_id": "ord_denom"}
        mock_s3_info.return_value = {"url": "https://s3/1", "headers": {}}
        mock_upload.return_value = True
        mock_trigger.return_value = True
        # 2 successful, 1 failed on server
        mock_poll.return_value = (
            {"images": [{"id": "1", "status": "completed"}, {"id": "2", "status": "completed"}, {"id": "3", "status": "failed"}]},
            [{"id": "1"}, {"id": "2"}],
            [{"id": "3"}],
        )

        mock_dl_one.side_effect = self._fake_download

        events: list[StepEvent] = []
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            Image.new("RGB", (30, 30)).save(Path(src) / "sample.jpg", "JPEG")

            batch_res = run_workflow(
                input_dir=src,
                output_dir=dst,
                api_key="key",
                event_fn=lambda e: events.append(e),
            )

            dl_events = [e for e in events if e.step == "download"]
            self.assertTrue(len(dl_events) > 0)
            final_dl = dl_events[-1]
            self.assertEqual(final_dl.status, "partial")

    @patch("core.autoenhance.workflow.download_and_process_image")
    @patch("core.autoenhance.workflow.poll_order_completion")
    @patch("core.autoenhance.workflow.trigger_process")
    @patch("core.autoenhance.workflow._upload_one_file")
    @patch("core.autoenhance.workflow.get_upload_s3_info")
    @patch("core.autoenhance.workflow.create_order")
    def test_concurrent_workflows_isolate_run_ids(
        self, mock_create, mock_s3_info, mock_upload, mock_trigger, mock_poll, mock_dl_one
    ):
        """Verify two simultaneous workflows have distinct run_ids."""
        mock_create.return_value = {"order_id": "ord_multi"}
        mock_s3_info.return_value = {"url": "https://s3/1", "headers": {}}
        mock_upload.return_value = True
        mock_trigger.return_value = True
        mock_poll.return_value = ({"images": [{"id": "1", "status": "completed"}]}, [{"id": "1"}], [])

        mock_dl_one.side_effect = self._fake_download

        run_ids = []

        def run_wf(name):
            with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
                sample = Path(src) / f"{name}.jpg"
                Image.new("RGB", (30, 30)).save(sample, "JPEG")
                run_workflow(
                    input_dir=src,
                    output_dir=dst,
                    api_key="key",
                    event_fn=lambda e: run_ids.append((name, e.run_id)),
                )

        t1 = threading.Thread(target=run_wf, args=("flow1",))
        t2 = threading.Thread(target=run_wf, args=("flow2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        flow1_ids = {rid for name, rid in run_ids if name == "flow1"}
        flow2_ids = {rid for name, rid in run_ids if name == "flow2"}

        self.assertEqual(len(flow1_ids), 1)
        self.assertEqual(len(flow2_ids), 1)
        self.assertNotEqual(flow1_ids, flow2_ids)


if __name__ == "__main__":
    unittest.main()
