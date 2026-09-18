"""Tests for Autoenhance Batch Processing, Entitlements, and Resumption."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from core.autoenhance.workflow import (
    autoenhance_executor,
    build_autoenhance_outputs,
    restart_workflow_job,
    run_workflow,
)
from core.shared.jobs import (
    BatchResult,
    JobAttempt,
    JobCapacityExceededError,
    JobLimits,
    JobPlan,
    JobRecord,
    JobResult,
    JobSpec,
    JobStore,
    OutputSpec,
    plan_jobs,
)
from core.shared.licensing import require_access
from core.shared.licensing.models import (
    LicenseResult,
    LicensingAccessError,
)


def _create_dummy_images(dir_path: Path, count: int) -> list[Path]:
    dir_path.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(count):
        p = dir_path / f"img_{i:03d}.jpg"
        Image.new("RGB", (20, 20), color=(i * 10 % 255, 100, 100)).save(p, "JPEG")
        paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# 1. Phân quyền (Entitlements): Lite vs Plus
# ---------------------------------------------------------------------------

def test_lite_mode_single_20_outputs_allowed(tmp_path: Path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _create_dummy_images(src, 20)

    with patch("core.shared.licensing.check", return_value=LicenseResult(valid=True, engine="autoenhance", level="lite")), \
         patch("core.autoenhance.workflow.autoenhance_executor") as mock_exec:
        mock_exec.return_value = JobResult(
            job_id="job_001", status="success", attempt_no=1, step="export", started_at=0, finished_at=1
        )
        res = run_workflow(input_dir=src, output_dir=dst, mode="single", api_key="dummy_key")
        assert res.status == "success"
        assert len(res.job_results) == 1


def test_lite_mode_single_21_outputs_rejected(tmp_path: Path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _create_dummy_images(src, 21)

    with patch("core.shared.licensing.check", return_value=LicenseResult(valid=True, engine="autoenhance", level="lite")):
        with pytest.raises(JobCapacityExceededError, match="tối đa 20 outputs"):
            run_workflow(input_dir=src, output_dir=dst, mode="single", api_key="dummy_key")


def test_lite_mode_batch_mode_rejected_by_licensing(tmp_path: Path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _create_dummy_images(src, 10)

    with patch("core.shared.licensing.check", return_value=LicenseResult(valid=True, engine="autoenhance", level="lite")):
        # 1. Direct require_access raises LicensingAccessError
        with pytest.raises(LicensingAccessError, match="chỉ hỗ trợ chế độ 'single'"):
            require_access("autoenhance", "batch")

        # 2. Workflow fails early at activation step
        res = run_workflow(input_dir=src, output_dir=dst, mode="batch", api_key="dummy_key")
        assert res.status == "failed"
        assert len(res.job_results) >= 1
        assert res.job_results[0].step == "activation"
        assert res.job_results[0].status == "failed"
        assert "chỉ hỗ trợ chế độ 'single'" in res.job_results[0].error_message


def test_plus_mode_single_21_outputs_rejected(tmp_path: Path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _create_dummy_images(src, 21)

    with patch("core.shared.licensing.check", return_value=LicenseResult(valid=True, engine="autoenhance", level="plus")):
        # Plus single mode still enforces single mode cap (max 20 per job)
        with pytest.raises(JobCapacityExceededError, match="tối đa 20 outputs"):
            run_workflow(input_dir=src, output_dir=dst, mode="single", api_key="dummy_key")


def test_plus_mode_batch_60_outputs_split_3_jobs_success(tmp_path: Path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _create_dummy_images(src, 60)

    with patch("core.shared.licensing.check", return_value=LicenseResult(valid=True, engine="autoenhance", level="plus")), \
         patch("core.autoenhance.workflow.autoenhance_executor") as mock_exec:
        mock_exec.side_effect = lambda spec, ctx: JobResult(
            job_id=spec.job_id, status="success", attempt_no=ctx.attempt_no, step="export", started_at=0, finished_at=1
        )
        res = run_workflow(input_dir=src, output_dir=dst, mode="batch", api_key="dummy_key")
        assert res.status == "success"
        assert len(res.job_results) == 3
        for job_res in res.job_results:
            assert job_res.status == "success"


def test_plus_mode_batch_61_outputs_rejected(tmp_path: Path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _create_dummy_images(src, 61)

    with patch("core.shared.licensing.check", return_value=LicenseResult(valid=True, engine="autoenhance", level="plus")):
        with pytest.raises(JobCapacityExceededError, match="vượt quá giới hạn batch tối đa"):
            run_workflow(input_dir=src, output_dir=dst, mode="batch", api_key="dummy_key")


# ---------------------------------------------------------------------------
# 2. Restart Mechanics: Order Creation & Download Deduplication
# ---------------------------------------------------------------------------

def test_restart_before_polling_creates_new_order(tmp_path: Path):
    """Restart khi job thất bại trước polling (ví dụ: upload) phải tạo order mới."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    imgs = _create_dummy_images(src, 2)

    store = JobStore(tmp_path / "checkpoints")
    run_id = "run_restart_pre_poll"
    job_id = "job_001"

    outputs = [OutputSpec(output_id=p.name, input_files=[p]) for p in imgs]
    limits = JobLimits(max_outputs_per_job=20, max_jobs_per_batch=3)
    plan = plan_jobs(
        engine="autoenhance",
        outputs=outputs,
        mode="single",
        limits=limits,
        preferences={"api_key": "dummy_key"},
        output_dir=dst,
        run_id=run_id,
    )
    job_id = plan.jobs[0].job_id
    store.init_run(plan)

    # Simulate prior attempt failed at upload
    store.add_job_attempt(
        run_id,
        job_id,
        JobAttempt(
            attempt_no=1,
            started_at=0,
            finished_at=1,
            step="upload",
            status="failed",
            error_message="S3 timeout",
            server_resources={"order_id": "old_dead_order"},
        ),
    )

    created_orders = []

    def fake_create_order(*args, **kwargs):
        new_oid = f"new_order_{len(created_orders) + 1}"
        created_orders.append(new_oid)
        return {"order_id": new_oid}

    poll_det = {
        "images": [
            {"id": "img_id_000", "filename": "img_000.jpg", "status": "enhanced"},
            {"id": "img_id_001", "filename": "img_001.jpg", "status": "enhanced"},
        ]
    }
    poll_success = [
        {"id": "img_id_000", "filename": "img_000.jpg", "status": "enhanced"},
        {"id": "img_id_001", "filename": "img_001.jpg", "status": "enhanced"},
    ]

    def fake_dl_and_process(*, image_id, original_filename, output_dir, **kwargs):
        clean_stem = Path(original_filename).stem.strip() or image_id[:8]
        out_f = output_dir / f"{clean_stem}.jpg"
        im = Image.new("RGB", (10, 10), color=(255, 255, 255))
        im.save(out_f, "JPEG")
        return True, out_f, None

    with patch("core.shared.licensing.check", return_value=LicenseResult(valid=True, engine="autoenhance", level="plus")), \
         patch("core.autoenhance.workflow.create_order", side_effect=fake_create_order), \
         patch("core.autoenhance.workflow.get_upload_s3_info", return_value={"url": "https://s3", "headers": {}}), \
         patch("core.autoenhance.workflow._upload_one_file", return_value=True), \
         patch("core.autoenhance.workflow.trigger_process", return_value=True), \
         patch("core.autoenhance.workflow.poll_order_completion", return_value=(poll_det, poll_success, [])), \
         patch("core.autoenhance.workflow.download_and_process_image", side_effect=fake_dl_and_process), \
         patch("core.autoenhance.workflow.save_order_metadata"):

        res = restart_workflow_job(
            run_id=run_id,
            job_id=job_id,
            store=store,
        )

        assert res.status == "success"
        # Must have called create_order to create a fresh order
        assert len(created_orders) == 1
        assert created_orders[0] != "old_dead_order"


def test_restart_at_polling_reuses_existing_order(tmp_path: Path):
    """Restart khi job thất bại tại polling phải tái sử dụng order cũ, không upload lại."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    imgs = _create_dummy_images(src, 2)

    store = JobStore(tmp_path / "checkpoints")
    run_id = "run_restart_polling"
    job_id = "job_001"

    outputs = [OutputSpec(output_id=p.name, input_files=[p]) for p in imgs]
    limits = JobLimits(max_outputs_per_job=20, max_jobs_per_batch=3)
    plan = plan_jobs(
        engine="autoenhance",
        outputs=outputs,
        mode="single",
        limits=limits,
        preferences={"api_key": "dummy_key"},
        output_dir=dst,
        run_id=run_id,
    )
    job_id = plan.jobs[0].job_id
    store.init_run(plan)

    store.add_job_attempt(
        run_id,
        job_id,
        JobAttempt(
            attempt_no=1,
            started_at=0,
            finished_at=1,
            step="polling",
            status="failed",
            error_message="Network lost during polling",
            server_resources={"order_id": "ord_reuse_polling"},
        ),
    )

    poll_det = {
        "images": [
            {"id": "img_id_000", "filename": "img_000.jpg", "status": "enhanced"},
            {"id": "img_id_001", "filename": "img_001.jpg", "status": "enhanced"},
        ]
    }
    poll_success = [
        {"id": "img_id_000", "filename": "img_000.jpg", "status": "enhanced"},
        {"id": "img_id_001", "filename": "img_001.jpg", "status": "enhanced"},
    ]

    def fake_dl_and_process(*, image_id, original_filename, output_dir, **kwargs):
        clean_stem = Path(original_filename).stem.strip() or image_id[:8]
        out_f = output_dir / f"{clean_stem}.jpg"
        im = Image.new("RGB", (10, 10), color=(255, 255, 255))
        im.save(out_f, "JPEG")
        return True, out_f, None

    with patch("core.shared.licensing.check", return_value=LicenseResult(valid=True, engine="autoenhance", level="plus")), \
         patch("core.autoenhance.workflow.create_order") as mock_create, \
         patch("core.autoenhance.workflow._upload_one_file") as mock_upload, \
         patch("core.autoenhance.workflow.trigger_process") as mock_trigger, \
         patch("core.autoenhance.workflow.poll_order_completion", return_value=(poll_det, poll_success, [])), \
         patch("core.autoenhance.workflow.download_and_process_image", side_effect=fake_dl_and_process):

        res = restart_workflow_job(
            run_id=run_id,
            job_id=job_id,
            store=store,
        )

        assert res.status == "success"
        # Must NOT create order again
        mock_create.assert_not_called()
        # Must NOT upload files again
        mock_upload.assert_not_called()
        # Must NOT trigger process again
        mock_trigger.assert_not_called()


def test_restart_at_download_does_not_overwrite_existing_files(tmp_path: Path):
    """Restart tại download: file đã tải hoàn tất từ trước được giữ nguyên, không tải đè."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    imgs = _create_dummy_images(src, 2)

    store = JobStore(tmp_path / "checkpoints")
    run_id = "run_restart_dl"
    job_id = "job_001"

    outputs = [OutputSpec(output_id=p.name, input_files=[p]) for p in imgs]
    limits = JobLimits(max_outputs_per_job=20, max_jobs_per_batch=3)
    plan = plan_jobs(
        engine="autoenhance",
        outputs=outputs,
        mode="single",
        limits=limits,
        preferences={"api_key": "dummy_key"},
        output_dir=dst,
        run_id=run_id,
    )
    job_id = plan.jobs[0].job_id
    store.init_run(plan)

    store.add_job_attempt(
        run_id,
        job_id,
        JobAttempt(
            attempt_no=1,
            started_at=0,
            finished_at=1,
            step="download",
            status="failed",
            error_message="Aborted during download",
            server_resources={"order_id": "ord_dl_reuse", "downloaded_files": ["img_id_000"]},
        ),
    )

    # Pre-create img_000.jpg in job's output directory with valid JPEG image
    job_out_dir = dst / job_id
    job_out_dir.mkdir(parents=True, exist_ok=True)
    existing_file = job_out_dir / "img_000.jpg"
    im_existing = Image.new("RGB", (10, 10), color=(100, 100, 100))
    im_existing.save(existing_file, "JPEG")
    original_bytes = existing_file.read_bytes()

    downloaded_calls = []

    def fake_dl_and_process(*, image_id, original_filename, output_dir, **kwargs):
        downloaded_calls.append(image_id)
        clean_stem = Path(original_filename).stem.strip() or image_id[:8]
        out_f = output_dir / f"{clean_stem}.jpg"
        im = Image.new("RGB", (10, 10), color=(200, 200, 200))
        im.save(out_f, "JPEG")
        return True, out_f, None

    poll_response = {
        "images": [
            {"id": "img_id_000", "filename": "img_000.jpg", "status": "enhanced"},
            {"id": "img_id_001", "filename": "img_001.jpg", "status": "enhanced"},
        ]
    }

    with patch("core.shared.licensing.check", return_value=LicenseResult(valid=True, engine="autoenhance", level="plus")), \
         patch("core.autoenhance.workflow.create_order") as mock_create, \
         patch("core.autoenhance.workflow._api_get") as mock_api_get, \
         patch("core.autoenhance.workflow.download_and_process_image", side_effect=fake_dl_and_process):

        mock_api_get.return_value = MagicMock(status_code=200, json=lambda: poll_response)

        res = restart_workflow_job(
            run_id=run_id,
            job_id=job_id,
            store=store,
        )

        assert res.status == "success"
        mock_create.assert_not_called()
        # img_000 was already verified in checkpoint & disk, so download should only be called for img_id_001!
        assert "img_id_000" not in downloaded_calls
        assert "img_id_001" in downloaded_calls
        # Existing file content was preserved
        assert existing_file.read_bytes() == original_bytes


# ---------------------------------------------------------------------------
# 3. Lỗi xác thực khi restart: latest_step và server_resources bảo toàn
# ---------------------------------------------------------------------------

def test_restart_activation_failure_preserves_latest_step_and_server_resources(tmp_path: Path):
    """Khi xác thực thất bại lúc restart, latest_step và server_resources trong checkpoint không bị ghi đè."""
    store = JobStore(tmp_path / "checkpoints")
    run_id = "run_lic_fail"
    job_id = "job_001"

    limits = JobLimits(max_outputs_per_job=20, max_jobs_per_batch=3)
    plan = plan_jobs(
        engine="autoenhance",
        outputs=[OutputSpec(output_id="out_0", input_files=[tmp_path / "a.jpg"])],
        mode="single",
        limits=limits,
        preferences={"api_key": "dummy_key"},
        output_dir=tmp_path / "dst",
        run_id=run_id,
    )
    job_id = plan.jobs[0].job_id
    store.init_run(plan)
    store.add_job_attempt(
        run_id,
        job_id,
        JobAttempt(
            attempt_no=1,
            started_at=0,
            finished_at=1,
            step="polling",
            status="failed",
            error_message="Network failure",
            server_resources={"order_id": "ord_valuable_data", "extra_token": "secret_abc"},
        ),
    )

    # Verify initial checkpoint
    rec_before = store.load_job_record(run_id, job_id)
    assert rec_before.latest_step == "polling"
    assert rec_before.server_resources["order_id"] == "ord_valuable_data"

    # Now attempt restart with invalid license
    with patch("core.shared.licensing.check", return_value=LicenseResult(valid=False, engine="autoenhance", message="Key hết hạn")):
        res = restart_workflow_job(
            run_id=run_id,
            job_id=job_id,
            store=store,
        )
        assert res.status == "failed"
        assert res.step == "activation"

    # Reload record from disk and verify preservation
    rec_after = store.load_job_record(run_id, job_id)
    assert rec_after.latest_step == "polling"  # MUST NOT be overwritten to 'activation'!
    assert rec_after.server_resources["order_id"] == "ord_valuable_data"
    assert rec_after.server_resources["extra_token"] == "secret_abc"
    assert rec_after.status == "failed"


def test_autoenhance_job_success_when_outputs_exceed_spec(tmp_path: Path):
    """Kiểm tra khi Autoenhance tự gom bracket tạo thêm ảnh HDR (ví dụ: 6 inputs -> 8 outputs),
    toàn bộ output đều thành công thì job status phải là 'success' (không phải 'partial')."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    dst.mkdir(parents=True, exist_ok=True)
    files = _create_dummy_images(src, 6)

    outputs = [OutputSpec(output_id=f.stem, input_files=[f]) for f in files]
    spec = JobSpec(
        job_id="job_001",
        run_id="run_test_extra",
        job_index=0,
        job_total=1,
        engine="autoenhance",
        outputs=outputs,
        preferences={"api_key": "test_key"},
        output_dir=dst,
    )
    store = JobStore(tmp_path / "jobs")
    plan = JobPlan(
        run_id="run_test_extra",
        engine="autoenhance",
        mode="single",
        jobs=[spec],
        limits=JobLimits(max_outputs_per_job=20, max_jobs_per_batch=1),
        total_outputs=6,
    )
    store.init_run(plan)

    from core.shared.jobs import JobContext
    context = JobContext(
        run_id="run_test_extra",
        job_id="job_001",
        attempt_no=1,
        engine="autoenhance",
        store=store,
        credentials={"api_key": "test_key"},
    )

    with patch("core.autoenhance.workflow.create_order", return_value={"order_id": "ord_123"}), \
         patch("core.autoenhance.workflow.get_upload_s3_info", return_value={"s3PutObjectUrl": "http://s3.fake/put"}), \
         patch("core.autoenhance.workflow._upload_one_file", return_value=True), \
         patch("core.autoenhance.workflow.trigger_process", return_value=True), \
         patch("core.autoenhance.workflow.poll_order_completion") as mock_poll, \
         patch("core.autoenhance.workflow.download_and_process_image") as mock_dl:

        mock_images = [
            {"image_id": f"id_{i}", "image_name": f"img_{i:03d}.jpg", "status": "processed"}
            for i in range(6)
        ] + [
            {"image_id": "id_merge_1", "image_name": "img_002_merge.jpg", "status": "processed"},
            {"image_id": "id_merge_2", "image_name": "img_005_merge.jpg", "status": "processed"},
        ]
        mock_poll.return_value = ({"images": mock_images}, mock_images, [])
        mock_dl.side_effect = lambda image_id, original_filename, output_dir, **kwargs: (
            True, Path(output_dir) / original_filename, None
        )

        res = autoenhance_executor(spec, context)
        assert res.status == "success"
        assert res.step == "export"
