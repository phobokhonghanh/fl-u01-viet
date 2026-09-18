"""Comparison tests proving why downloaded images were smaller in standalone."""
from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import core.fotello as fotello
import core.fotello.from_debug as ae_debug
from tests.fotello.fixtures.mock_data import (
    MOCK_ENHANCE_DOCS,
    MOCK_MULTI_RENDITION_DOCS,
    MOCK_TOKENS,
    MOCK_VARIANT_DOCS,
)


def _make_dummy_image(w: int, h: int) -> bytes:
    bio = io.BytesIO()
    Image.new("RGB", (w, h), color=(100, 100, 100)).save(bio, "JPEG")
    return bio.getvalue()


def test_reproduce_and_verify_resolution_shortfall(tmp_path: Path):
    """
    This test reproduces the exact bug:
    - In legacy standalone: DEFAULT_RENDITIONS picks 'edited' (2048x1365) instead of 'edited_upsized' (8192x5464).
    - In from_debug: 'editedImageUpsized' is picked first (8192x5464).
    - In main fotello: 'edited_upsized' is picked first (8192x5464).
    """
    def mock_stream(uri, access_token, dest_temp_path, **kwargs):
        if "upsized" in uri:
            data = _make_dummy_image(8192, 5464)
            dest_temp_path.write_bytes(data)
            return len(data), "mock_sha", (8192, 5464), "jpg"
        data = _make_dummy_image(2048, 1365)
        dest_temp_path.write_bytes(data)
        return len(data), "mock_sha", (2048, 1365), "jpg"

    def mock_debug_blob(uri: str, token: str, timeout: int = 60) -> bytes:
        if "upsized" in uri:
            return _make_dummy_image(8192, 5464)
        return _make_dummy_image(2048, 1365)

    # 1. Run legacy download (reproduce bug: photo is smaller than source)
    with patch("core.fotello.download.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.listings.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.listings.run_query", return_value=[MOCK_ENHANCE_DOCS[0]]), \
         patch("core.fotello.download.stream_download_image", side_effect=mock_stream):
        
        legacy_res = fotello.download.download_listing(
            listing_id="listing_test_123",
            output_dir=tmp_path / "standalone_legacy",
            prioritize_upsized=False,  # Legacy bug
        )
        assert legacy_res.renditions[0].downloaded_width == 2048
        assert legacy_res.renditions[0].rendition == "edited"

    # 2. Run corrected main fotello download (fix verified: photo downloaded at full 8192x5464)
    with patch("core.fotello.download.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.listings.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.listings.run_query", return_value=[MOCK_ENHANCE_DOCS[0]]), \
         patch("core.fotello.download.stream_download_image", side_effect=mock_stream):
        
        fixed_res = fotello.download.download_listing(
            listing_id="listing_test_123",
            output_dir=tmp_path / "standalone_fixed",
            prioritize_upsized=True,  # Fixed
        )
        assert fixed_res.renditions[0].downloaded_width == 8192
        assert fixed_res.renditions[0].rendition == "edited_upsized"

    # 3. Run from_debug download (verifies 8192x5464 picked from server)
    with patch("core.fotello.from_debug.download.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.from_debug.listings.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.from_debug.listings.firestore_run_query", return_value=[MOCK_ENHANCE_DOCS[0]]), \
         patch("core.fotello.from_debug.download.download_media_uri", side_effect=mock_debug_blob):
        
        debug_res = ae_debug.download(
            listing_id="listing_test_123",
            output_dir=tmp_path / "debug",
            allow_local_resize=False,
        )
        assert debug_res.renditions[0].downloaded_width == 8192
        assert debug_res.renditions[0].is_upsized is True


def test_all_renditions_dual_engine_comparison(tmp_path: Path):
    """Test all_renditions mode on both engines using fixtures.
    
    Verifies:
    1. Detection of edited, edited_upsized, merged, merged_upsized, and variant/render outputs.
    2. File naming convention:
       {ten_anh}__{enhance_id}__{rendition}__{width}x{height}.{ext}
       and with variant/render ID.
    3. Manifest saving with SHA-256 hashes and dimensions.
    4. Exact equivalence between from_debug and main fotello downloads.
    """
    import hashlib
    def mock_download_rendition(uri: str, token: str, timeout: int = 60) -> bytes:
        if "upsized" in uri:
            return _make_dummy_image(6000, 4000)
        return _make_dummy_image(2048, 1365)

    def mock_stream_rendition(uri, access_token, dest_temp_path, **kwargs):
        raw = mock_download_rendition(uri, access_token)
        dest_temp_path.write_bytes(raw)
        h = hashlib.sha256(raw).hexdigest()
        dims = (6000, 4000) if "upsized" in uri else (2048, 1365)
        return len(raw), h, dims, "jpg"

    debug_dir = tmp_path / "from_debug" / "listing_multi_123"
    fotello_dir = tmp_path / "fotello" / "listing_multi_123"

    # 1. Run from_debug with all_renditions=True
    with patch("core.fotello.from_debug.download.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.from_debug.listings.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.from_debug.listings.firestore_run_query", side_effect=[MOCK_MULTI_RENDITION_DOCS, MOCK_VARIANT_DOCS]), \
         patch("core.fotello.from_debug.download.download_media_uri", side_effect=mock_download_rendition):

        res_debug = ae_debug.download(
            listing_id="listing_multi_123",
            output_dir=debug_dir,
            all_renditions=True,
        )
        assert res_debug.success is True
        assert res_debug.count == 6

    # 2. Run main fotello with all_renditions=True
    with patch("core.fotello.download.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.listings.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.listings.run_query", side_effect=[MOCK_MULTI_RENDITION_DOCS, MOCK_VARIANT_DOCS]), \
         patch("core.fotello.download.stream_download_image", side_effect=mock_stream_rendition):

        res_fotello = fotello.download.download_listing(
            listing_id="listing_multi_123",
            output_dir=fotello_dir,
            all_renditions=True,
            run_id="comp_run",
        )
        assert res_fotello.success is True
        assert res_fotello.count == 6

    # 3. Check filenames in both output directories
    debug_files = sorted([f.name for f in debug_dir.glob("*.jpg")])
    fotello_files = sorted([f.name for f in fotello_dir.glob("*.jpg")])
    assert debug_files == fotello_files

    expected_files = [
        "phong_khach__enh_abc123__edited__2048x1365.jpg",
        "phong_khach__enh_abc123__edited_upsized__6000x4000.jpg",
        "phong_khach__enh_abc123__merged__2048x1365.jpg",
        "phong_khach__enh_abc123__merged_upsized__6000x4000.jpg",
        "phong_khach__enh_abc123__var_sky01__rnd_v1__output__2048x1365.jpg",
        "phong_khach__enh_abc123__var_sky01__rnd_v1__output_upsized__6000x4000.jpg",
    ]
    assert debug_files == sorted(expected_files)

    # 4. Check manifests
    assert (debug_dir / "manifest.json").exists()
    assert res_fotello.manifest_path is not None
    assert res_fotello.manifest_path.exists()

    debug_manifest = json.loads((debug_dir / "manifest.json").read_text())
    fotello_manifest = json.loads(res_fotello.manifest_path.read_text())

    assert debug_manifest["total_renditions"] == 6
    assert fotello_manifest["total_renditions"] == 6
    assert len(debug_manifest["files"]) == 6
    assert len(fotello_manifest["files"]) == 6

    # Verify SHA-256 hashes match across files
    debug_sha_map = {f["filename"]: f["sha256"] for f in debug_manifest["files"]}
    fotello_sha_map = {f["filename"]: f["sha256"] for f in fotello_manifest["files"]}
    for fname in expected_files:
        assert debug_sha_map[fname] == fotello_sha_map[fname]
        assert len(debug_sha_map[fname]) == 64
