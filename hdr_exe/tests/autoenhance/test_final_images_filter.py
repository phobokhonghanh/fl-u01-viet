"""Tests for Autoenhance Final Processed Images Filtering."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from core.autoenhance.orders import filter_final_processed_images, get_order_brackets


def test_filter_final_images_using_brackets():
    """Kiểm tra việc lọc ảnh dựa trên danh sách /orders/{id}/brackets."""
    all_images = [
        # 4 ảnh neutral sơ bộ được tạo tự động khi upload
        {"image_id": "raw_1", "image_name": "room1.jpg", "date_added": 100, "metadata": {"MIMEType": "image/jpeg"}},
        {"image_id": "raw_2", "image_name": "room2.jpg", "date_added": 100, "metadata": {"MIMEType": "image/jpeg"}},
        {"image_id": "raw_3", "image_name": "room3.jpg", "date_added": 100, "metadata": {"MIMEType": "image/jpeg"}},
        {"image_id": "raw_4", "image_name": "room4.jpg", "date_added": 100, "metadata": {"MIMEType": "image/jpeg"}},
        # 4 ảnh thành phẩm AI sau khi gọi process
        {"image_id": "fin_1", "image_name": "room1.jpg", "date_added": 120, "metadata": {"Manually Grouped": False}, "preset_id": "vivid_id"},
        {"image_id": "fin_2", "image_name": "room2.jpg", "date_added": 120, "metadata": {"Manually Grouped": False}, "preset_id": "vivid_id"},
        {"image_id": "fin_3", "image_name": "room3.jpg", "date_added": 120, "metadata": {"Manually Grouped": False}, "preset_id": "vivid_id"},
        {"image_id": "fin_4", "image_name": "room4.jpg", "date_added": 120, "metadata": {"Manually Grouped": False}, "preset_id": "vivid_id"},
    ]

    mock_brackets = [
        {"bracket_id": "b1", "image_id": "fin_1", "name": "room1.jpg"},
        {"bracket_id": "b2", "image_id": "fin_2", "name": "room2.jpg"},
        {"bracket_id": "b3", "image_id": "fin_3", "name": "room3.jpg"},
        {"bracket_id": "b4", "image_id": "fin_4", "name": "room4.jpg"},
    ]

    with patch("core.autoenhance.orders.get_order_brackets", return_value=mock_brackets):
        res = filter_final_processed_images(
            order_id="ord_test",
            images=all_images,
            api_key="key_123",
        )

        assert len(res) == 4
        res_ids = [img["image_id"] for img in res]
        assert res_ids == ["fin_1", "fin_2", "fin_3", "fin_4"]
        # Tên file phải giữ nguyên tên gốc ban đầu
        assert [img["image_name"] for img in res] == ["room1.jpg", "room2.jpg", "room3.jpg", "room4.jpg"]


def test_filter_final_images_hdr_brackets():
    """Kiểm tra trường hợp ghép HDR (6 ảnh bracket ghép thành 2 ảnh kết quả)."""
    all_images = [
        {"image_id": "raw_1", "image_name": "IMG_001.jpg", "metadata": {"MIMEType": "image/jpeg"}},
        {"image_id": "raw_2", "image_name": "IMG_002.jpg", "metadata": {"MIMEType": "image/jpeg"}},
        {"image_id": "raw_3", "image_name": "IMG_003.jpg", "metadata": {"MIMEType": "image/jpeg"}},
        {"image_id": "raw_4", "image_name": "IMG_004.jpg", "metadata": {"MIMEType": "image/jpeg"}},
        {"image_id": "raw_5", "image_name": "IMG_005.jpg", "metadata": {"MIMEType": "image/jpeg"}},
        {"image_id": "raw_6", "image_name": "IMG_006.jpg", "metadata": {"MIMEType": "image/jpeg"}},
        # 2 ảnh kết quả HDR
        {"image_id": "hdr_1", "image_name": "IMG_003.jpg", "metadata": {"Manually Grouped": False}, "preset_id": "vivid_id"},
        {"image_id": "hdr_2", "image_name": "IMG_006.jpg", "metadata": {"Manually Grouped": False}, "preset_id": "vivid_id"},
    ]

    mock_brackets = [
        {"bracket_id": "b1", "image_id": "hdr_1", "name": "IMG_001.jpg"},
        {"bracket_id": "b2", "image_id": "hdr_1", "name": "IMG_002.jpg"},
        {"bracket_id": "b3", "image_id": "hdr_1", "name": "IMG_003.jpg"},
        {"bracket_id": "b4", "image_id": "hdr_2", "name": "IMG_004.jpg"},
        {"bracket_id": "b5", "image_id": "hdr_2", "name": "IMG_005.jpg"},
        {"bracket_id": "b6", "image_id": "hdr_2", "name": "IMG_006.jpg"},
    ]

    with patch("core.autoenhance.orders.get_order_brackets", return_value=mock_brackets):
        res = filter_final_processed_images(
            order_id="ord_hdr",
            images=all_images,
            api_key="key_123",
        )

        assert len(res) == 2
        res_ids = [img["image_id"] for img in res]
        assert res_ids == ["hdr_1", "hdr_2"]
        assert [img["image_name"] for img in res] == ["IMG_003.jpg", "IMG_006.jpg"]


def test_filter_final_images_fallback_heuristics():
    """Kiểm tra cơ chế fallback khi không gọi được /brackets endpoint."""
    all_images = [
        {"image_id": "raw_1", "image_name": "living.jpg", "date_added": 100, "metadata": {"MIMEType": "image/jpeg"}},
        {"image_id": "fin_1", "image_name": "living.jpg", "date_added": 125, "metadata": {"Manually Grouped": False}, "preset_id": "vivid_id"},
        {"image_id": "raw_2", "image_name": "bed.jpg", "date_added": 100, "metadata": {"MIMEType": "image/jpeg"}},
        {"image_id": "fin_2", "image_name": "bed.jpg", "date_added": 125, "metadata": {"Manually Grouped": False}, "preset_id": "vivid_id"},
    ]

    with patch("core.autoenhance.orders.get_order_brackets", return_value=[]):
        res = filter_final_processed_images(
            order_id="ord_fallback",
            images=all_images,
            api_key="key_123",
        )

        assert len(res) == 2
        res_ids = {img["image_id"] for img in res}
        assert res_ids == {"fin_1", "fin_2"}
        assert {img["image_name"] for img in res} == {"living.jpg", "bed.jpg"}


def test_filter_final_images_clean_order_unchanged():
    """Kiểm tra đơn hàng sạch (không có bản trùng lặp sơ bộ) không bị lọc mất ảnh."""
    clean_images = [
        {"image_id": "img_1", "image_name": "kitchen.jpg"},
        {"image_id": "img_2", "image_name": "bath.jpg"},
    ]

    with patch("core.autoenhance.orders.get_order_brackets", return_value=[]):
        res = filter_final_processed_images(
            order_id="ord_clean",
            images=clean_images,
            api_key="key_123",
        )

        assert len(res) == 2
        assert [img["image_id"] for img in res] == ["img_1", "img_2"]
