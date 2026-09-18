"""Tests for Autoenhance options standardization, mapping, and catalog schema."""
from __future__ import annotations

import pytest

from core.autoenhance.constants import PRESET_MAP
from core.autoenhance.options import (
    AUTOENHANCE_OPTIONS_SCHEMA,
    DEFAULT_AUTOENHANCE_OPTIONS,
    map_user_options_to_api_payload,
)
from core.shared.options import get_default_options, get_options_schema


def test_autoenhance_default_options_mapping():
    """Kiểm tra ánh xạ bộ tùy chọn mặc định đề xuất sang payload API."""
    payload = map_user_options_to_api_payload(DEFAULT_AUTOENHANCE_OPTIONS)

    assert payload["ai_version"] == "latest"
    assert payload["preset_id"] == PRESET_MAP["vivid"]
    assert payload["vertical_correction"] is True
    assert payload["lens_correction"] is True
    assert payload["privacy"] is False
    assert payload["sky_replacement"] is False
    assert "cloud_type" not in payload  # Tắt thay trời thì không gửi cloud_type
    assert payload["window_pull_type"] == "NONE"
    assert payload["grass"] == "AS_SHOT"
    assert payload["tvs"] == "AS_SHOT"
    assert payload["fire_in_fireplaces"] == "AS_SHOT"
    assert payload["photographer"] == "AS_SHOT"


def test_autoenhance_explicit_effects_mapping():
    """Kiểm tra ánh xạ các giá trị kích hoạt hiệu ứng (không dùng chuỗi 'off' thô)."""
    user_opts = {
        "preset": "warm",
        "sky_replacement": "low_cloud",
        "perspective_correction": False,
        "auto_privacy": True,
        "window_pull": "windows_with_skies",
        "grass": "green",
        "tv_blackout": "black_out",
        "fireplace": "alight",
        "photographer": "remove",
    }
    payload = map_user_options_to_api_payload(user_opts)

    assert payload["preset_id"] == PRESET_MAP["warm"]
    assert payload["vertical_correction"] is False
    assert payload["privacy"] is True
    assert payload["sky_replacement"] is True
    assert payload["cloud_type"] == "LOW_CLOUD"
    assert payload["window_pull_type"] == "WINDOWS_WITH_SKIES"
    assert payload["grass"] == "GREEN"
    assert payload["tvs"] == "BLACK_OUT"
    assert payload["fire_in_fireplaces"] == "ALIGHT"
    assert payload["photographer"] == "REMOVE"


def test_autoenhance_neutral_sky_mapping():
    """Kiểm tra sky_replacement='neutral' ánh xạ sang LOW_CLOUD_LOW_SAT."""
    payload = map_user_options_to_api_payload({"sky_replacement": "neutral"})
    assert payload["sky_replacement"] is True
    assert payload["cloud_type"] == "LOW_CLOUD_LOW_SAT"
    assert payload["window_pull_type"] == "NONE"


def test_shared_options_catalog_contract():
    """Kiểm tra contract danh mục options dùng chung cho cả 2 engine."""
    ae_schema = get_options_schema("autoenhance")
    assert len(ae_schema) >= 10
    ae_fields = {item["field"] for item in ae_schema}
    assert "perspective_correction" in ae_fields
    assert "sky_replacement" in ae_fields
    assert "window_pull" in ae_fields

    fo_schema = get_options_schema("fotello")
    assert len(fo_schema) >= 5
    fo_fields = {item["field"] for item in fo_schema}
    assert "bracket_size" in fo_fields
    assert "contrast_style" in fo_fields
    assert "cloud_style" in fo_fields

    # Kiểm tra trường chưa hỗ trợ được đánh dấu đúng
    custom_style_item = next(i for i in fo_schema if i["field"] == "custom_style_id")
    assert custom_style_item.get("supported") is False

    ae_defaults = get_default_options("autoenhance")
    assert ae_defaults["preset"] == "vivid"
    assert ae_defaults["perspective_correction"] is True
