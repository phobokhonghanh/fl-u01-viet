"""Unit and integration tests for hdr_exe pywebview UI Bridge and App Factory."""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from core.shared.events import StepEvent
from core.shared.jobs.models import JobLimits, JobPlan, JobSpec, OutputSpec
from ui.app import create_app
from ui.bridge import BridgeApi


@pytest.fixture
def mock_window():
    win = MagicMock()
    win.evaluate_js = MagicMock()
    win.create_file_dialog = MagicMock()
    return win


@pytest.fixture
def bridge(mock_window, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HDR_EXE_DATA_DIR", str(tmp_path / ".hdr_exe"))
    b = BridgeApi(window=mock_window)
    return b


def test_bridge_init(bridge, mock_window):
    assert bridge._window is mock_window
    assert "autoenhance" in bridge._job_stores
    assert "fotello" in bridge._job_stores


def test_default_folders(bridge):
    folders = bridge.get_default_folders()
    assert "autoenhance" in folders
    assert "fotello" in folders
    assert "Autoenhance_Out" in folders["autoenhance"]
    assert "Fotello_Downloads" in folders["fotello"]


def test_app_config_persistence(bridge, tmp_path):
    cfg1 = bridge.get_app_config()
    assert "autoenhance_output_dir" in cfg1

    custom_dir = str(tmp_path / "MyCustomOut")
    save_res = bridge.save_app_config({"autoenhance_output_dir": custom_dir, "last_active_tab": "fotello"})
    assert save_res["success"] is True

    cfg2 = bridge.get_app_config()
    assert cfg2["autoenhance_output_dir"] == custom_dir
    assert cfg2["last_active_tab"] == "fotello"


def test_open_folder(bridge, tmp_path):
    target = tmp_path / "test_open_dir"
    with patch("subprocess.Popen") as mock_popen, patch("os.startfile", create=True) as mock_startfile:
        res = bridge.open_folder(str(target))
        assert res["success"] is True
        assert target.exists()


def test_select_folder_dialog(bridge, mock_window):
    mock_window.create_file_dialog.return_value = ["/path/to/selected"]
    result = bridge.select_folder("/initial")
    assert result == "/path/to/selected"

    mock_window.create_file_dialog.return_value = None
    assert bridge.select_folder() is None


def test_inspect_input_folder(bridge, tmp_path):
    # Empty dir returns error
    res_empty = bridge.inspect_input_folder("autoenhance", str(tmp_path))
    assert res_empty["valid"] is False

    # Create dummy images
    img1 = tmp_path / "photo_01.jpg"
    img2 = tmp_path / "photo_02.jpg"
    img3 = tmp_path / "photo_03.jpg"
    img1.write_bytes(b"\xff\xd8\xffdummy")
    img2.write_bytes(b"\xff\xd8\xffdummy")
    img3.write_bytes(b"\xff\xd8\xffdummy")

    res_ae = bridge.inspect_input_folder("autoenhance", str(tmp_path))
    assert res_ae["valid"] is True
    assert res_ae["inputs"] == 3
    assert res_ae["outputs"] == 3

    res_fo = bridge.inspect_input_folder("fotello", str(tmp_path), bracket_size=3)
    assert res_fo["valid"] is True
    assert res_fo["inputs"] == 3
    assert res_fo["outputs"] == 1


def test_licensing_lifecycle(bridge):
    # Initially unregistered
    status = bridge.get_license_status("autoenhance")
    assert "valid" in status
    assert "level" in status
    assert "machine_id" in status

    # Clear license
    clr = bridge.clear_license("autoenhance")
    assert clr["success"] is True

    # Activate mock
    with patch("ui.bridge.activate") as mock_act:
        from core.shared.licensing.models import LicenseResult
        mock_act.return_value = LicenseResult(engine="autoenhance", valid=True, level="plus", code=None, message="Activated")
        act_res = bridge.activate_license("autoenhance", "TEST-KEY-PLUS")
        assert act_res["valid"] is True
        assert act_res["level"] == "plus"


def test_auth_status_and_login_service(bridge):
    ae_auth = bridge.get_auth_status("autoenhance")
    assert "connected" in ae_auth
    assert ae_auth["engine"] == "autoenhance"

    fo_auth = bridge.get_auth_status("fotello")
    assert "connected" in fo_auth
    assert fo_auth["engine"] == "fotello"

    with patch("core.autoenhance.cdp.extract_and_save_api_key", return_value=(True, "Success")):
        res = bridge.login_service_cdp("autoenhance")
        assert res["success"] is True

    with patch("core.fotello.cdp.login", return_value={"status": "success", "message": "OK"}):
        res = bridge.login_service_cdp("fotello")
        assert res["success"] is True


def test_fetch_fotello_listings(bridge):
    mock_listings = [
        {"id": "lst_1", "name": "Listing 1", "created_at": "2026-09-11", "enhances_count": 10, "status": "completed"}
    ]
    with patch("core.fotello.listings.list_listings", return_value=mock_listings):
        res = bridge.fetch_fotello_listings()
        assert res["success"] is True
        assert res["total"] == 1
        assert res["listings"][0]["id"] == "lst_1"
        assert res["listings"][0]["outputsCount"] == 10


def test_run_jobs_and_manifest_retrieval(bridge, tmp_path):
    run_id = "test_run_123"
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    spec1 = JobSpec(
        job_id="job_001",
        run_id=run_id,
        job_index=1,
        job_total=2,
        engine="autoenhance",
        outputs=[OutputSpec(output_id="out_1", input_files=[tmp_path / "a.jpg"])],
        preferences={},
        output_dir=out_dir,
    )
    spec2 = JobSpec(
        job_id="job_002",
        run_id=run_id,
        job_index=2,
        job_total=2,
        engine="autoenhance",
        outputs=[OutputSpec(output_id="out_2", input_files=[tmp_path / "b.jpg"])],
        preferences={},
        output_dir=out_dir,
    )
    plan = JobPlan(
        run_id=run_id,
        engine="autoenhance",
        mode="batch",
        jobs=[spec1, spec2],
        limits=JobLimits(max_outputs_per_job=20, max_jobs_per_batch=10),
        total_outputs=2,
    )

    store = bridge._job_stores["autoenhance"]
    store.init_run(plan)

    # Test get_run_jobs
    jobs = bridge.get_run_jobs("autoenhance", run_id)
    assert len(jobs) == 2
    assert jobs[0]["job_id"] == "job_001"
    assert jobs[1]["job_id"] == "job_002"
    assert jobs[0]["status"] == "queued"
    assert len(jobs[0]["outputs"]) == 1
    assert jobs[0]["outputs"][0]["id"] == "out_1"
    assert jobs[0]["outputs"][0]["inputName"] == "a.jpg"
    assert jobs[0]["outputs"][0]["status"] == "queued"

    # Test get_job_manifest synthesis when not yet on disk
    manifest = bridge.get_job_manifest("autoenhance", run_id, "job_001")
    assert manifest is not None
    assert manifest["job_id"] == "job_001"
    assert manifest["outputs_total"] == 1

    # Test get_job_manifest when written to disk
    mnf_file = out_dir / f"manifest_{run_id}_job_001_att1.json"
    mnf_file.write_text(json.dumps({"job_id": "job_001", "disk_read": True}))
    loaded = bridge.get_job_manifest("autoenhance", run_id, "job_001", attempt_no=1)
    assert loaded.get("disk_read") is True


def test_outputs_formatting_with_partial_and_merged_outputs(bridge, tmp_path):
    run_id = "test_run_partial"
    out_dir = tmp_path / "out_p"
    out_dir.mkdir(parents=True, exist_ok=True)

    spec = JobSpec(
        job_id="job_001",
        run_id=run_id,
        job_index=0,
        job_total=1,
        engine="autoenhance",
        outputs=[
            OutputSpec(output_id="IMG_9942", input_files=[tmp_path / "IMG_9942.jpg"]),
            OutputSpec(output_id="IMG_9943", input_files=[tmp_path / "IMG_9943.jpg"]),
        ],
        preferences={},
        output_dir=out_dir,
    )
    plan = JobPlan(
        run_id=run_id,
        engine="autoenhance",
        mode="single",
        jobs=[spec],
        limits=JobLimits(max_outputs_per_job=20, max_jobs_per_batch=1),
        total_outputs=2,
    )
    store = bridge._job_stores["autoenhance"]
    store.init_run(plan)

    # Simulate manifest written with extra auto-merged output
    mnf_file = out_dir / f"manifest_{run_id}_job_001_att1.json"
    mnf_file.write_text(json.dumps({
        "run_id": run_id,
        "job_id": "job_001",
        "attempt_no": 1,
        "items": [
            {"filename": "IMG_9942.jpg", "file_path": str(out_dir / "IMG_9942.jpg")},
            {"filename": "IMG_9943.jpg", "file_path": str(out_dir / "IMG_9943.jpg")},
            {"filename": "IMG_9943_merged.jpg", "file_path": str(out_dir / "IMG_9943_merged.jpg")},
        ]
    }))

    from core.shared.jobs.models import JobAttempt
    rec = store.load_job_record(run_id, "job_001")
    assert rec is not None
    rec.status = "success"
    rec.latest_step = "export"
    rec.attempts = [
        JobAttempt(
            attempt_no=1,
            status="success",
            step="export",
            started_at=100.0,
            finished_at=200.0,
            outputs_succeeded=["IMG_9942.jpg", "IMG_9943.jpg", "IMG_9943_merged.jpg"],
        )
    ]
    store.save_job_record(rec)

    jobs = bridge.get_run_jobs("autoenhance", run_id)
    assert len(jobs) == 1
    job = jobs[0]
    assert job["outputs_succeeded"] == 3
    assert job["outputs_total"] == 3
    assert len(job["outputs"]) == 3
    assert all(o["status"] == "success" for o in job["outputs"])


def test_task_control(bridge, tmp_path):
    assert bridge.is_task_running("autoenhance") is False
    stop_res = bridge.stop_engine_task("autoenhance")
    assert stop_res["success"] is False  # No task running to stop

    # Attempt to start task without license entitlement should fail cleanly
    with patch("ui.bridge.require_access") as mock_req, patch.object(bridge, "inspect_input_folder", return_value={"valid": True, "inputs": 1, "outputs": 1}):
        from core.shared.licensing.models import LicensingAccessError
        mock_req.side_effect = LicensingAccessError("No valid key", code="UNREGISTERED")
        res = bridge.start_engine_task("autoenhance", {"input_dir": str(tmp_path), "output_dir": str(tmp_path), "exec_mode": "single"})
        assert res["success"] is False
        assert "No valid key" in res["message"]


def test_start_engine_task_empty_folder_validation(bridge, tmp_path):
    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()
    res = bridge.start_engine_task("autoenhance", {"input_dir": str(empty_dir), "output_dir": str(tmp_path), "exec_mode": "single"})
    assert res["success"] is False
    assert "Không tìm thấy ảnh hợp lệ" in res["message"]


def test_create_app_factory():
    with patch("webview.create_window") as mock_cw:
        mock_cw.return_value = MagicMock()
        win, api = create_app(debug=False)
        assert mock_cw.called
        assert isinstance(api, BridgeApi)
        args, kwargs = mock_cw.call_args
        assert kwargs.get("title") == "hdr_exe - HDR Processing Suite"
        assert kwargs.get("min_size") == (1024, 640)
        assert kwargs.get("js_api") is api
