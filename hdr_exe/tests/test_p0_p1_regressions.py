"""Comprehensive Regression Tests for P0/P1 Fixes in hdr_exe.

Verifies:
1. Output ID collision prevention (a.jpg, a.png, a_2.jpg -> unique output IDs).
2. Credentials isolation: api_key and tokens are never written to checkpoint run.json.
3. Restart recovery: polling failure -> activation failure -> successful restart reuses order_id.
4. Download safety: never unlinks existing user files in output directory.
5. Pillow verification: corrupt files on disk are not reused, but cleanly re-downloaded.
6. Licensing enforcement: batch_download checks licensing both with and without tracker.
7. Strict configuration: invalid types or non-HTTPS licensing URLs raise ConfigurationError without network calls.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image
import pytest

from core.autoenhance.config import load_autoenhance_config
from core.autoenhance.download import batch_download, download_and_process_image
from core.autoenhance.workflow import (
    autoenhance_executor,
    build_autoenhance_outputs,
    restart_workflow_job,
    run_workflow,
)
from core.shared.config import ConfigurationError, load_app_config
from core.shared.events import StepTracker
from core.shared.jobs.models import (
    JobAttempt,
    JobLimits,
    JobResult,
    JobSpec,
    OutputSpec,
)
from core.shared.jobs.planner import plan_jobs
from core.shared.jobs.runner import JobContext, restart_job
from core.shared.jobs.store import JobStore
from core.shared.licensing.models import LicenseResult


def test_id_collision_prevention(tmp_path: Path):
    """Kiểm tra chống trùng lặp ID khi các file có tên stem giống nhau hoặc trùng với counter."""
    f1 = tmp_path / "a.jpg"
    f2 = tmp_path / "a.png"
    f3 = tmp_path / "a_2.jpg"
    f1.write_bytes(b"1")
    f2.write_bytes(b"2")
    f3.write_bytes(b"3")

    outputs = build_autoenhance_outputs([f1, f2, f3])
    out_ids = [o.output_id for o in outputs]

    assert len(out_ids) == 3
    assert len(set(out_ids)) == 3, f"Output IDs must be unique, got: {out_ids}"
    assert out_ids == ["a", "a_2", "a_2_2"]


def test_credentials_not_saved_in_checkpoint(tmp_path: Path, monkeypatch):
    """Kiểm tra api_key và token không bao giờ bị ghi vào file checkpoint ~/.hdr_exe/jobs/{run_id}.json."""
    monkeypatch.setenv("HDR_EXE_HOME", str(tmp_path))
    in_dir = tmp_path / "inputs"
    in_dir.mkdir(parents=True)
    img_f = in_dir / "photo.jpg"
    img_f.write_bytes(b"dummy")

    out_dir = tmp_path / "outputs"
    out_dir.mkdir(parents=True)

    secret_key = "SK_SECRET_KEY_NEVER_LEAK_12345"

    with patch("core.shared.licensing.check", return_value=LicenseResult(valid=True, engine="autoenhance", level="plus")), \
         patch("core.autoenhance.workflow.create_order", return_value={"order_id": "ord_1"}), \
         patch("core.autoenhance.workflow.get_upload_s3_info", return_value={"url": "https://s3", "headers": {}}), \
         patch("core.autoenhance.workflow._upload_one_file", return_value=True), \
         patch("core.autoenhance.workflow.trigger_process", return_value=True), \
         patch("core.autoenhance.workflow.poll_order_completion", return_value=({"images": []}, [], [])), \
         patch("core.autoenhance.workflow.save_order_metadata"):

        res = run_workflow(
            input_dir=in_dir,
            output_dir=out_dir,
            mode="single",
            api_key=secret_key,
            run_id="run_leak_test",
        )

    store = JobStore(tmp_path / "jobs")
    checkpoint_file = store.get_run_path("run_leak_test")
    assert checkpoint_file.is_file()

    raw_checkpoint_text = checkpoint_file.read_text(encoding="utf-8")
    assert secret_key not in raw_checkpoint_text, "API key bị lộ trong file checkpoint!"

    # Kiểm tra cả khi đọc qua load_job_record
    run_doc = store.load_run("run_leak_test")
    job_id = list(run_doc["jobs"].keys())[0]
    record = store.load_job_record("run_leak_test", job_id)
    assert record is not None
    assert "api_key" not in record.preferences


def test_polling_fail_then_activation_fail_then_restart_reuses_order(tmp_path: Path):
    """Kiểm tra kịch bản chuỗi: Polling lỗi -> Activation lỗi -> Restart thành công không tạo lại order mới."""
    store = JobStore(tmp_path / "jobs")
    run_id = "run_chain_test"
    job_id = "job_001"
    out_dir = tmp_path / "outputs" / "job_001"
    out_dir.mkdir(parents=True)

    spec = JobSpec(
        job_id=job_id,
        run_id=run_id,
        job_index=0,
        job_total=1,
        engine="autoenhance",
        outputs=[OutputSpec(output_id="p1", input_files=[tmp_path / "p1.jpg"])],
        preferences={},
        output_dir=out_dir,
    )
    plan = plan_jobs(
        engine="autoenhance",
        outputs=spec.outputs,
        mode="single",
        limits=JobLimits(20, 3),
        preferences={},
        output_dir=tmp_path / "outputs",
        run_id=run_id,
        job_ids=[job_id],
    )
    store.init_run(plan)

    # Attempt 1: thất bại ở polling, đã có order_id
    store.add_job_attempt(
        run_id,
        job_id,
        JobAttempt(
            attempt_no=1,
            status="failed",
            step="polling",
            started_at=10.0,
            finished_at=20.0,
            error_message="Network dropped during polling",
            server_resources={"order_id": "ord_preserved_999"},
        ),
    )

    # Attempt 2: người dùng bấm restart nhưng key hết hạn / server bản quyền lỗi -> fail tại activation
    with patch("core.shared.licensing.check", return_value=LicenseResult(valid=False, engine="autoenhance", message="Key expired")):
        res2 = restart_job(
            run_id=run_id,
            job_id=job_id,
            execute_job=autoenhance_executor,
            store=store,
        )
    assert res2.status == "failed"
    assert res2.step == "activation"

    # Kiểm tra record sau attempt 2: latest_step không bị đè về activation
    rec_after_2 = store.load_job_record(run_id, job_id)
    assert rec_after_2.latest_step == "polling"
    assert rec_after_2.server_resources.get("order_id") == "ord_preserved_999"

    # Attempt 3: người dùng gia hạn key thành công -> restart lần nữa
    # Phải tự động tiếp tục từ polling với ord_preserved_999, KHÔNG gọi create_order
    mock_create_order = MagicMock()
    poll_det = {"images": [{"id": "img_p1", "filename": "p1.jpg", "status": "enhanced"}]}
    poll_success = [{"id": "img_p1", "filename": "p1.jpg", "status": "enhanced"}]

    def fake_dl(*, image_id, original_filename, output_dir, **kwargs):
        dest = output_dir / f"{Path(original_filename).stem}.jpg"
        im = Image.new("RGB", (10, 10))
        im.save(dest, "JPEG")
        return True, dest, None

    with patch("core.shared.licensing.check", return_value=LicenseResult(valid=True, engine="autoenhance", level="plus")), \
         patch("core.autoenhance.workflow.create_order", mock_create_order), \
         patch("core.autoenhance.workflow.poll_order_completion", return_value=(poll_det, poll_success, [])), \
         patch("core.autoenhance.workflow.download_and_process_image", side_effect=fake_dl):

        res3 = restart_job(
            run_id=run_id,
            job_id=job_id,
            execute_job=autoenhance_executor,
            credentials={"api_key": "valid_api_key"},
            store=store,
        )

    assert res3.status == "success"
    # TUYỆT ĐỐI không gọi create_order
    mock_create_order.assert_not_called()
    assert res3.server_resources.get("order_id") == "ord_preserved_999"

    # Kiểm tra trạng thái tổng run được cập nhật thành success
    run_doc = store.load_run(run_id)
    assert run_doc["status"] == "success"


def test_download_never_unlinks_existing_user_files(tmp_path: Path):
    """Kiểm tra hàm download tuyệt đối không xóa file có sẵn của user kể cả khi lỗi mạng."""
    output_dir = tmp_path / "user_photos"
    output_dir.mkdir(parents=True)

    # File có sẵn của người dùng
    user_file = output_dir / "living_room.jpg"
    user_content = b"PRECIOUS_USER_PHOTO_BYTES_DO_NOT_DELETE"
    user_file.write_bytes(user_content)

    temp_dir = tmp_path / "temp_workspace"

    # Giả lập tải file nhị phân lỗi mạng
    with patch("core.autoenhance.download._download_one_file", return_value=False):
        ok, final_path, err = download_and_process_image(
            image_id="img_new_123",
            original_filename="living_room.jpg",
            output_dir=output_dir,
            api_key="dummy_key",
            temp_dir=temp_dir,
        )

    assert ok is False
    # File người dùng tuyệt đối còn nguyên vẹn
    assert user_file.is_file()
    assert user_file.read_bytes() == user_content


def test_corrupt_file_not_reused_on_restart(tmp_path: Path):
    """Kiểm tra file lỗi/hỏng trên ổ đĩa không được tái sử dụng khi restart mà được tải lại."""
    out_dir = tmp_path / "job_001"
    out_dir.mkdir(parents=True)

    corrupt_file = out_dir / "img_001.jpg"
    corrupt_file.write_bytes(b"CORRUPTED_TRUNCATED_NOT_A_JPEG_FILE")

    store = JobStore(tmp_path / "jobs")
    run_id = "run_corrupt_test"
    job_id = "job_001"

    spec = JobSpec(
        job_id=job_id,
        run_id=run_id,
        job_index=0,
        job_total=1,
        engine="autoenhance",
        outputs=[OutputSpec(output_id="img_001", input_files=[tmp_path / "in.jpg"])],
        preferences={},
        output_dir=out_dir,
    )
    plan = plan_jobs(
        engine="autoenhance",
        outputs=spec.outputs,
        mode="single",
        limits=JobLimits(20, 3),
        preferences={},
        output_dir=tmp_path / "outputs",
        run_id=run_id,
        job_ids=[job_id],
    )
    store.init_run(plan)
    store.add_job_attempt(
        run_id,
        job_id,
        JobAttempt(
            attempt_no=1,
            status="failed",
            step="download",
            started_at=10.0,
            error_message="Partial fail",
            server_resources={"order_id": "ord_dl_1", "downloaded_files": ["img_id_001"]},
        ),
    )

    downloaded_calls = []

    def fake_dl(*, image_id, original_filename, output_dir, **kwargs):
        downloaded_calls.append(image_id)
        out_f = output_dir / "img_001.jpg"
        im = Image.new("RGB", (10, 10), color=(50, 50, 50))
        im.save(out_f, "JPEG")
        return True, out_f, None

    poll_response = {
        "images": [{"id": "img_id_001", "filename": "img_001.jpg", "status": "enhanced"}]
    }

    with patch("core.shared.licensing.check", return_value=LicenseResult(valid=True, engine="autoenhance", level="plus")), \
         patch("core.autoenhance.workflow._api_get", return_value=MagicMock(status_code=200, json=lambda: poll_response)), \
         patch("core.autoenhance.workflow.download_and_process_image", side_effect=fake_dl):

        res = restart_workflow_job(
            run_id=run_id,
            job_id=job_id,
            api_key="test_key",
            store=store,
        )

    assert res.status == "success"
    # Vì file cũ bị hỏng không vượt qua Pillow verify -> phải tải lại!
    assert "img_id_001" in downloaded_calls


def test_batch_download_enforces_licensing(tmp_path: Path):
    """Kiểm tra batch_download luôn bắt buộc kiểm tra bản quyền dù có tracker hay không."""
    out_dir = tmp_path / "downloads"
    out_dir.mkdir(parents=True)

    # 1. Không có tracker
    with patch("core.shared.licensing.check", return_value=LicenseResult(valid=False, engine="autoenhance", message="No license")):
        count = batch_download(
            api_key="test_key",
            order_ids=["ord_1"],
            savedir=out_dir,
            tracker=None,
        )
    assert count == 0

    # 2. Có tracker được truyền vào
    events = []
    tracker = StepTracker(
        engine="Autoenhance.Download",
        steps=[
            ("activation", "Activation"),
            ("auth", "Auth"),
            ("order_details", "OrderDetails"),
            ("download", "Download"),
        ],
        run_id="run_test_tr",
        event_fn=events.append,
    )
    with patch("core.shared.licensing.check", return_value=LicenseResult(valid=False, engine="autoenhance", message="No license")):
        count_tr = batch_download(
            api_key="test_key",
            order_ids=["ord_1"],
            savedir=out_dir,
            tracker=tracker,
        )
    assert count_tr == 0
    assert any(e.step == "activation" and e.status == "failed" for e in events)


def test_strict_config_validation(tmp_path: Path, monkeypatch):
    """Kiểm tra cấu hình nghiêm ngặt: sai schema hoặc dùng URL HTTP cho licensing phải báo lỗi và không gửi request mạng."""
    monkeypatch.setenv("HDR_EXE_HOME", str(tmp_path))

    # 1. max_outputs_per_job không phải số nguyên
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "autoenhance": {
            "jobs": {
                "max_outputs_per_job": "not_an_int"
            }
        }
    }), encoding="utf-8")

    with pytest.raises(ConfigurationError) as excinfo:
        load_autoenhance_config()
    assert "max_outputs_per_job" in str(excinfo.value)

    # 2. server_url licensing không phải HTTPS
    cfg_file.write_text(json.dumps({
        "licensing": {
            "server_url": "http://insecure-licensing.example.com"
        }
    }), encoding="utf-8")

    with pytest.raises(ConfigurationError) as excinfo_lic:
        load_app_config()
    assert "HTTPS" in str(excinfo_lic.value)


def test_manifest_written_on_early_job_failure_or_cancellation(tmp_path: Path):
    """Kiểm tra file manifest luôn được ghi nhận đầy đủ kể cả khi job thất bại sớm ở bước prepare/auth."""
    out_dir = tmp_path / "outputs" / "job_001"
    out_dir.mkdir(parents=True)
    in_dir = tmp_path / "inputs"
    in_dir.mkdir(parents=True)

    spec = JobSpec(
        job_id="job_001",
        run_id="run_early_fail",
        job_index=0,
        job_total=1,
        engine="autoenhance",
        outputs=[OutputSpec(output_id="p1", input_files=[in_dir / "p1.jpg"])],
        preferences={},
        output_dir=out_dir,
    )
    store = JobStore(tmp_path / "jobs")
    context = JobContext(
        run_id="run_early_fail",
        job_id="job_001",
        attempt_no=1,
        engine="autoenhance",
        store=store,
        credentials={"api_key": "test_key"},
    )

    res = autoenhance_executor(spec, context)
    assert res.status == "failed"
    assert res.step == "prepare"

    manifest_file = out_dir / "manifest_run_early_fail_job_001_att1.json"
    assert manifest_file.is_file(), "File manifest phải được ghi dù job thất bại sớm ở bước prepare!"
    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert manifest_data["run_id"] == "run_early_fail"
    assert manifest_data["job_id"] == "job_001"
    assert manifest_data["attempt_no"] == 1
    assert manifest_data["status"] == "failed"
    assert manifest_data["step"] == "prepare"
    assert manifest_data["outputs_total"] == 1
    assert manifest_data["outputs_succeeded"] == 0
    assert manifest_data["error_message"] is not None


def test_uncaught_exception_in_executor_emits_terminal_step_event(tmp_path: Path):
    """Kiểm tra khi executor gặp exception chưa bắt thì runner phải bắn terminal StepEvent(status='failed')."""
    store = JobStore(tmp_path / "jobs")
    run_id = "run_crash"
    job_id = "job_001"

    spec = JobSpec(
        job_id=job_id,
        run_id=run_id,
        job_index=0,
        job_total=1,
        engine="autoenhance",
        outputs=[OutputSpec(output_id="p1", input_files=[tmp_path / "p1.jpg"])],
        preferences={},
        output_dir=tmp_path / "out",
    )
    plan = plan_jobs(
        engine="autoenhance",
        outputs=spec.outputs,
        mode="single",
        limits=JobLimits(20, 3),
        preferences={},
        output_dir=tmp_path / "out",
        run_id=run_id,
    )

    events = []
    def crashing_executor(s, ctx):
        raise RuntimeError("Unexpected crash inside executor!")

    with patch("core.shared.jobs.runner.require_access", return_value=LicenseResult(valid=True, engine="autoenhance", level="plus")):
        from core.shared.jobs.runner import run_jobs
        batch_res = run_jobs(
            plan=plan,
            execute_job=crashing_executor,
            store=store,
            event_fn=events.append,
        )

    assert batch_res.status == "failed"
    failed_events = [e for e in events if e.status == "failed"]
    assert len(failed_events) > 0
    assert any("Ngoại lệ chưa bắt trong executor" in e.message for e in failed_events)


def test_prepare_fails_immediately_on_unconvertible_image(tmp_path: Path):
    """Kiểm tra khi có ảnh không thể chuyển đổi định dạng ở prepare thì job phải dừng ngay (failed)."""
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True)
    in_dir = tmp_path / "in"
    in_dir.mkdir(parents=True)

    bad_file = in_dir / "corrupt.raw"
    bad_file.write_bytes(b"NOT_A_VALID_RAW_FILE")

    spec = JobSpec(
        job_id="job_bad_prep",
        run_id="run_prep_fail",
        job_index=0,
        job_total=1,
        engine="autoenhance",
        outputs=[OutputSpec(output_id="bad", input_files=[bad_file])],
        preferences={},
        output_dir=out_dir,
    )
    store = JobStore(tmp_path / "jobs")
    context = JobContext(
        run_id="run_prep_fail",
        job_id="job_bad_prep",
        attempt_no=1,
        engine="autoenhance",
        store=store,
        credentials={"api_key": "test_key"},
    )

    with patch("core.autoenhance.upload.convert_to_jpg", return_value=False):
        res = autoenhance_executor(spec, context)

    assert res.status == "failed"
    assert res.step == "prepare"
    assert "Lỗi chuẩn bị ảnh" in (res.error_message or "")
