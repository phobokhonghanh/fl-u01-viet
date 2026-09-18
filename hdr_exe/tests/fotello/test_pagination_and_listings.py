"""Tests for Firestore cursor pagination, pagination limits, and listings retrieval."""
from __future__ import annotations

from core.fotello.config import FotelloConfig, FotelloEndpointsConfig, FotelloFirestoreConfig

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.fotello.firestore import run_query
from core.fotello.listings import fetch_enhances_for_listing, fetch_variants_for_listing
from tests.fotello.fixtures.mock_data import MOCK_TOKENS


def _make_mock_firestore_row(doc_id: str, listing_id: str = "listing_test_123") -> dict:
    return {
        "document": {
            "name": f"projects/proj/databases/(default)/documents/enhances/{doc_id}",
            "fields": {
                "listingId": {"stringValue": listing_id},
                "status": {"stringValue": "completed"},
                "editedImage": {"stringValue": f"gs://bucket/renders/{doc_id}.jpg"},
            },
        }
    }


def test_cursor_pagination_multiple_pages():
    # Simulate page 1 (3 items, limit=3) and page 2 (1 item, indicating last page)
    page1 = [_make_mock_firestore_row(f"doc_{i}") for i in range(1, 4)]
    page2 = [_make_mock_firestore_row("doc_4")]

    responses = [page1, page2]

    with patch("core.fotello.firestore.load_fotello_config") as mock_cfg, \
         patch("urllib.request.urlopen") as mock_urlopen:

        cfg_mock = FotelloConfig(
            endpoints=FotelloEndpointsConfig(firestore_url="https://firestore.mock"),
            firestore=FotelloFirestoreConfig(page_size=3, max_query_documents=100, request_timeout_seconds=30),
        )
        mock_cfg.return_value = cfg_mock

        resp1 = MagicMock()
        resp1.read.return_value = json.dumps(page1).encode("utf-8")
        resp2 = MagicMock()
        resp2.read.return_value = json.dumps(page2).encode("utf-8")

        mock_urlopen.return_value.__enter__.side_effect = [resp1, resp2]

        query = {"from": [{"collectionId": "enhances"}]}
        results = run_query("token_123", query, paginate=True)

        assert len(results) == 4
        doc_names = [r["document"]["name"].split("/")[-1] for r in results]
        assert doc_names == ["doc_1", "doc_2", "doc_3", "doc_4"]


def test_cursor_pagination_infinite_loop_detection():
    # If server keeps returning the same page / cursor
    same_page = [_make_mock_firestore_row("stuck_doc") for _ in range(3)]

    with patch("core.fotello.firestore.load_fotello_config") as mock_cfg, \
         patch("urllib.request.urlopen") as mock_urlopen:

        cfg_mock = FotelloConfig(
            endpoints=FotelloEndpointsConfig(firestore_url="https://firestore.mock"),
            firestore=FotelloFirestoreConfig(page_size=3, max_query_documents=100, request_timeout_seconds=30),
        )
        mock_cfg.return_value = cfg_mock

        resp = MagicMock()
        resp.read.return_value = json.dumps(same_page).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = resp

        query = {"from": [{"collectionId": "enhances"}]}
        with pytest.raises(RuntimeError, match="Phát hiện vòng lặp phân trang"):
            run_query("token_123", query, paginate=True)


def test_variant_query_uses_only_listing_id():
    with patch("core.fotello.listings.get_tokens", return_value=MOCK_TOKENS), \
         patch("core.fotello.listings.run_query") as mock_run_query:

        mock_run_query.return_value = []
        fetch_variants_for_listing("listing_abc_123")

        assert mock_run_query.call_count == 1
        query_arg = mock_run_query.call_args[0][1]
        field_filter = query_arg["where"]["fieldFilter"]
        # Must strictly use listingId
        assert field_filter["field"]["fieldPath"] == "listingId"
        assert field_filter["value"]["stringValue"] == "listing_abc_123"
