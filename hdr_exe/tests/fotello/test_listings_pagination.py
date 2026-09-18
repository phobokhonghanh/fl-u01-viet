"""Unit tests for Fotello full cursor pagination, auth/team_id enforcement, and batch download."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.fotello.config import FotelloConfig, FotelloEndpointsConfig, FotelloFirestoreConfig
from core.fotello.download import download_multiple_listings
from core.fotello.listings import ListingsList, list_listings
from core.fotello.models import DownloadResult


def _make_listing_doc(doc_id: str, name: str = "Villa", team_id: str = "team_123") -> dict:
    return {
        "document": {
            "name": f"projects/proj/databases/(default)/documents/listings/{doc_id}",
            "fields": {
                "teamId": {"stringValue": team_id},
                "name": {"stringValue": name},
                "createdAt": {"stringValue": "2026-09-10T12:00:00Z"},
                "status": {"stringValue": "completed"},
            },
        }
    }


def test_list_listings_requires_auth_and_team():
    """Kiểm tra bắt buộc phải có auth token và team_id; không được trả danh sách rỗng im lặng."""
    with patch("core.fotello.listings.get_tokens", return_value={}):
        with pytest.raises(RuntimeError, match="Chưa đăng nhập Fotello"):
            list_listings()

    with patch("core.fotello.listings.get_tokens", return_value={"access_token": "token_abc"}):
        with pytest.raises(RuntimeError, match="Không tìm thấy thông tin team_id"):
            list_listings()


def test_list_listings_cursor_pagination_and_deduplication():
    """Kiểm tra lấy đầy đủ danh sách listing qua nhiều trang và khử trùng lặp ID."""
    page1 = [_make_listing_doc(f"lst_{i}", f"House {i}") for i in range(1, 4)]
    # page2 chứa lst_3 (trùng) và lst_4, lst_5
    page2 = [_make_listing_doc("lst_3", "House 3"), _make_listing_doc("lst_4", "House 4"), _make_listing_doc("lst_5", "House 5")]

    with patch("core.fotello.listings.get_tokens", return_value={"access_token": "token_123", "team_id": "team_123"}), \
         patch("core.fotello.listings.run_query", return_value=page1 + page2):

        res = list_listings(paginate=True)

        assert isinstance(res, ListingsList)
        assert res.complete is True
        # Sau khi khử trùng lặp lst_3 thì còn lại đúng 5 listing (lst_1, lst_2, lst_3, lst_4, lst_5)
        assert len(res) == 5
        assert [l["id"] for l in res] == ["lst_1", "lst_2", "lst_3", "lst_4", "lst_5"]
        assert res[0]["name"] == "House 1"
        assert res[0]["status"] == "completed"


def test_list_listings_normalization_of_missing_fields():
    """Kiểm tra chuẩn hóa giá trị 'Chưa có thông tin' khi server không cung cấp."""
    doc_sparse = {
        "document": {
            "name": "projects/proj/databases/(default)/documents/listings/lst_sparse",
            "fields": {
                "teamId": {"stringValue": "team_123"},
                # Thiếu name, createdAt, status
            },
        }
    }

    with patch("core.fotello.listings.get_tokens", return_value={"access_token": "token_123", "team_id": "team_123"}), \
         patch("core.fotello.listings.run_query", return_value=[doc_sparse]):

        res = list_listings()
        assert len(res) == 1
        item = res[0]
        assert item["name"] == "Chưa có thông tin"
        assert item["createdAt"] == "Chưa có thông tin"
        assert item["status"] == "Chưa có thông tin"


def test_download_multiple_listings_sequential(tmp_path: Path):
    """Kiểm tra tải nhiều listing tuần tự vào các thư mục con riêng biệt."""
    out_dir = tmp_path / "multi_downloads"

    def mock_dl_listing(listing_id, output_dir, **kwargs):
        # Giả lập lst_1 thành công, lst_2 thất bại
        if listing_id == "lst_1":
            f = Path(output_dir) / "img1.jpg"
            f.write_bytes(b"data")
            return DownloadResult(success=True, status="success", count=1, files=[f], message="OK")
        else:
            return DownloadResult(success=False, status="failed", count=0, errors=[{"error": "No images"}], message="Failed")

    with patch("core.fotello.download.check", return_value=MagicMock(valid=True)), \
         patch("core.fotello.download.get_tokens", return_value={"access_token": "token_123", "team_id": "team_123"}), \
         patch("core.fotello.download.download_listing", side_effect=mock_dl_listing):

        res = download_multiple_listings(["lst_1", "lst_2"], output_dir=out_dir)

        assert res["total"] == 2
        assert res["succeeded"] == 1
        assert res["failed"] == 1
        assert res["status"] == "partial"
        assert (out_dir / "lst_1").is_dir()
        assert (out_dir / "lst_2").is_dir()
        assert Path(res["manifest_path"]).is_file()
