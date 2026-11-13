"""
Unit test kiểm thử tích hợp các Provider Job (AutoHDRJob, AutoenhanceJob)
Địa điểm: autohdr_exe/1tm/tests/test_jobs.py
"""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

# Đưa 1tm và autohdr_exe vào sys.path để chạy test độc lập
TM_DIR = Path(__file__).resolve().parent.parent
LIBS_DIR = TM_DIR / "libs"
AUTOHDR_DIR = TM_DIR.parent
sys.path.insert(0, str(TM_DIR))
sys.path.insert(0, str(LIBS_DIR))
sys.path.insert(0, str(AUTOHDR_DIR))

from libs.executor import ExecutionContext, ExecutorRegistry
from jobs import AutoHDRJob, AutoHDRDownloadJob, AutoenhanceJob, AutoenhanceDownloadJob


def test_registry_auto_registration():
    print("[TEST] 1. Kiểm thử tự động đăng ký Provider vào ExecutorRegistry...")
    assert ExecutorRegistry.get("autohdr") is AutoHDRJob
    assert ExecutorRegistry.get("AUTOHDR") is AutoHDRJob
    assert ExecutorRegistry.get("autohdr_download") is AutoHDRDownloadJob
    assert ExecutorRegistry.get("autoenhance") is AutoenhanceJob
    assert ExecutorRegistry.get("AUTOENHANCE") is AutoenhanceJob
    assert ExecutorRegistry.get("autoenhance_download") is AutoenhanceDownloadJob
    assert "autohdr" in ExecutorRegistry.list_available()
    assert "autohdr_download" in ExecutorRegistry.list_available()
    assert "autoenhance" in ExecutorRegistry.list_available()
    assert "autoenhance_download" in ExecutorRegistry.list_available()
    print("  -> PASS: Toàn bộ 4 engine (Upload & Download cho cả AutoHDR và Autoenhance) đã tự động đăng ký vào Registry.")


def test_autohdr_job_missing_auth():
    print("[TEST] 2. Kiểm thử AutoHDRJob khi thiếu thông tin xác thực...")
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_p = Path(temp_dir)
        img = temp_p / "photo.jpg"
        img.write_bytes(b"image_bytes")

        context = ExecutionContext(
            job_id="hdr_test_01",
            file_paths=[str(img)],
            output_dir=str(temp_p / "out"),
            credentials={},  # Thiếu cookie
        )

        with patch("jobs.autohdr.get_active_session", return_value=None):
            job = AutoHDRJob()
            res = job.execute(context)
            assert res.status == "failed"
            assert "Chưa có Cookie xác thực AutoHDR" in res.error_message
            print("  -> PASS: AutoHDR chặn bắt buộc phải có thông tin xác thực.")


def test_autoenhance_job_missing_auth():
    print("[TEST] 3. Kiểm thử AutoenhanceJob khi thiếu API key...")
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_p = Path(temp_dir)
        img = temp_p / "photo.jpg"
        img.write_bytes(b"image_bytes")

        context = ExecutionContext(
            job_id="ae_test_01",
            file_paths=[str(img)],
            output_dir=str(temp_p / "out"),
            credentials={},  # Thiếu api_key
        )

        with patch("jobs.autoenhance.get_active_session", return_value=None):
            job = AutoenhanceJob()
            res = job.execute(context)
            assert res.status == "failed"
            assert "Chưa có API key Autoenhance" in res.error_message
            print("  -> PASS: Autoenhance chặn bắt buộc phải có API key.")
        assert res.status == "failed"
        assert "Chưa có API key Autoenhance" in res.error_message
        print("  -> PASS: Autoenhance chặn bắt buộc phải có API key.")


def test_autohdr_job_mocked_success():
    print("[TEST] 4. Kiểm thử AutoHDRJob chạy thành công trọn vẹn (Mocked Network)...")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_p = Path(temp_dir)
        img = temp_p / "test_photo.jpg"
        img.write_bytes(b"fake_jpeg_binary_data")
        out_dir = temp_p / "output"

        # Mock Session
        mock_sess = MagicMock()

        # Mock API responses
        def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "generate_presigned_urls" in url:
                resp.json.return_value = {
                    "presignedUrls": [{"filename": "test_photo.jpg", "url": "https://s3.mock/upload"}]
                }
            elif "finalize_upload" in url:
                resp.json.return_value = {"info": "uploaded successfully"}
            elif "associate_and_run" in url:
                resp.json.return_value = {"success": True}
            elif "finalize-adjustment" in url:
                resp.json.return_value = {"url": "https://s3.mock/download/test_photo.jpg"}
            return resp

        def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "/photoshoots" in url and not url.endswith("/photos"):
                resp.json.return_value = {
                    "photoshoots": [{"id": 1234, "name": "fake_unique", "address": "test_addr", "status": "success"}]
                }
            elif url.endswith("/photos"):
                resp.json.return_value = {
                    "photos": [{"id": 999, "filename": "test_photo.jpg", "url": "https://s3.mock/download/test_photo.jpg"}]
                }
            return resp

        mock_sess.post.side_effect = mock_post
        mock_sess.get.side_effect = mock_get

        context = ExecutionContext(
            job_id="hdr_success_01",
            file_paths=[str(img)],
            output_dir=str(out_dir),
            credentials={"cookie": "autohdr_test_cookie", "user_id": "usr_123"},
            preferences={"address": "test_addr"},
        )

        job = AutoHDRJob(
            session=mock_sess,
            max_upload_workers=1,
            max_download_workers=1,
            poll_interval=0,
        )

        # Mock requests.put (S3 upload) và requests.get (S3 download)
        import requests
        original_put = requests.put
        original_get = requests.get

        def fake_s3_put(url, **kwargs):
            m = MagicMock()
            m.status_code = 200
            return m

        def fake_s3_get(url, **kwargs):
            m = MagicMock()
            m.status_code = 200
            m.iter_content.return_value = [b"enhanced_jpeg_content"]
            return m

        try:
            requests.put = fake_s3_put
            requests.get = fake_s3_get

            res = job.execute(context)
            assert res.status == "completed", f"Status should be completed, got {res.status}: {res.error_message}"
            assert res.remote_job_id == "1234"
            assert len(res.downloaded_files) == 1
            assert (out_dir / "test_photo.jpg").is_file()
            print("  -> PASS: AutoHDRJob hoàn tất toàn bộ 7 bước nghiệp vụ thành công.")
        finally:
            requests.put = original_put
            requests.get = original_get


def test_autoenhance_job_mocked_success():
    print("[TEST] 5. Kiểm thử AutoenhanceJob chạy thành công trọn vẹn (Mocked Network)...")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_p = Path(temp_dir)
        img = temp_p / "room.jpg"
        img.write_bytes(b"dummy_room_image")
        out_dir = temp_p / "out_ae"

        mock_sess = MagicMock()

        def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "/orders/" in url and not url.endswith("/process"):
                resp.json.return_value = {"order_id": "ord_ae_555", "order_name": "Test Order"}
            elif "/images/" in url:
                resp.json.return_value = {
                    "image_id": "img_999",
                    "s3PutObjectUrl": "https://s3.mock/upload",
                    "headers": {"Content-Type": "image/jpeg"},
                }
            elif "/process" in url:
                resp.json.return_value = {"status": "processing"}
            return resp

        def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "/orders/ord_ae_555" in url:
                resp.json.return_value = {
                    "id": "ord_ae_555",
                    "status": "completed",
                    "images": [
                        {"id": "img_999", "image_name": "room.jpg", "status": "processed", "enhanced": True}
                    ],
                }
            return resp

        mock_sess.post.side_effect = mock_post
        mock_sess.get.side_effect = mock_get

        context = ExecutionContext(
            job_id="ae_success_01",
            file_paths=[str(img)],
            output_dir=str(out_dir),
            credentials={"api_key": "test_api_key_ae_123"},
        )

        job = AutoenhanceJob(session=mock_sess, max_upload_workers=1, max_download_workers=1, poll_interval=0)

        import requests
        original_put = requests.put
        original_get = requests.get

        def fake_s3_put(url, **kwargs):
            m = MagicMock()
            m.status_code = 200
            return m

        def fake_s3_get(url, **kwargs):
            m = MagicMock()
            m.status_code = 200
            m.iter_content.return_value = [b"enhanced_png_content"]
            return m

        try:
            requests.put = fake_s3_put
            requests.get = fake_s3_get

            res = job.execute(context)
            assert res.status == "completed", f"Status should be completed, got {res.status}: {res.error_message}"
            assert res.remote_job_id == "ord_ae_555"
            assert len(res.downloaded_files) == 1
            assert (out_dir / "room.jpg").is_file()
            print("  -> PASS: AutoenhanceJob hoàn tất toàn bộ quy trình thành công.")
        finally:
            requests.put = original_put
            requests.get = original_get


def test_autohdr_s3_headers_and_single_error():
    print("[TEST] 6. Kiểm thử AutoHDR upload S3 có đủ headers và không duplicate ERROR log...")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_p = Path(temp_dir)
        img = temp_p / "photo1.jpg"
        img.write_bytes(b"content_12345")
        out_dir = temp_p / "out"

        mock_sess = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "presignedUrls": [{"filename": "photo1.jpg", "url": "https://s3.mock/upload"}]
        }
        mock_sess.post.return_value = mock_resp

        captured_headers = {}
        import requests
        original_put = requests.put

        def fake_put(url, data=None, headers=None, **kwargs):
            nonlocal captured_headers
            captured_headers = headers or {}
            resp = MagicMock()
            resp.status_code = 403  # Giả lập S3 bị từ chối
            resp.text = "Forbidden Access Denied"
            return resp

        logs = []
        context = ExecutionContext(
            job_id="hdr_err_01",
            file_paths=[str(img)],
            output_dir=str(out_dir),
            credentials={"cookie": "autohdr_cookie"},
            on_log=lambda lvl, msg: logs.append((lvl, msg)),
        )

        try:
            requests.put = fake_put
            job = AutoHDRJob(session=mock_sess, max_upload_workers=1)
            res = job.execute(context)

            # 1. Kiểm tra S3 PUT headers
            assert captured_headers.get("Content-Type") == "application/octet-stream"
            assert captured_headers.get("Content-Length") == str(len(b"content_12345"))
            assert captured_headers.get("x-amz-acl") == "private"

            # 2. Kiểm tra status và lỗi
            assert res.status == "failed"
            assert "HTTP 403" in res.error_message or "Upload S3 thất bại" in res.error_message

            # 3. Kiểm tra KHÔNG lặp log ERROR (chỉ ghi nhận 1 lần)
            error_logs = [msg for lvl, msg in logs if lvl == "ERROR"]
            assert len(error_logs) == 1, f"Expected exactly 1 ERROR log, got {len(error_logs)}: {error_logs}"
            print("  -> PASS: Headers S3 đầy đủ và ERROR log chỉ xuất hiện đúng 1 lần (không lặp 2 lần).")
        finally:
            requests.put = original_put


def test_autoenhance_create_order_uuid_payload():
    print("[TEST] 7. Kiểm thử Autoenhance tạo order gửi order_id (UUID) và name chuẩn API v3...")
    import uuid
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_p = Path(temp_dir)
        img = temp_p / "room.jpg"
        img.write_bytes(b"dummy")

        mock_sess = MagicMock()
        captured_payload = {}

        def mock_post(url, json=None, **kwargs):
            nonlocal captured_payload
            captured_payload = json or {}
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "order_id": captured_payload.get("order_id"),
                "name": captured_payload.get("name"),
                "status": "waiting",
            }
            return resp

        mock_sess.post.side_effect = mock_post
        logs = []

        context = ExecutionContext(
            job_id="ae_uuid_test",
            file_paths=[str(img)],
            output_dir=str(temp_p / "out"),
            credentials={"api_key": "dummy_key"},
            preferences={"order_name": "Project_Sunshine"},
            on_log=lambda lvl, msg: logs.append((lvl, msg)),
        )

        job = AutoenhanceJob(session=mock_sess)
        headers = job._get_headers("dummy_key")
        order_id = job._step_create_order(context, headers)

        assert order_id is not None
        assert "order_id" in captured_payload
        assert "name" in captured_payload
        assert "order_name" not in captured_payload  # Phải là 'name', không được là 'order_name'
        assert captured_payload["name"] == "Project_Sunshine"

        # Kiểm tra order_id là UUID hợp lệ
        parsed_uuid = uuid.UUID(captured_payload["order_id"])
        assert str(parsed_uuid) == captured_payload["order_id"]
        print("  -> PASS: Payload POST /orders/ đúng chuẩn v3: có order_id UUID và name.")


def test_autoenhance_trigger_process_and_preview_false_download():
    print("[TEST] 8. Kiểm thử Autoenhance process payload chuẩn OpenAPI & tải preview=false...")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_p = Path(temp_dir)
        img = temp_p / "room.jpg"
        img.write_bytes(b"dummy_content")

        mock_sess = MagicMock()
        captured_process_payload = {}

        def mock_post(url, json=None, **kwargs):
            nonlocal captured_process_payload
            resp = MagicMock()
            resp.status_code = 200
            if "/process" in url:
                captured_process_payload = json or {}
                resp.json.return_value = {"status": "processing"}
            return resp

        mock_sess.post.side_effect = mock_post

        context = ExecutionContext(
            job_id="ae_proc_test",
            file_paths=[str(img)],
            output_dir=str(temp_p / "out"),
            credentials={"api_key": "test_key"},
            preferences={
                "cloud_type": "LOW_CLOUD",
                "perspective_correction_mode": "PRESERVE_CONTENT",
                "lens_correction": True,
                "number_of_brackets_per_image": "1",
            },
        )

        job = AutoenhanceJob(session=mock_sess)
        headers = job._get_headers("test_key")

        # 1. Kiểm tra header có x-api-key
        assert headers.get("x-api-key") == "test_key"

        # 2. Chạy _step_trigger_process
        ok = job._step_trigger_process(context, headers, "order_123")
        assert ok is True

        # 3. Kiểm tra payload chuẩn OpenAPI
        assert captured_process_payload.get("sky_replacement") is True  # Phải là boolean True!
        assert captured_process_payload.get("cloud_type") == "LOW_CLOUD"  # Enum OpenAPI
        assert captured_process_payload.get("perspective_correction") is True
        assert captured_process_payload.get("perspective_correction_mode") == "PRESERVE_CONTENT"
        assert captured_process_payload.get("number_of_brackets_per_image") == "1"

        # 4. Kiểm tra URL tải ảnh có preview=false (không có watermark)
        captured_dl_url = ""
        captured_dl_headers = {}
        import requests
        orig_get = requests.get

        def fake_get(url, headers=None, **kwargs):
            nonlocal captured_dl_url, captured_dl_headers
            captured_dl_url = url
            captured_dl_headers = headers or {}
            resp = MagicMock()
            resp.status_code = 200
            resp.iter_content.return_value = [b"enhanced_img"]
            return resp

        try:
            requests.get = fake_get
            img_list = [{"image_id": "img_001", "image_name": "room.jpg"}]
            dl_ok = job._step_download_and_process(context, headers, img_list)
            assert dl_ok is True
            from libs.constants.autoenhance import ENHANCED_IMAGE_QUERY
            assert ENHANCED_IMAGE_QUERY in captured_dl_url
            print(f"  -> PASS: Payload process gửi boolean & download gọi {ENHANCED_IMAGE_QUERY} chuẩn xác.")
        finally:
            requests.get = orig_get


def test_autoenhance_smart_resume_skips_to_download():
    print("[TEST] 9. Kiểm thử Smart Resume Autoenhance bỏ qua bước upload & AI khi order đã hoàn tất...")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_p = Path(temp_dir)
        img = temp_p / "resumed_room.jpg"
        img.write_bytes(b"dummy_data")

        mock_sess = MagicMock()

        def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "/orders/order_completed_999" in url:
                resp.json.return_value = {
                    "order_id": "order_completed_999",
                    "status": "completed",
                    "images": [
                        {"image_id": "img_done_01", "image_name": "resumed_room.jpg", "status": "processed"}
                    ],
                }
            return resp

        mock_sess.get.side_effect = mock_get

        context = ExecutionContext(
            job_id="ae_resume_test",
            file_paths=[str(img)],
            output_dir=str(temp_p / "out"),
            credentials={"api_key": "test_key"},
            remote_job_id="order_completed_999",  # Giả lập đã có order từ phiên trước
        )

        job = AutoenhanceJob(session=mock_sess)
        job._step_create_order = MagicMock()
        job._step_upload_files = MagicMock()
        job._step_trigger_process = MagicMock()
        job._step_download_and_process = MagicMock(return_value=True)

        res = job.execute(context)

        assert res.status == "completed"
        # Xác nhận các bước trước download hoàn toàn KHÔNG bị gọi lại
        job._step_create_order.assert_not_called()
        job._step_upload_files.assert_not_called()
        job._step_trigger_process.assert_not_called()
        # Xác nhận đã nhảy thẳng đến bước download
        job._step_download_and_process.assert_called_once()
        print("  -> PASS: Smart Resume Autoenhance phát hiện order hoàn tất, nhảy thẳng đến download.")


def test_autohdr_smart_resume_skips_to_download():
    print("[TEST] 10. Kiểm thử Smart Resume AutoHDR bỏ qua upload khi đã có Photoshoot ID...")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_p = Path(temp_dir)
        img = temp_p / "hdr_room.jpg"
        img.write_bytes(b"dummy_data")

        mock_sess = MagicMock()

        context = ExecutionContext(
            job_id="hdr_resume_test",
            file_paths=[str(img)],
            output_dir=str(temp_p / "out"),
            credentials={"cookie": "test_cookie=abc"},
            remote_job_id="88888",  # Giả lập đã có photoshoot_id từ phiên trước
        )

        job = AutoHDRJob(session=mock_sess)
        job._step_presigned_urls = MagicMock()
        job._step_upload_files = MagicMock()
        job._step_finalize_upload = MagicMock()
        job._step_associate_and_run = MagicMock()
        job._step_poll_status = MagicMock()
        job._step_get_processed_urls = MagicMock(return_value=[{"filename": "hdr_room.jpg", "url": "http://example.com/dl"}])
        job._step_download = MagicMock(return_value=True)

        res = job.execute(context)

        assert res.status == "completed"
        # Xác nhận các bước upload/associate/poll KHÔNG bị gọi lại
        job._step_presigned_urls.assert_not_called()
        job._step_upload_files.assert_not_called()
        job._step_finalize_upload.assert_not_called()
        job._step_associate_and_run.assert_not_called()
        job._step_poll_status.assert_not_called()
        # Xác nhận nhảy thẳng đến bước get urls & download
        job._step_get_processed_urls.assert_called_once()
        job._step_download.assert_called_once()
        print("  -> PASS: Smart Resume AutoHDR phát hiện photoshoot hoàn tất, nhảy thẳng đến download.")


def test_autoenhance_anti_collision_duplicate_names():
    print("[TEST] 11. Kiểm thử chống ghi đè khi tải 2 ảnh trùng tên trong Autoenhance...")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_p = Path(temp_dir)
        out_dir = temp_p / "out"

        context = ExecutionContext(
            job_id="ae_collision_test",
            file_paths=[],
            output_dir=str(out_dir),
            credentials={"api_key": "test_key"},
        )

        job = AutoenhanceJob()
        headers = job._get_headers("test_key")

        # Giả lập 2 ảnh hoàn tất từ Autoenhance có CÙNG 1 TÊN 'image.jpg'
        completed_images = [
            {"image_id": "ade8a1c3-3e04-43d1-8acf-811b2186fc6c", "image_name": "image.jpg"},
            {"image_id": "9c21bff5-a158-4dd0-89cd-b60944c15fe1", "image_name": "image.jpg"},
        ]

        import requests
        orig_get = requests.get

        def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.iter_content.return_value = [b"dummy_binary_jpeg"]
            return resp

        try:
            requests.get = fake_get
            ok = job._step_download_and_process(context, headers, completed_images)
            assert ok is True

            # Kiểm tra 2 file đích phải có tên khác nhau, KHÔNG ĐƯỢC ghi đè nhau
            file1 = out_dir / "image.jpg"
            file2 = out_dir / "image_1.jpg"

            assert file1.is_file(), "File thứ nhất 'image.jpg' phải tồn tại"
            assert file2.is_file(), "File thứ hai 'image_1.jpg' phải tồn tại (không bị replace)"
            assert len(context.downloaded_files) == 2
            assert str(file1) in context.downloaded_files
            assert str(file2) in context.downloaded_files
            print("  -> PASS: Cả 2 ảnh trùng tên đều được lưu độc lập (image.jpg, image_1.jpg), không bị replace.")
        finally:
            requests.get = orig_get


def test_autohdr_anti_collision_duplicate_names():
    print("[TEST] 12. Kiểm thử chống ghi đè khi tải 2 ảnh trùng tên trong AutoHDR...")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_p = Path(temp_dir)
        out_dir = temp_p / "out"

        context = ExecutionContext(
            job_id="hdr_collision_test",
            file_paths=[],
            output_dir=str(out_dir),
            credentials={"cookie": "test_cookie"},
        )

        job = AutoHDRJob()

        # Giả lập 2 ảnh từ AutoHDR có CÙNG 1 TÊN 'photo.jpg'
        photo_urls = [
            {"filename": "photo.jpg", "url": "http://example.com/photo1.jpg"},
            {"filename": "photo.jpg", "url": "http://example.com/photo2.jpg"},
        ]

        import requests
        orig_get = requests.get

        def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.iter_content.return_value = [b"hdr_binary"]
            return resp

        try:
            requests.get = fake_get
            ok = job._step_download(context, photo_urls)
            assert ok is True

            file1 = out_dir / "photo.jpg"
            file2 = out_dir / "photo_1.jpg"

            assert file1.is_file(), "File thứ nhất 'photo.jpg' phải tồn tại"
            assert file2.is_file(), "File thứ hai 'photo_1.jpg' phải tồn tại (không bị replace)"
            assert len(context.downloaded_files) == 2
            assert str(file1) in context.downloaded_files
            assert str(file2) in context.downloaded_files
            print("  -> PASS: AutoHDR lưu độc lập cả 2 ảnh trùng tên (photo.jpg, photo_1.jpg), không bị replace.")
        finally:
            requests.get = orig_get


def test_autoenhance_download_job_from_order_id():
    print("[TEST] 13. Kiểm thử AutoenhanceDownloadJob tải trực tiếp từ Order ID (không cần file local)...")
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as temp_dir:
        out_p = Path(temp_dir) / "output"
        context = ExecutionContext(
            job_id="ae_dl_job_test",
            file_paths=[],  # Danh sách rỗng: không có file nguồn trên máy
            output_dir=str(out_p),
            credentials={"api_key": "test_api_key"},
            preferences={"order_id": "eb8a8b62-d95b-4413-8026-57e44b5f6453"},
        )

        mock_session = MagicMock()
        job = AutoenhanceDownloadJob(session=mock_session)

        # Mock fetch_order_status và download_order_images
        with patch("jobs.autoenhance_download.fetch_order_status") as mock_fetch, \
             patch("jobs.autoenhance_download.download_order_images") as mock_dl:

            mock_fetch.return_value = (
                "processed",
                [{"image_id": "img_001", "image_name": "image.jpg"}],
                1,
                {"name": "Test Cloud Order", "images": [{"image_id": "img_001"}]}
            )
            mock_dl.return_value = True

            result_ctx = job.execute(context)

            assert result_ctx.status == "completed"
            assert result_ctx.error_message == ""
            assert out_p.is_dir()
            mock_fetch.assert_called_once()
            mock_dl.assert_called_once()
            print("  -> PASS: AutoenhanceDownloadJob vượt qua hậu kiểm không cần file nguồn và hoàn tất tải về.")


def test_autohdr_download_job():
    print("[TEST] 11. Kiểm thử AutoHDRDownloadJob (tải ảnh qua photoshoot URL)...")
    from unittest.mock import patch, MagicMock

    with tempfile.TemporaryDirectory() as temp_dir:
        out_p = Path(temp_dir) / "output"
        context = ExecutionContext(
            job_id="hdr_dl_test",
            file_paths=[],
            output_dir=str(out_p),
            credentials={"cookie": "test_cookie=1"},
            preferences={"photoshoot_uuid": "11111111-2222-3333-4444-555555555555"},
        )

        mock_session = MagicMock()
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {"id": 12345}
        mock_session.get.return_value = mock_res

        job = AutoHDRDownloadJob(session=mock_session)

        with patch.object(job, "_step_get_processed_urls", return_value=[{"filename": "a.jpg", "url": "http://img.jpg"}]), \
             patch.object(job, "_step_download", return_value=True):
            res = job.execute(context)
            assert res.status == "completed"
            assert res.remote_job_id == "12345"
            print("  -> PASS: AutoHDRDownloadJob tải ảnh photoshoot thành công không cần file nguồn.")


def test_autoenhance_upload_presigned_retry_and_backoff():
    print("[TEST] 14. Kiểm thử Autoenhance upload đa luồng, retry khi gặp 429 và S3 lỗi...")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_p = Path(temp_dir)
        img1 = temp_p / "p1.jpg"
        img2 = temp_p / "p2.jpg"
        img1.write_bytes(b"content_1")
        img2.write_bytes(b"content_2")
        out_dir = temp_p / "out"

        mock_sess = MagicMock()
        call_counts = {"p1.jpg": 0, "p2.jpg": 0}

        def mock_post(url, **kwargs):
            resp = MagicMock()
            if "/orders/" in url and not url.endswith("/process"):
                resp.status_code = 200
                resp.json.return_value = {"order_id": "ord_retry_test"}
                return resp
            if "/images/" in url:
                payload = kwargs.get("json", {})
                img_name = payload.get("image_name")
                call_counts[img_name] = call_counts.get(img_name, 0) + 1
                # Giả lập p1 gặp 429 ở lần đầu tiên, lần 2 mới thành công
                if img_name == "p1.jpg" and call_counts["p1.jpg"] == 1:
                    resp.status_code = 429
                    resp.headers = {"Retry-After": "0"}
                    resp.text = "Rate limited"
                    return resp
                resp.status_code = 200
                resp.json.return_value = {"s3PutObjectUrl": f"https://s3.mock/{img_name}", "headers": {}}
                return resp
            resp.status_code = 200
            return resp

        mock_sess.post.side_effect = mock_post

        import requests
        put_counts = {}
        original_put = requests.put

        def fake_put(url, **kwargs):
            put_counts[url] = put_counts.get(url, 0) + 1
            resp = MagicMock()
            # Giả lập p2 bị lỗi 500 ở lần upload đầu tiên, lần 2 mới thành công
            if "p2.jpg" in url and put_counts[url] == 1:
                resp.status_code = 500
                resp.text = "Internal S3 error"
                return resp
            resp.status_code = 200
            return resp

        context = ExecutionContext(
            job_id="ae_retry_job",
            file_paths=[str(img1), str(img2)],
            output_dir=str(out_dir),
            credentials={"api_key": "dummy_key"},
        )
        job = AutoenhanceJob(session=mock_sess, max_upload_workers=2)
        context.ready_files = [img1, img2]

        try:
            requests.put = fake_put
            with patch("time.sleep", return_value=None):
                ok = job._step_upload_files(context, {"x-api-key": "dummy_key"}, "ord_retry_test")
            assert ok is True
            assert call_counts["p1.jpg"] == 2, f"p1.jpg should be called 2 times (retried after 429), got {call_counts['p1.jpg']}"
            assert put_counts["https://s3.mock/p2.jpg"] == 2, f"p2.jpg S3 PUT should be called 2 times (retried after 500), got {put_counts.get('https://s3.mock/p2.jpg')}"
            print("  -> PASS: Đa luồng lấy link & upload S3 tự động retry thành công khi gặp 429 và 500.")
        finally:
            requests.put = original_put


if __name__ == "__main__":
    test_registry_auto_registration()
    test_autohdr_job_missing_auth()
    test_autoenhance_job_missing_auth()
    test_autohdr_job_mocked_success()
    test_autoenhance_job_mocked_success()
    test_autohdr_s3_headers_and_single_error()
    test_autoenhance_create_order_uuid_payload()
    test_autoenhance_trigger_process_and_preview_false_download()
    test_autoenhance_smart_resume_skips_to_download()
    test_autohdr_smart_resume_skips_to_download()
    test_autohdr_download_job()
    test_autoenhance_anti_collision_duplicate_names()
    test_autohdr_anti_collision_duplicate_names()
    test_autoenhance_download_job_from_order_id()
    test_autoenhance_upload_presigned_retry_and_backoff()
    print("\n✅ TOÀN BỘ 14 BÀI TEST TÍCH HỢP JOBS ĐỀU ĐẠT 100%!")
