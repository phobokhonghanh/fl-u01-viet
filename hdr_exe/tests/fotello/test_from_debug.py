"""Unit tests for Fotello from_debug engine."""
from __future__ import annotations

import io
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image
import pytest

import core.fotello.from_debug as ae_debug
from tests.fotello.fixtures.mock_data import MOCK_ENHANCE_DOCS, MOCK_LISTING_DOCS, MOCK_TOKENS


def _make_dummy_image(w: int, h: int) -> bytes:
    bio = io.BytesIO()
    Image.new("RGB", (w, h), color=(120, 150, 200)).save(bio, "JPEG")
    return bio.getvalue()


def test_auth_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    fake_tokens_file = tmp_path / ".hdr_exe" / "fotello" / "tokens.json"
    with patch("core.fotello.from_debug.auth.TOKENS_FILE", fake_tokens_file):
        assert ae_debug.validate_session() is False
        ae_debug.save_fotello_tokens(MOCK_TOKENS)
        assert ae_debug.validate_session() is True
        status = ae_debug.get_status()
        assert status["connected"] is True
        assert status["email"] == "test@fotello.co"


def test_list_listings_and_enhances(tmp_path: Path):
    with patch("core.fotello.from_debug.listings.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.from_debug.listings.firestore_run_query", side_effect=[MOCK_LISTING_DOCS, MOCK_ENHANCE_DOCS]):
        listings = ae_debug.list_listings()
        assert len(listings) == 1
        assert listings[0]["name"] == "123 Villa Luxury"

        enhances = ae_debug.list_enhances_for_listing("listing_test_123")
        assert len(enhances) == 2
        # Debug prioritizes upsized for enh_001
        assert enhances[0]["upsized"] is True
        assert "upsized.jpg" in enhances[0]["image_uri"]
        # enh_002 only has standard
        assert enhances[1]["upsized"] is False
        assert "standard.jpg" in enhances[1]["image_uri"]


def test_download_flow_with_local_resize(tmp_path: Path):
    out_dir = tmp_path / "out"
    events = []

    def mock_download_blob(uri: str, token: str, timeout: int = 60) -> bytes:
        if "upsized" in uri:
            return _make_dummy_image(8192, 5464)
        return _make_dummy_image(2048, 1365)

    with patch("core.fotello.from_debug.download.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.from_debug.download.list_enhances_for_listing", return_value=[
             {
                 "id": "enh_002",
                 "has_image": True,
                 "upsized": False,
                 "image_uri": "gs://bucket/enh_002_standard.jpg",
                 "name": "bedroom.jpg",
                 "inputWidth": 6000,
                 "inputHeight": 4000,
             }
         ]), \
         patch("core.fotello.from_debug.download.download_media_uri", side_effect=mock_download_blob):
        
        # 1. Download without local resize (preserves raw downloaded dimensions)
        res1 = ae_debug.download(
            listing_id="listing_test_123",
            output_dir=out_dir / "raw",
            allow_local_resize=False,
            event_fn=events.append,
        )
        assert res1.success is True
        assert res1.count == 1
        r_info1 = res1.renditions[0]
        assert r_info1.downloaded_width == 2048
        assert r_info1.final_width == 2048
        assert r_info1.resolution_shortfall is True
        assert r_info1.is_local_resized is False

        # 2. Download with local resize (reproduces debug upscaling behavior)
        res2 = ae_debug.download(
            listing_id="listing_test_123",
            output_dir=out_dir / "resized",
            allow_local_resize=True,
        )
        assert res2.success is True
        r_info2 = res2.renditions[0]
        assert r_info2.downloaded_width == 2048
        assert r_info2.final_width == 6000
        assert r_info2.is_local_resized is True


from tests.fotello.fixtures.mock_data import MOCK_MULTI_RENDITION_DOCS


def test_all_renditions_partial_failure_tolerance(tmp_path: Path):
    """Verify that if one rendition fails to download, remaining renditions still download successfully."""
    out_dir = tmp_path / "partial"

    def mock_flaky_download(uri: str, token: str, timeout: int = 60) -> bytes:
        if "merged" in uri:
            raise urllib.error.HTTPError(uri, 404, "Not Found", {}, None)
        return _make_dummy_image(3000, 2000)

    import urllib.error
    with patch("core.fotello.from_debug.download.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.from_debug.listings.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.from_debug.listings.firestore_run_query", side_effect=[MOCK_MULTI_RENDITION_DOCS, []]), \
         patch("core.fotello.from_debug.download.download_media_uri", side_effect=mock_flaky_download):

        res = ae_debug.download(
            listing_id="listing_multi_123",
            output_dir=out_dir,
            all_renditions=True,
        )
        # Succeeded partially: 2 succeeded (edited, edited_upsized), 2 failed (merged, merged_upsized)
        assert res.status == "partial"
        assert res.count == 2
        assert len(res.errors) == 2
        assert any(e["rendition"] == "merged" for e in res.errors)
