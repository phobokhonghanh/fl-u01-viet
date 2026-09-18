"""Tests for Fotello Bracket 7 support and option validation."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from core.fotello.brackets import BracketValidationError, build_bracket_outputs
from core.fotello.config import (
    ConfigurationError,
    FotelloConfig,
    FotelloPreferencesConfig,
    deserialize_fotello_config,
)
from core.fotello.constants import VALID_BRACKET_SIZES, VALID_CLOUD_STYLES, VALID_CONTRAST_STYLES
from core.fotello.execute import create_enhance


def test_bracket_7_in_valid_bracket_sizes():
    """Kiểm tra bracket 7 đã được đưa vào bộ hằng số kích thước bracket hợp lệ."""
    assert 7 in VALID_BRACKET_SIZES
    assert VALID_BRACKET_SIZES == (1, 3, 5, 7)


def test_build_bracket_outputs_with_bracket_7(tmp_path: Path):
    """Kiểm tra phân nhóm và tính output chính xác với bracket 7."""
    files = []
    for i in range(14):
        p = tmp_path / f"img_{i:03d}.jpg"
        p.write_bytes(b"test")
        files.append(p)

    outputs = build_bracket_outputs(files, bracket_size=7)
    assert len(outputs) == 2
    assert len(outputs[0].input_files) == 7
    assert len(outputs[1].input_files) == 7


def test_build_bracket_outputs_bracket_7_remainder_raises(tmp_path: Path):
    """Kiểm tra báo lỗi khi số ảnh không chia hết cho 7."""
    files = []
    for i in range(13):  # Thiếu 1 ảnh cho đủ 2 bracket 7
        p = tmp_path / f"img_{i:03d}.jpg"
        p.write_bytes(b"test")
        files.append(p)

    with pytest.raises(BracketValidationError, match="không chia hết cho bracket_size"):
        build_bracket_outputs(files, bracket_size=7)


def test_fotello_config_preferences_validation():
    """Kiểm tra nạp và validate các options hợp lệ trong config."""
    valid_data = {
        "preferences": {
            "bracket_size": 7,
            "contrast_style": "twilight",
            "exterior_sky_replacement": "off",
            "perspective_correction": "on",
            "cloud_style": "crisp_streaks",
        }
    }
    cfg = deserialize_fotello_config(valid_data)
    assert cfg.preferences.bracket_size == 7
    assert cfg.preferences.contrast_style == "twilight"
    assert cfg.preferences.exterior_sky_replacement == "off"
    assert cfg.preferences.perspective_correction == "on"

    # Lỗi khi contrast_style không hợp lệ
    with pytest.raises(ConfigurationError, match="contrast_style"):
        deserialize_fotello_config({"preferences": {"contrast_style": "invalid_style"}})

    # Lỗi khi cloud_style không hợp lệ
    with pytest.raises(ConfigurationError, match="cloud_style"):
        deserialize_fotello_config({"preferences": {"cloud_style": "invalid_cloud"}})


def test_fotello_create_enhance_strips_cloud_when_sky_off():
    """Kiểm tra khi exterior_sky_replacement='off' thì cloud_style không được gửi lên server."""
    captured_payload = {}

    def mock_urlopen(req, timeout=None):
        nonlocal captured_payload
        import json
        captured_payload = json.loads(req.data.decode("utf-8"))
        res = MagicMock()
        res.__enter__.return_value = res
        res.__exit__.return_value = None
        res.read.return_value = json.dumps({"id": "enh_123"}).encode("utf-8")
        return res

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        create_enhance(
            listing_id="lst_1",
            upload_ids=["up_1"],
            preferences={
                "contrast_style": "signature",
                "exterior_sky_replacement": "off",
                "cloud_style": "full_house_puffs",
                "custom_style_id": None,
            },
            team_id="team_1",
            id_token="token_1",
        )

    prefs = captured_payload.get("preferences", {})
    assert prefs.get("exterior_sky_replacement") == "off"
    # cloud_style và custom_style_id phải được loại bỏ để không vô tình kích hoạt lại thay trời
    assert "cloud_style" not in prefs
    assert "custom_style_id" not in prefs
