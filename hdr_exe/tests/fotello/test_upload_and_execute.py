"""Tests for Fotello upload, resource creation (Listing & Enhance), and pre-listing failure guards."""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch
import urllib.response

import pytest

from core.fotello.config import FotelloConfig, FotelloEndpointsConfig, FotelloNetworkConfig
from core.fotello.execute import create_enhance, create_job_resources, create_listing
from core.fotello.upload import upload_job_inputs, upload_single_file
from core.shared.jobs.models import OutputSpec


class MockResponse:
    def __init__(self, status: int = 200, data: dict | None = None, headers: dict | None = None):
        self.status = status
        self.code = status
        self._raw = json.dumps(data or {}).encode("utf-8")
        self.headers = headers or {}

    def read(self, *args):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _make_mock_response(status: int = 200, data: dict | None = None, headers: dict | None = None):
    return MockResponse(status=status, data=data, headers=headers)


def test_upload_single_file_resumable_flow(tmp_path: Path):
    test_file = tmp_path / "photo.jpg"
    test_file.write_bytes(b"image binary bytes")

    config = FotelloConfig()

    calls = []

    def mock_urlopen(req, timeout=None):
        url = req.full_url
        calls.append((url, req.get_method(), dict(req.headers)))
        if "createUpload" in url or "create-upload" in url:
            return _make_mock_response(200, {"id": "up_abc123"})
        elif "fotello-uploads" in url:
            resp = _make_mock_response(200, {})
            resp.headers = {"x-goog-upload-url": "https://storage.mock/upload/session123"}
            return resp
        elif "storage.mock" in url:
            return _make_mock_response(200, {})
        raise RuntimeError(f"Unexpected url {url}")

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        upload_id = upload_single_file(
            filepath=test_file,
            id_token="mock_id_token",
            team_id="team_xyz",
            config=config,
        )

    assert upload_id == "up_abc123"
    assert len(calls) == 3
    # Check authorization header in createUpload
    assert "Bearer mock_id_token" in calls[0][2].get("Authorization")
    # Check Firebase authorization in GCS start
    assert "Firebase mock_id_token" in calls[1][2].get("Authorization")


def test_upload_job_inputs_aborts_on_failure(tmp_path: Path):
    file1 = tmp_path / "photo1.jpg"
    file2 = tmp_path / "photo2.jpg"
    file1.write_bytes(b"bytes 1")
    file2.write_bytes(b"bytes 2")

    outputs = [
        OutputSpec(output_id="out1", input_files=[file1]),
        OutputSpec(output_id="out2", input_files=[file2]),
    ]

    config = FotelloConfig()

    # Fail on photo2.jpg
    def mock_upload_single(path, id_token, team_id, cfg, stop_event=None):
        if path.name == "photo2.jpg":
            raise RuntimeError("Network disconnected")
        return f"up_{path.name}"

    with patch("core.fotello.upload.upload_single_file", side_effect=mock_upload_single):
        output_ids, failed = upload_job_inputs(
            outputs=outputs,
            id_token="token",
            team_id="team",
            config=config,
        )

    # photo1 succeeded, photo2 failed
    assert "out1" in output_ids
    assert "out2" not in output_ids
    assert len(failed) == 1
    assert failed[0]["filename"] == "photo2.jpg"


def test_create_listing_and_enhance_endpoints(tmp_path: Path):
    config = FotelloConfig()

    captured_requests = []

    def mock_urlopen(req, timeout=None):
        body = json.loads(req.data.decode("utf-8")) if req.data else {}
        captured_requests.append((req.full_url, body))
        if "createListing" in req.full_url or "create-listing" in req.full_url:
            return _make_mock_response(200, {"id": "list_999"})
        if "createEnhance" in req.full_url or "create-enhance" in req.full_url:
            return _make_mock_response(200, {"id": "enh_777"})
        raise RuntimeError("Unexpected url")

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        listing_id, enhance_map = create_job_resources(
            listing_name="Test Listing",
            outputs=[OutputSpec(output_id="b1", input_files=[tmp_path / "f1.jpg"])],
            output_upload_ids={"b1": ["up_111"]},
            preferences={"contrast_style": "signature"},
            team_id="team_123",
            id_token="id_tok",
            config=config,
        )

    assert listing_id == "list_999"
    assert enhance_map == {"b1": "enh_777"}
    assert len(captured_requests) == 2
    assert captured_requests[0][1]["name"] == "Test Listing"
    assert captured_requests[0][1]["teamId"] == "team_123"
    assert captured_requests[1][1]["upload_ids"] == ["up_111"]


def test_creation_uses_configured_paths_and_timeout(tmp_path):
    cfg = FotelloConfig(
        endpoints=FotelloEndpointsConfig(
            api_base_url="https://fixture.invalid/",
            create_listing_path="/listings",
            create_enhance_path="/enhances",
        ),
        network=FotelloNetworkConfig(request_timeout_seconds=17),
    )
    with patch("urllib.request.urlopen", side_effect=[
        _make_mock_response(data={"id": "listing"}),
        _make_mock_response(data={"id": "enhance"}),
    ]) as open_url:
        create_job_resources(
            listing_name="Fixture", outputs=[OutputSpec(output_id="one", input_files=[tmp_path / "one.jpg"])],
            output_upload_ids={"one": ["upload"]}, preferences={}, team_id="team", id_token="fixture",
            config=cfg,
        )
    assert [call.args[0].full_url for call in open_url.call_args_list] == [
        "https://fixture.invalid/listings", "https://fixture.invalid/enhances",
    ]
    assert all(call.kwargs["timeout"] == 17 for call in open_url.call_args_list)
