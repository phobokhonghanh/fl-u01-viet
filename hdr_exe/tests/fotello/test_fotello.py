"""Unit tests for main Fotello engine."""
from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch
import urllib.error

from PIL import Image
import pytest

import core.fotello as fotello
from core.fotello import auth, cdp, download, listings
from tests.fotello.fixtures.mock_data import (
    MOCK_ENHANCE_DOCS,
    MOCK_LISTING_DOCS,
    MOCK_MULTI_RENDITION_DOCS,
    MOCK_TOKENS,
    MOCK_VARIANT_DOCS,
)


def _make_dummy_image(w: int, h: int) -> bytes:
    bio = io.BytesIO()
    Image.new("RGB", (w, h), color=(100, 180, 120)).save(bio, "JPEG")
    return bio.getvalue()


def test_package_does_not_auto_import_from_debug():
    """Verify that importing core.fotello does not expose from_debug in public namespace."""
    assert "from_debug" not in fotello.__all__
    # Check that core/fotello/__init__.py source code has no 'from_debug' import
    init_path = Path(fotello.__file__)
    init_source = init_path.read_text(encoding="utf-8")
    assert "from_debug" not in init_source


def test_auth_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    fake_tokens_file = tmp_path / ".hdr_exe" / "fotello" / "tokens.json"

    assert auth.validate_session() is False
    auth.save_tokens(MOCK_TOKENS)
    assert auth.validate_session() is True
    status = auth.get_status()
    assert status["connected"] is True
    assert status["service"] == "fotello"
    assert status["email"] == "test@fotello.co"


def test_list_enhances_rendition_priority():
    with patch("core.fotello.listings.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.listings.run_query", return_value=MOCK_ENHANCE_DOCS):
        
        # 1. Legacy behavior (prioritize_upsized=False): chooses edited (standard) first
        enhances_legacy = listings.list_enhances("listing_test_123", prioritize_upsized=False)
        assert len(enhances_legacy) == 2
        assert enhances_legacy[0]["rendition"] == "edited"
        assert "standard.jpg" in enhances_legacy[0]["image_uri"]

        # 2. Corrected behavior (prioritize_upsized=True): chooses edited_upsized first
        enhances_fixed = listings.list_enhances("listing_test_123", prioritize_upsized=True)
        assert len(enhances_fixed) == 2
        assert enhances_fixed[0]["rendition"] == "edited_upsized"
        assert "upsized.jpg" in enhances_fixed[0]["image_uri"]


def test_download_flow(tmp_path: Path):
    out_dir = tmp_path / "out"

    def mock_stream(uri, access_token, dest_temp_path, **kwargs):
        data = _make_dummy_image(8192, 5464)
        dest_temp_path.write_bytes(data)
        return len(data), "sha256_mock", (8192, 5464), "jpg"

    with patch("core.fotello.download.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.download.list_enhances") as mock_list, \
         patch("core.fotello.download.stream_download_image", side_effect=mock_stream):
        
        mock_list.return_value = [
            {
                "id": "enh_001",
                "has_image": True,
                "rendition": "edited_upsized",
                "image_uri": "gs://bucket/enh_001_upsized.jpg",
                "filename": "living_room.jpg",
            }
        ]

        res = download.download_listing(
            listing_id="listing_test_123",
            output_dir=out_dir,
            prioritize_upsized=True,
        )
        assert res.success is True
        assert res.count == 1
        r_info = res.renditions[0]
        assert r_info.downloaded_width == 8192
        assert r_info.is_upsized is True
        assert r_info.is_local_resized is False


def test_all_renditions_partial_failure_tolerance(tmp_path: Path):
    """Verify that Fotello continues downloading when one rendition fails."""
    out_dir = tmp_path / "partial_fotello"

    def mock_flaky_stream(uri, access_token, dest_temp_path, **kwargs):
        if "edited_upsized" in uri:
            raise urllib.error.HTTPError(uri, 500, "Internal Server Error", {}, None)
        data = _make_dummy_image(3000, 2000)
        dest_temp_path.write_bytes(data)
        return len(data), "mock_sha", (3000, 2000), "jpg"

    with patch("core.fotello.download.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.listings.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.listings.run_query", side_effect=[MOCK_MULTI_RENDITION_DOCS, MOCK_VARIANT_DOCS]), \
         patch("core.fotello.download.stream_download_image", side_effect=mock_flaky_stream):

        res = download.download_listing(
            listing_id="listing_multi_123",
            output_dir=out_dir,
            all_renditions=True,
            run_id="partial_run_01",
        )
        assert res.success is True
        assert res.status == "partial"
        assert res.count == 5  # 6 total - 1 failed = 5 successful
        failed_rends = [r for r in res.renditions if r.error]
        assert len(failed_rends) == 1
        assert failed_rends[0].rendition == "edited_upsized"
        assert (out_dir / "manifest_partial_run_01.json").exists()
