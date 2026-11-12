"""
Unit tests cho các tiện ích mới:
- libs/utils/thread.py (run_parallel, BatchResult)
- libs/utils/poll.py (poll_until, PollStatus, PollResult)
"""
import sys
import threading
import time
from pathlib import Path

# Đưa 1tm và autohdr_exe vào sys.path để chạy test độc lập
TM_DIR = Path(__file__).resolve().parent.parent
LIBS_DIR = TM_DIR / "libs"
sys.path.insert(0, str(TM_DIR))
sys.path.insert(0, str(LIBS_DIR))

from libs.utils import (
    run_parallel,
    BatchResult,
    poll_until,
    PollStatus,
    PollResult,
    fetch_order_status,
    poll_order_completion,
    apply_pil_postprocess,
    download_order_images,
)


def test_run_parallel_success():
    print("[TEST] 1. Kiểm thử run_parallel thành công hoàn toàn...")
    items = [1, 2, 3, 4, 5]
    progress_calls = []

    def square(x: int) -> int:
        time.sleep(0.01)
        return x * x

    def on_prog(cur, tot, item, res):
        progress_calls.append((cur, tot, item, res))

    res = run_parallel(items, square, max_workers=3, on_progress=on_prog)

    assert res.all_succeeded is True
    assert len(res.succeeded) == 5
    assert len(res.failed) == 0
    assert res.is_cancelled is False
    assert len(progress_calls) == 5
    results = sorted([val for _, val in res.succeeded])
    assert results == [1, 4, 9, 16, 25]
    print("  -> PASS: run_parallel tính toán đa luồng và báo tiến độ chính xác.")


def test_run_parallel_with_errors():
    print("[TEST] 2. Kiểm thử run_parallel khi có task bị lỗi...")
    items = [1, 2, 0, 4]

    def divide_ten_by(x: int) -> float:
        if x == 0:
            raise ZeroDivisionError("Chia cho 0!")
        return 10.0 / x

    res = run_parallel(items, divide_ten_by, max_workers=2)

    assert res.all_succeeded is False
    assert len(res.succeeded) == 3
    assert len(res.failed) == 1
    failed_item, exc = res.failed[0]
    assert failed_item == 0
    assert isinstance(exc, ZeroDivisionError)
    print("  -> PASS: run_parallel cô lập ngoại lệ an toàn, không làm chết pool.")


def test_run_parallel_cancellation():
    print("[TEST] 3. Kiểm thử run_parallel hủy tác vụ an toàn...")
    items = list(range(20))
    stop_event = threading.Event()

    def slow_worker(x: int) -> int:
        if x == 2:
            stop_event.set()  # Phát tín hiệu hủy sau khi chạm số 2
        time.sleep(0.02)
        return x

    res = run_parallel(items, slow_worker, max_workers=2, stop_event=stop_event)

    assert res.is_cancelled is True
    assert len(res.succeeded) < 20  # Không chạy hết toàn bộ 20 items
    print(f"  -> PASS: run_parallel dừng sớm khi nhận stop_event (chạy được {len(res.succeeded)}/20).")


def test_poll_until_success():
    print("[TEST] 4. Kiểm thử poll_until thành công sau vài lần thử...")
    attempt_count = 0

    def check_status(attempt: int):
        nonlocal attempt_count
        attempt_count += 1
        if attempt < 3:
            return PollStatus.IN_PROGRESS, None, "Đang chờ..."
        return PollStatus.SUCCESS, "order_done_123", "Hoàn tất thành công"

    res = poll_until(check_status, poll_interval=0.01, max_retries=10)

    assert res.is_success is True
    assert res.data == "order_done_123"
    assert res.attempts == 3
    print("  -> PASS: poll_until hoàn tất đúng trạng thái SUCCESS sau 3 lần thử.")


def test_poll_until_timeout_retries():
    print("[TEST] 5. Kiểm thử poll_until hết lượt thử (max_retries timeout)...")
    def always_pending(attempt: int):
        return PollStatus.IN_PROGRESS, None, "Vẫn đang xử lý..."

    res = poll_until(always_pending, poll_interval=0.01, max_retries=3)

    assert res.status == PollStatus.TIMEOUT
    assert "Vượt quá số lần kiểm tra tối đa" in res.error_message
    assert res.attempts == 4  # attempt 4 vượt quá max_retries=3
    print("  -> PASS: poll_until kích hoạt timeout theo max_retries chính xác.")


def test_poll_until_timeout_seconds():
    print("[TEST] 6. Kiểm thử poll_until timeout theo thời gian (max_seconds)...")
    def always_pending(attempt: int):
        return PollStatus.IN_PROGRESS, None, "Vẫn đang xử lý..."

    res = poll_until(always_pending, poll_interval=0.05, max_seconds=0.1)

    assert res.status == PollStatus.TIMEOUT
    assert "Quá thời gian chờ xử lý" in res.error_message
    print("  -> PASS: poll_until kích hoạt timeout theo max_seconds chính xác.")


def test_poll_until_instant_cancellation():
    print("[TEST] 7. Kiểm thử poll_until dừng lập tức (0ms latency)...")
    stop_event = threading.Event()
    stop_event.set()  # Phát cờ dừng ngay lập tức

    start = time.time()
    res = poll_until(
        lambda a: (PollStatus.IN_PROGRESS, None, "waiting"),
        poll_interval=10.0,  # Nếu sleep thường sẽ bị kẹt 10 giây!
        stop_event=stop_event,
    )
    elapsed = time.time() - start

    assert res.status == PollStatus.CANCELLED
    assert elapsed < 0.1  # Thoát tức thì dưới 100ms
    print(f"  -> PASS: poll_until dừng tức thì trong {elapsed:.4f}s (không bị kẹt sleep 10s).")


def test_progress_reporter_on_failure():
    print("[TEST] 8. Kiểm thử make_progress_reporter không báo giả 'Đã upload' khi gặp lỗi...")
    from libs.utils.common import make_progress_reporter

    class DummyContext:
        def __init__(self):
            self.steps = []
            self.logs = []

        def set_step(self, step, desc, progress_pct=-1):
            self.steps.append((step, desc, progress_pct))

        def log(self, level, msg):
            self.logs.append((level, msg))

    ctx = DummyContext()
    reporter = make_progress_reporter(ctx, "upload", "upload", 0, 100)

    def worker(x: int):
        if x == 0:
            raise ValueError("Lỗi file hỏng")
        return x

    res = run_parallel([1, 0], worker, max_workers=1, on_progress=reporter)
    assert len(res.succeeded) == 1
    assert len(res.failed) == 1

    # Kiểm tra rằng không có bước nào báo "Đã upload 2/2 ảnh"
    all_descriptions = [desc for _, desc, _ in ctx.steps]
    assert "Đã upload 2/2 ảnh" not in all_descriptions
    # Đã có cảnh báo lỗi được ghi lại
    warn_logs = [msg for lvl, msg in ctx.logs if lvl == "WARN"]
    assert any("Lỗi upload" in w for w in warn_logs)
    print("  -> PASS: make_progress_reporter phân biệt chính xác thành công và thất bại.")


def test_autoenhance_utils_fetch_and_postprocess():
    print("[TEST] 9. Kiểm thử fetch_order_status và apply_pil_postprocess trong utils...")
    import tempfile
    from unittest.mock import MagicMock
    from PIL import Image

    # 1. Test fetch_order_status
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "processed",
        "images": [
            {"image_id": "img-1", "image_name": "photo.jpg", "status": "processed", "enhanced": True},
            {"image_id": "img-2", "image_name": "photo.jpg", "status": "failed", "error": True},
        ]
    }
    mock_session.get.return_value = mock_resp

    status, completed, total, raw = fetch_order_status(mock_session, {"x-api-key": "k"}, "ord-123")
    assert status == "processed"
    assert len(completed) == 1
    assert completed[0]["image_id"] == "img-1"
    assert total == 2

    # 2. Test apply_pil_postprocess
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / "temp.png"
        dest = Path(tmpdir) / "final.jpg"

        # Tạo ảnh RGBA tạm
        im = Image.new("RGBA", (100, 100), (255, 0, 0, 128))
        im.save(src, "PNG")

        apply_pil_postprocess(src, dest, target_dims=(50, 50))
        assert dest.is_file()
        with Image.open(dest) as fin:
            assert fin.mode == "RGB"
            assert fin.size == (50, 50)
            assert fin.info.get("dpi") == (300, 300)

    print("  -> PASS: fetch_order_status và apply_pil_postprocess hoạt động chuẩn xác.")


def test_autoenhance_utils_download_order_images():
    print("[TEST] 10. Kiểm thử download_order_images: chống trùng tên & smart resume...")
    import tempfile
    from io import BytesIO
    from unittest.mock import MagicMock
    from PIL import Image

    class MockContext:
        def __init__(self, out_dir):
            self.output_dir = out_dir
            self.logs = []
            self.extra_data = {}
            self.stop_event = threading.Event()
            self.downloaded_files = []
            self.meta_dims = {}

        def log(self, lvl, msg):
            self.logs.append((lvl, msg))

        def set_step(self, step, desc, progress_pct=0):
            pass

        def is_cancelled(self):
            return self.stop_event.is_set()

    # Tạo buffer ảnh 1x1 giả lập dữ liệu trả về từ server
    buf = BytesIO()
    Image.new("RGB", (10, 10), (0, 255, 0)).save(buf, "JPEG")
    fake_bytes = buf.getvalue()

    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_content.return_value = [fake_bytes]
    mock_session.get.return_value = mock_resp

    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = MockContext(tmpdir)
        completed_images = [
            {"image_id": "uuid-1", "image_name": "view.jpg"},
            {"image_id": "uuid-2", "image_name": "view.jpg"},  # Trùng tên
        ]

        ok = download_order_images(ctx, mock_session, {"x-api-key": "k"}, completed_images, max_workers=2)
        assert ok is True
        assert len(ctx.downloaded_files) == 2

        f1 = Path(tmpdir) / "view.jpg"
        f2 = Path(tmpdir) / "view_1.jpg"
        assert f1.is_file() and f1.stat().st_size > 0
        assert f2.is_file() and f2.stat().st_size > 0

        # Lần 2: Chạy lại trên cùng thư mục -> Smart Resume phải bỏ qua cả 2 file
        mock_session.reset_mock()
        ctx2 = MockContext(tmpdir)
        # Giữ lại download_map để map đúng file
        ctx2.extra_data["ae_download_map"] = ctx.extra_data.get("ae_download_map", {})
        ok2 = download_order_images(ctx2, mock_session, {"x-api-key": "k"}, completed_images, max_workers=2)
        assert ok2 is True
        # Không có request mạng nào được gửi đi vì cả 2 file đều đã tồn tại trên máy
        mock_session.get.assert_not_called()
        resume_logs = [msg for lvl, msg in ctx2.logs if "[RESUME]" in msg]
        assert len(resume_logs) == 2

    print("  -> PASS: download_order_images chống trùng tên và tự động Smart Resume xuất sắc.")


if __name__ == "__main__":
    test_run_parallel_success()
    test_run_parallel_with_errors()
    test_run_parallel_cancellation()
    test_poll_until_success()
    test_poll_until_timeout_retries()
    test_poll_until_timeout_seconds()
    test_poll_until_instant_cancellation()
    test_progress_reporter_on_failure()
    test_autoenhance_utils_fetch_and_postprocess()
    test_autoenhance_utils_download_order_images()
    print("\n✅ TOÀN BỘ 10 BÀI TEST UTILS ĐỀU ĐẠT 100%!")

