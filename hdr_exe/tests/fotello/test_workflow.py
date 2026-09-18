"""Tests for Fotello End-to-End Workflow and Step-Aware Manual Restarts."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.fotello.config import FotelloConfig
from core.fotello.models import RenditionInfo
from core.fotello.workflow import fotello_executor, restart_workflow_job, run_workflow
from core.shared.jobs import JobContext, JobStore
from core.shared.jobs.models import JobAttempt, JobLimits, JobResult, JobSpec, OutputSpec
from PIL import Image


def _make_dummy_image_files(folder: Path, count: int) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    for i in range(count):
        p = folder / f"img_{i + 1:03d}.jpg"
        p.write_bytes(f"dummy content {i}".encode("utf-8"))
        files.append(p)
    return files


@pytest.fixture
def mock_auth_valid(monkeypatch):
    monkeypatch.setattr(
        "core.fotello.auth.check_auth_session",
        lambda: ("valid", "OK", {
            "access_token": "mock_access_tok",
            "id_token": "mock_id_tok",
            "team_id": "team_123",
        }),
    )


def test_workflow_end_to_end_single_mode(tmp_path: Path, mock_auth_valid, monkeypatch):
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    _make_dummy_image_files(input_dir, 3)  # 3 images, bracket_size 3 -> 1 output

    # Mock upload
    monkeypatch.setattr(
        "core.fotello.workflow.upload_job_inputs",
        lambda outputs, **kwargs: ({outputs[0].output_id: ["up_1", "up_2", "up_3"]}, []),
    )

    # Mock resource creation
    monkeypatch.setattr(
        "core.fotello.workflow.create_job_resources",
        lambda listing_name, outputs, **kwargs: ("listing_mock_1", {outputs[0].output_id: "enh_mock_1"}),
    )

    # Mock polling
    monkeypatch.setattr(
        "core.fotello.workflow.poll_enhances_completion",
        lambda enhance_ids, **kwargs: (set(enhance_ids), set(), set()),
    )

    # Mock listing fetch
    monkeypatch.setattr(
        "core.fotello.listings.list_enhances",
        lambda listing_id, **kwargs: [{
            "enhance_id": "enh_mock_1",
            "has_image": True,
            "rendition": "edited_upsized",
            "image_uri": "gs://mock/output.jpg",
        }],
    )

    # Mock stream download
    def mock_download_stream(uri, access_token, dest_temp_path, **kwargs):
        dest_temp_path.write_bytes(b"downloaded image bytes")
        return 1024, "abc123sha", (6000, 4000), "jpg"

    monkeypatch.setattr("core.fotello.client.stream_download_image", mock_download_stream)

    batch_result = run_workflow(
        input_dir=input_dir,
        output_dir=output_dir,
        mode="single",
        bracket_size=3,
        store=JobStore(jobs_dir=tmp_path / "jobs"),
    )

    assert batch_result.status == "success"
    assert batch_result.total_jobs == 1
    assert batch_result.succeeded_jobs == 1
    assert batch_result.succeeded_outputs == 1

    # Verify manifest written
    manifests = list(output_dir.glob("**/manifest_*.json"))
    assert len(manifests) == 1
    m_data = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert m_data["outputs_succeeded"] == 1
    assert m_data["listing_id"] == "listing_mock_1"


def test_step_aware_restart_polling(tmp_path: Path, mock_auth_valid, monkeypatch):
    """Kiểm tra restart khi lỗi xảy ra tại polling: Bỏ qua upload/listing, resume polling."""
    jobs_dir = tmp_path / "jobs"
    store = JobStore(jobs_dir=jobs_dir)
    input_dir = tmp_path / "inputs"
    _make_dummy_image_files(input_dir, 1)

    spec = JobSpec(
        job_id="job_001",
        run_id="run_test",
        job_index=0,
        job_total=1,
        engine="fotello",
        outputs=[OutputSpec(output_id="out_1", input_files=[input_dir / "img_001.jpg"])],
        preferences={"bracket_size": 1},
        output_dir=tmp_path / "outputs" / "job_001",
    )

    # Setup context as if attempt 1 failed at polling
    context = JobContext(
        run_id="run_test",
        job_id="job_001",
        attempt_no=2,
        engine="fotello",
        store=store,
        latest_step="polling",
        server_resources={"listing_id": "listing_saved_123", "enhance_map": {"out_1": "enh_saved_456"}},
        previous_attempt=JobAttempt(
            attempt_no=1,
            status="failed",
            step="polling",
            started_at=100.0,
            error_message="Polling timeout",
            server_resources={"listing_id": "listing_saved_123", "enhance_map": {"out_1": "enh_saved_456"}},
        ),
    )

    upload_called = False
    def spy_upload(*args, **kwargs):
        nonlocal upload_called
        upload_called = True
        return {}, []
    monkeypatch.setattr("core.fotello.workflow.upload_job_inputs", spy_upload)

    # Mock polling succeeds now
    monkeypatch.setattr(
        "core.fotello.workflow.poll_enhances_completion",
        lambda enhance_ids, **kwargs: (set(enhance_ids), set(), set()),
    )
    monkeypatch.setattr(
        "core.fotello.listings.list_enhances",
        lambda listing_id, **kwargs: [{
            "enhance_id": "enh_saved_456",
            "has_image": True,
            "rendition": "edited_upsized",
            "image_uri": "gs://mock/output.jpg",
        }],
    )
    def mock_stream_restart(uri, access_token, dest_temp_path, **kwargs):
        dest_temp_path.write_bytes(b"restart downloaded image bytes")
        return 2048, "restartsha", (6000, 4000), "jpg"

    monkeypatch.setattr("core.fotello.client.stream_download_image", mock_stream_restart)

    result = fotello_executor(spec, context)

    # Upload and create listing must NOT have been called on polling restart
    assert not upload_called
    assert result.status == "success"
    assert result.attempt_no == 2
    assert result.server_resources["listing_id"] == "listing_saved_123"


def test_step_aware_restart_download(tmp_path: Path, mock_auth_valid, monkeypatch):
    """Kiểm tra restart khi lỗi xảy ra tại download: Bỏ qua upload, listing, polling, chỉ tải file chưa xong."""
    jobs_dir = tmp_path / "jobs"
    store = JobStore(jobs_dir=jobs_dir)
    input_dir = tmp_path / "inputs"
    _make_dummy_image_files(input_dir, 2)
    job_out_dir = tmp_path / "outputs" / "job_001"
    job_out_dir.mkdir(parents=True, exist_ok=True)

    # Giả sử out_1 đã được tải thành công từ trước
    already_downloaded_file = job_out_dir / "out_1.jpg"
    im = Image.new("RGB", (10, 10), color=(100, 100, 100))
    im.save(already_downloaded_file, "JPEG")

    spec = JobSpec(
        job_id="job_001",
        run_id="run_test",
        job_index=0,
        job_total=1,
        engine="fotello",
        outputs=[
            OutputSpec(output_id="out_1", input_files=[input_dir / "img_001.jpg"]),
            OutputSpec(output_id="out_2", input_files=[input_dir / "img_002.jpg"]),
        ],
        preferences={"bracket_size": 1},
        output_dir=job_out_dir,
    )

    context = JobContext(
        run_id="run_test",
        job_id="job_001",
        attempt_no=2,
        engine="fotello",
        store=store,
        latest_step="download",
        server_resources={
            "listing_id": "listing_saved_123",
            "enhance_map": {"out_1": "enh_1", "out_2": "enh_2"},
        },
        previous_attempt=JobAttempt(
            attempt_no=1,
            status="failed",
            step="download",
            started_at=100.0,
            error_message="Connection reset during out_2 download",
            server_resources={"listing_id": "listing_saved_123", "enhance_map": {"out_1": "enh_1", "out_2": "enh_2"}},
        ),
    )

    downloaded_uris = []
    def mock_stream(uri, access_token, dest_temp_path, **kwargs):
        downloaded_uris.append(uri)
        dest_temp_path.write_bytes(b"out 2 content")
        return 500, "sha2", (5000, 3000), "jpg"

    monkeypatch.setattr("core.fotello.client.stream_download_image", mock_stream)
    monkeypatch.setattr(
        "core.fotello.listings.list_enhances",
        lambda listing_id, **kwargs: [
            {"enhance_id": "enh_1", "has_image": True, "rendition": "edited_upsized", "image_uri": "gs://mock/out1.jpg"},
            {"enhance_id": "enh_2", "has_image": True, "rendition": "edited_upsized", "image_uri": "gs://mock/out2.jpg"},
        ],
    )

    result = fotello_executor(spec, context)

    assert result.status == "success"
    assert len(result.outputs_succeeded) == 2
    # Chỉ tải out_2, out_1 đã tồn tại và được giữ nguyên
    assert downloaded_uris == ["gs://mock/out2.jpg"]
    assert (job_out_dir / "out_1.jpg").read_bytes() == already_downloaded_file.read_bytes()
    assert (job_out_dir / "out_2.jpg").is_file()

