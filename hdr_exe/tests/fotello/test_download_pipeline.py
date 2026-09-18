"""Tests for the download pipeline, streaming verification, atomic export, and run manifests."""
from __future__ import annotations

import io
import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image
import pytest

from core.fotello import download
from core.fotello.client import stream_download_image
from core.fotello.download import export_unique_file
from tests.fotello.fixtures.mock_data import (
    MOCK_ENHANCE_DOCS,
    MOCK_MULTI_RENDITION_DOCS,
    MOCK_TOKENS,
    MOCK_VARIANT_DOCS,
)


def _make_dummy_image(w: int, h: int, fmt: str = "JPEG") -> bytes:
    bio = io.BytesIO()
    Image.new("RGB", (w, h), color=(120, 150, 180)).save(bio, fmt)
    return bio.getvalue()


def test_atomic_export_unique_file_no_overwrite(tmp_path: Path):
    dest_dir = tmp_path / "out"
    temp1 = tmp_path / "temp1.jpg"
    temp2 = tmp_path / "temp2.jpg"
    temp3 = tmp_path / "temp3.jpg"

    temp1.write_bytes(b"content1")
    temp2.write_bytes(b"content2")
    temp3.write_bytes(b"content3")

    f1 = export_unique_file(temp1, dest_dir, "photo.jpg")
    f2 = export_unique_file(temp2, dest_dir, "photo.jpg")
    f3 = export_unique_file(temp3, dest_dir, "photo.jpg")

    assert f1.name == "photo.jpg"
    assert f2.name == "photo_1.jpg"
    assert f3.name == "photo_2.jpg"
    assert f1.read_bytes() == b"content1"
    assert f2.read_bytes() == b"content2"
    assert f3.read_bytes() == b"content3"


def test_stream_download_corrupt_image_detection(tmp_path: Path):
    temp_target = tmp_path / "corrupt.jpg"
    bad_bytes = b"Not a real jpeg file"

    mock_resp = MagicMock()
    mock_resp.read.side_effect = [bad_bytes, b""]
    mock_resp.__enter__.return_value = mock_resp

    with patch("core.fotello.client._OPENER.open", return_value=mock_resp):
        with pytest.raises(ValueError, match="không phải ảnh hợp lệ hoặc bị hỏng"):
            stream_download_image("https://mock.com/bad.jpg", "token", temp_target)


def test_stream_download_exceeds_max_bytes(tmp_path: Path):
    temp_target = tmp_path / "huge.jpg"
    chunk = b"X" * 1024

    mock_resp = MagicMock()
    mock_resp.read.side_effect = [chunk, chunk, chunk, b""]
    mock_resp.__enter__.return_value = mock_resp

    with patch("core.fotello.client._OPENER.open", return_value=mock_resp):
        with pytest.raises(ValueError, match="vượt quá giới hạn cho phép"):
            # Set max_bytes to 2000; total is 3072
            stream_download_image("https://mock.com/huge.jpg", "token", temp_target, max_bytes=2000)


def test_stream_download_exceeds_max_dimension(tmp_path: Path):
    temp_target = tmp_path / "big_dim.jpg"
    img_data = _make_dummy_image(4000, 3000)

    mock_resp = MagicMock()
    mock_resp.read.side_effect = [img_data, b""]
    mock_resp.__enter__.return_value = mock_resp

    with patch("core.fotello.client._OPENER.open", return_value=mock_resp):
        with pytest.raises(ValueError, match="vượt quá giới hạn tối đa cho phép"):
            # Max dimension 2000 px, image is 4000x3000
            stream_download_image("https://mock.com/big.jpg", "token", temp_target, max_dimension=2000)


def test_download_fallback_warning_and_manifest(tmp_path: Path):
    out_dir = tmp_path / "fallback_run"
    log_messages = []

    def mock_stream(uri, access_token, dest_temp_path, **kwargs):
        data = _make_dummy_image(3840, 2562)
        dest_temp_path.write_bytes(data)
        return len(data), "sha256_mock", (3840, 2562), "jpg"

    # Mock doc 2: only has editedImage (no editedImageUpsized)
    with patch("core.fotello.download.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.listings.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.listings.run_query", return_value=[MOCK_ENHANCE_DOCS[1]]), \
         patch("core.fotello.download.stream_download_image", side_effect=mock_stream):

        res = download.download_listing(
            listing_id="listing_test_123",
            output_dir=out_dir,
            prioritize_upsized=True,
            log_fn=lambda msg, lvl: log_messages.append((msg, lvl)),
            run_id="run_test_001",
        )

        assert res.success is True
        assert res.status == "success"
        assert res.count == 1
        r_info = res.renditions[0]
        assert r_info.rendition == "edited"
        assert r_info.requested_rendition == "edited_upsized"
        assert "Không có edited_upsized; chuyển sang edited" in (r_info.fallback_reason or "")

        # Check manifest file
        manifest_file = out_dir / "manifest_run_test_001.json"
        assert manifest_file.is_file()
        m_data = json.loads(manifest_file.read_text(encoding="utf-8"))
        assert m_data["run_id"] == "run_test_001"
        assert m_data["files"][0]["requested_rendition"] == "edited_upsized"
        assert "chuyển sang edited" in m_data["files"][0]["fallback_reason"]


def test_zero_downloaded_status_is_failed_not_partial(tmp_path: Path):
    out_dir = tmp_path / "zero_run"

    def mock_failing_stream(*args, **kwargs):
        raise ConnectionError("Network down")

    with patch("core.fotello.download.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.listings.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.listings.run_query", return_value=[MOCK_ENHANCE_DOCS[0]]), \
         patch("core.fotello.download.stream_download_image", side_effect=mock_failing_stream):

        res = download.download_listing(
            listing_id="listing_test_123",
            output_dir=out_dir,
            run_id="run_zero_001",
        )
        assert res.success is False
        assert res.status == "failed"  # Must strictly be failed, NOT partial!
        assert res.count == 0


def test_download_cancellation_via_stop_event(tmp_path: Path):
    out_dir = tmp_path / "cancel_run"
    stop_ev = threading.Event()
    stop_ev.set()  # Cancel immediately

    with patch("core.fotello.download.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.listings.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.listings.run_query", return_value=[MOCK_ENHANCE_DOCS[0]]):

        res = download.download_listing(
            listing_id="listing_test_123",
            output_dir=out_dir,
            stop_event=stop_ev,
        )
        assert res.status == "cancelled"
        assert res.count == 0
