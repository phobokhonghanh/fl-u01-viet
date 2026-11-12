"""
Unit tests cho JobRunner (Bộ điều phối Job chạy nền).
Địa điểm: autohdr_exe/1tm/tests/test_runner.py
"""
import sys
import tempfile
import threading
import time
from pathlib import Path

# Đưa 1tm và autohdr_exe vào sys.path
TM_DIR = Path(__file__).resolve().parent.parent
LIBS_DIR = TM_DIR / "libs"
AUTOHDR_DIR = TM_DIR.parent
sys.path.insert(0, str(TM_DIR))
sys.path.insert(0, str(LIBS_DIR))
sys.path.insert(0, str(AUTOHDR_DIR))

from libs.executor.base import BaseExecutor, ExecutionContext, ExecutorRegistry
from jobs import JobRunner


class MockSlowJob(BaseExecutor):
    """Job giả lập chạy nhiều bước có thời gian chờ."""

    def transform(self, context: ExecutionContext) -> bool:
        context.set_step("mock_step_1", "Bước 1...", progress_pct=25)
        time.sleep(0.05)
        if context.is_cancelled():
            return False

        context.set_step("mock_step_2", "Bước 2...", progress_pct=50)
        time.sleep(0.05)
        if context.is_cancelled():
            return False

        context.set_step("mock_step_3", "Bước 3...", progress_pct=100)
        return True


class MockLongRunningJob(BaseExecutor):
    """Job giả lập chạy rất lâu để kiểm thử dừng khẩn cấp (stop_job)."""

    def transform(self, context: ExecutionContext) -> bool:
        for i in range(100):
            if context.is_cancelled():
                return False
            time.sleep(0.02)
        return True


def test_submit_unknown_engine():
    print("[TEST] 1. Kiểm thử submit engine không tồn tại...")
    runner = JobRunner()
    try:
        runner.submit_job(
            engine="non_existent_engine",
            file_paths=[],
            output_dir="",
        )
        assert False, "Phải ném ValueError khi engine chưa đăng ký"
    except ValueError as e:
        assert "chưa được đăng ký trong hệ thống" in str(e)
        print("  -> PASS: Bắt lỗi engine không tồn tại chính xác.")


def test_submit_async_execution():
    print("[TEST] 2. Kiểm thử submit_job chạy ngầm đa luồng và bắn callback...")
    ExecutorRegistry.register("mock_slow", MockSlowJob)

    runner = JobRunner()
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_p = Path(temp_dir)
        test_file = temp_p / "img.jpg"
        test_file.write_bytes(b"data")
        out_dir = temp_p / "output"

        logs = []
        progress = []
        finished_event = threading.Event()
        finished_context = []

        def on_log(lvl, msg):
            logs.append((lvl, msg))

        def on_prog(step, desc, pct):
            progress.append((step, desc, pct))

        def on_fin(ctx):
            finished_context.append(ctx)
            finished_event.set()

        ctx = runner.submit_job(
            engine="mock_slow",
            file_paths=[str(test_file)],
            output_dir=str(out_dir),
            ui_callback_log=on_log,
            ui_callback_progress=on_prog,
            ui_callback_finished=on_fin,
        )

        # Kiểm tra non-blocking: Hàm submit trả về ngay lập tức
        assert ctx.job_id is not None
        assert ctx.job_id.startswith("job_")
        print("  -> Non-blocking verified: submit_job trả về ngay lập tức.")

        # Đợi background thread hoàn thành
        ok = finished_event.wait(timeout=2.0)
        assert ok, "Job phải hoàn tất trong 2 giây"
        assert ctx.status == "completed"
        assert len(finished_context) == 1
        assert len(progress) >= 3
        assert len(logs) > 0

        # Kiểm tra runner.get_job()
        tracked = runner.get_job(ctx.job_id)
        assert tracked is ctx
        assert tracked.status == "completed"
        print("  -> PASS: Job chạy ngầm trên background thread và hoàn tất thành công.")


def test_stop_job():
    print("[TEST] 3. Kiểm thử dừng khẩn cấp tác vụ (stop_job)...")
    ExecutorRegistry.register("mock_long", MockLongRunningJob)

    runner = JobRunner()
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_p = Path(temp_dir)
        test_file = temp_p / "img.jpg"
        test_file.write_bytes(b"data")
        out_dir = temp_p / "output"

        finished_event = threading.Event()

        ctx = runner.submit_job(
            engine="mock_long",
            file_paths=[str(test_file)],
            output_dir=str(out_dir),
            ui_callback_finished=lambda c: finished_event.set(),
        )

        time.sleep(0.04)  # Để job kịp khởi động

        # Bấm Dừng tác vụ
        stop_success = runner.stop_job(ctx.job_id)
        assert stop_success is True

        finished_event.wait(timeout=1.0)
        assert ctx.status == "cancelled"
        print("  -> PASS: stop_job dừng tác vụ tức thì và cập nhật status = cancelled.")


def test_list_and_clear_jobs():
    print("[TEST] 4. Kiểm thử list_jobs và clear_finished_jobs...")
    runner = JobRunner()
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_p = Path(temp_dir)
        test_file = temp_p / "img.jpg"
        test_file.write_bytes(b"data")

        fin_ev = threading.Event()
        ctx = runner.submit_job(
            engine="mock_slow",
            file_paths=[str(test_file)],
            output_dir=str(temp_p / "out"),
            ui_callback_finished=lambda c: fin_ev.set(),
        )
        fin_ev.wait(timeout=2.0)

        assert len(runner.list_jobs()) == 1

        cleared_count = runner.clear_finished_jobs()
        assert cleared_count == 1
        assert len(runner.list_jobs()) == 0
        print("  -> PASS: list_jobs và clear_finished_jobs hoạt động hoàn hảo.")


class MockResumableJob(BaseExecutor):
    """Job giả lập bị lỗi ở lần chạy đầu tiên và thành công khi được resume."""
    run_count = 0

    def transform(self, context: ExecutionContext) -> bool:
        MockResumableJob.run_count += 1
        if MockResumableJob.run_count == 1:
            context.error_message = "Mô phỏng lỗi mạng tại bước 2"
            return False
        # Lần chạy thứ 2 (Resume): Tiếp tục và hoàn tất
        context.set_step("mock_resume_step", "Đang khôi phục và hoàn tất...", progress_pct=100)
        return True


def test_resume_job():
    print("[TEST] 5. Kiểm thử khôi phục tác vụ từ bước lỗi (resume_job)...")
    ExecutorRegistry.register("mock_resumable", MockResumableJob)
    MockResumableJob.run_count = 0

    runner = JobRunner()
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_p = Path(temp_dir)
        test_file = temp_p / "img.jpg"
        test_file.write_bytes(b"data")

        first_finish_ev = threading.Event()
        resume_finish_ev = threading.Event()

        # 1. Chạy lần đầu -> Sẽ gặp lỗi
        ctx = runner.submit_job(
            engine="mock_resumable",
            file_paths=[str(test_file)],
            output_dir=str(temp_p / "out"),
            ui_callback_finished=lambda c: first_finish_ev.set(),
        )

        ok1 = first_finish_ev.wait(timeout=2.0)
        assert ok1, "Lần chạy đầu tiên phải hoàn thành (báo lỗi)"
        assert ctx.status == "failed"
        assert "Mô phỏng lỗi mạng" in ctx.error_message

        # 2. Thử resume một job không tồn tại -> Phải trả về None
        none_res = runner.resume_job("job_not_found")
        assert none_res is None

        # 3. Kích hoạt resume_job cho job bị lỗi
        resumed_ctx = runner.resume_job(
            job_id=ctx.job_id,
            ui_callback_finished=lambda c: resume_finish_ev.set(),
        )
        assert resumed_ctx is ctx

        ok2 = resume_finish_ev.wait(timeout=2.0)
        assert ok2, "Resume job phải hoàn thành"
        assert ctx.status == "completed"
        assert ctx.error_message == ""
        assert MockResumableJob.run_count == 2
        print("  -> PASS: resume_job khôi phục tác vụ bị lỗi và thực thi thành công mỹ mãn.")


def test_job_runner_output_dir_has_job_id():
    print("[TEST] 6. Kiểm thử tự động tạo thư mục đích theo id job: <output_dir>/<job_id>/...")
    runner = JobRunner()
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_p = Path(temp_dir)
        test_file = temp_p / "img.jpg"
        test_file.write_bytes(b"data")
        base_dir = temp_p / "image_root"

        ctx = runner.submit_job(
            engine="mock_slow",
            file_paths=[str(test_file)],
            output_dir=str(base_dir),
        )

        expected_dir = str(base_dir.resolve() / ctx.job_id)
        assert ctx.output_dir == expected_dir, f"output_dir phải kết thúc bằng {ctx.job_id}, got: {ctx.output_dir}"
        print(f"  -> PASS: JobRunner tự động định tuyến output_dir sang {ctx.output_dir}.")


if __name__ == "__main__":
    test_submit_unknown_engine()
    test_submit_async_execution()
    test_stop_job()
    test_list_and_clear_jobs()
    test_resume_job()
    test_job_runner_output_dir_has_job_id()
    print("\n✅ TOÀN BỘ 6 BÀI TEST JOBRUNNER ĐỀU ĐẠT 100%!")
