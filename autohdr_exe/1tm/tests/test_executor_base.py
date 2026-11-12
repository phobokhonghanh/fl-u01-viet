"""
Unit tests for BaseExecutor, ExecutionContext, and ExecutorRegistry.
Bao gồm kiểm thử:
- Luồng thực thi chuẩn
- Dừng tác vụ (cancellation)
- Bắt lỗi ngoại lệ
- Hậu kiểm chốt chặn (Post-condition Validation / Gatekeeper) cho pre_transform
- ExecutorRegistry
"""
import os
import sys
import tempfile
import threading
from pathlib import Path

# Thêm thư mục 1tm/libs vào sys.path để test độc lập
LIBS_DIR = Path(__file__).resolve().parent.parent / "libs"
sys.path.insert(0, str(LIBS_DIR))

from executor import BaseExecutor, ExecutionContext, ExecutorRegistry


class DummySuccessExecutor(BaseExecutor):
    """Giả lập một Executor chạy thành công với nhiều bước động."""

    def transform(self, context: ExecutionContext) -> bool:
        context.set_step("mock_upload", "Đang upload ảnh lên mock cloud...", progress_pct=30)
        context.set_step("mock_ai_process", "Đang xử lý AI...", progress_pct=70)
        context.downloaded_files = [f"{f.stem}_enhanced.jpg" for f in context.ready_files]
        context.set_step("mock_download", "Đã tải xong ảnh về máy.", progress_pct=90)
        return True


class DummyFailingExecutor(BaseExecutor):
    """Giả lập một Executor bị lỗi giữa chừng."""

    def transform(self, context: ExecutionContext) -> bool:
        context.set_step("mock_upload", "Upload bắt đầu...")
        context.error_message = "Lỗi kết nối máy chủ mock cloud."
        return False


class DummyBadPreTransformExecutor(BaseExecutor):
    """Lớp con cố tình làm hỏng dữ liệu trong pre_transform: tạo file 0 byte."""

    def pre_transform(self, context: ExecutionContext) -> bool:
        super().pre_transform(context)
        # Giả lập convert ảnh bị lỗi sinh ra file 0 byte
        bad_file = Path(context.output_dir) / "corrupted_raw_convert.jpg"
        bad_file.write_bytes(b"")  # 0 byte
        context.ready_files = [bad_file]
        return True

    def transform(self, context: ExecutionContext) -> bool:
        return True


class DummyEmptyReadyFilesExecutor(BaseExecutor):
    """Lớp con lỡ tay gán ready_files = [] rỗng."""

    def pre_transform(self, context: ExecutionContext) -> bool:
        super().pre_transform(context)
        context.ready_files = []
        return True

    def transform(self, context: ExecutionContext) -> bool:
        return True


def test_successful_execution():
    print("[TEST] 1. Kiểm thử luồng thực thi thành công đầy đủ...")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        test_file1 = temp_path / "img1.jpg"
        test_file2 = temp_path / "img2.jpg"
        test_file1.write_bytes(b"dummy_image_1")
        test_file2.write_bytes(b"dummy_image_2")
        out_dir = temp_path / "output"

        logs = []
        steps = []

        context = ExecutionContext(
            job_id="job_001",
            file_paths=[str(test_file1), str(test_file2)],
            output_dir=str(out_dir),
            on_log=lambda lvl, msg: logs.append((lvl, msg)),
            on_step_change=lambda step, desc, pct: steps.append((step, desc, pct)),
        )

        executor = DummySuccessExecutor()
        res = executor.execute(context)

        assert res.status == "completed", f"Status must be completed, got {res.status}"
        assert len(res.ready_files) == 2, f"Ready files count must be 2, got {len(res.ready_files)}"
        assert len(res.downloaded_files) == 2
        assert out_dir.is_dir(), "Output dir must be created"
        assert res.duration_seconds >= 0.0

        recorded_step_names = [s[0] for s in steps]
        assert "prepare" in recorded_step_names
        assert "mock_upload" in recorded_step_names
        assert "mock_ai_process" in recorded_step_names
        assert "mock_download" in recorded_step_names
        assert "cleanup" in recorded_step_names
        print("  -> PASS: Luồng chạy thành công, tạo thư mục và ghi nhận bước động chính xác.")


def test_failing_execution():
    print("[TEST] 2. Kiểm thử khi Provider trả về False...")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        test_file = temp_path / "test.jpg"
        test_file.write_bytes(b"data")

        context = ExecutionContext(
            job_id="job_002",
            file_paths=[str(test_file)],
            output_dir=str(temp_path / "out"),
        )

        executor = DummyFailingExecutor()
        res = executor.execute(context)

        assert res.status == "failed"
        assert "Lỗi kết nối máy chủ mock cloud." in res.error_message
        print("  -> PASS: Bắt lỗi thất bại đúng thông điệp.")


def test_cancellation():
    print("[TEST] 3. Kiểm thử dừng tác vụ an toàn bằng stop_event...")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        test_file = temp_path / "test.jpg"
        test_file.write_bytes(b"data")

        stop_ev = threading.Event()
        stop_ev.set()  # Phát tín hiệu dừng ngay từ đầu

        context = ExecutionContext(
            job_id="job_003",
            file_paths=[str(test_file)],
            output_dir=str(temp_path / "out"),
            stop_event=stop_ev,
        )

        executor = DummySuccessExecutor()
        res = executor.execute(context)

        assert res.status == "cancelled"
        print("  -> PASS: Tác vụ dừng tức thì khi có stop_event.")


def test_gatekeeper_empty_ready_files():
    print("[TEST] 4. Kiểm thử HẬU KIỂM CHỐT CHẶN: Lớp con làm rỗng ready_files...")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        test_file = temp_path / "test.jpg"
        test_file.write_bytes(b"data")

        context = ExecutionContext(
            job_id="job_004",
            file_paths=[str(test_file)],
            output_dir=str(temp_path / "out"),
        )

        executor = DummyEmptyReadyFilesExecutor()
        res = executor.execute(context)

        assert res.status == "failed"
        assert "ready_files' bị rỗng sau bước pre_transform" in res.error_message
        print("  -> PASS: Lớp cha chặn thành công khi ready_files bị rỗng.")


def test_gatekeeper_zero_byte_file():
    print("[TEST] 5. Kiểm thử HẬU KIỂM CHỐT CHẶN: Lớp con sinh ra file 0 byte...")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        test_file = temp_path / "test.jpg"
        test_file.write_bytes(b"data")

        context = ExecutionContext(
            job_id="job_005",
            file_paths=[str(test_file)],
            output_dir=str(temp_path / "out"),
        )

        executor = DummyBadPreTransformExecutor()
        res = executor.execute(context)

        assert res.status == "failed"
        assert "File bị lỗi 0 byte" in res.error_message
        print("  -> PASS: Lớp cha chặn đứng file 0 byte (ngăn không cho gửi lên Cloud).")


def test_registry():
    print("[TEST] 6. Kiểm thử ExecutorRegistry...")
    ExecutorRegistry.clear()
    assert ExecutorRegistry.list_available() == []

    ExecutorRegistry.register("AutoHDR", DummySuccessExecutor)
    ExecutorRegistry.register("autoenhance", DummyFailingExecutor)

    assert ExecutorRegistry.list_available() == ["autoenhance", "autohdr"]
    assert ExecutorRegistry.get("autohdr") is DummySuccessExecutor
    assert ExecutorRegistry.get("AUTOENHANCE") is DummyFailingExecutor
    assert ExecutorRegistry.get("unknown") is None
    print("  -> PASS: ExecutorRegistry hoạt động chuẩn xác, không phân biệt hoa thường.")


if __name__ == "__main__":
    test_successful_execution()
    test_failing_execution()
    test_cancellation()
    test_gatekeeper_empty_ready_files()
    test_gatekeeper_zero_byte_file()
    test_registry()
    print("\n✅ TOÀN BỘ CÁC BÀI TEST (BAO GỒM HẬU KIỂM CHỐT CHẶN) ĐỀU ĐẠT 100%!")
