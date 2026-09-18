"""Tests for Shared Licensing Subsystem."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import MagicMock, mock_open, patch

import pytest
import requests

from core.shared.licensing import (
    activate,
    check,
    clear,
    get_machine_id,
    require_access,
)
from core.shared.licensing.client import verify_license_remotely
from core.shared.licensing.machine import get_raw_machine_id
from core.shared.licensing.models import (
    CODE_FEATURE_NOT_PERMITTED,
    CODE_INVALID_RESPONSE,
    CODE_KEY_INVALID,
    CODE_MACHINE_MISMATCH,
    CODE_NETWORK_ERROR,
    CODE_NETWORK_TIMEOUT,
    CODE_OK,
    CODE_SERVER_ERROR,
    LicenseResult,
    LicensingAccessError,
    MachineIdError,
)
from core.shared.licensing.storage import get_key, load_keys, set_key


@pytest.fixture
def tmp_licensing_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    keys_file = tmp_path / "licensing" / "keys.json"
    monkeypatch.setattr("core.shared.licensing.storage.get_licensing_keys_path", lambda: keys_file)
    monkeypatch.setattr("core.shared.licensing.machine.get_machine_id", lambda: "1234567890abcdef")
    return keys_file


# ---------------------------------------------------------------------------
# 1. API: activate(), check(), require_access(), clear()
# ---------------------------------------------------------------------------

def test_activate_success(tmp_licensing_dir: Path):
    with patch("core.shared.licensing.client.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "valid": True,
            "product": "fotello",
            "level": "plus",
            "message": "Kích hoạt thành công",
            "expires_at": 1893456000,
        }
        mock_post.return_value = mock_resp

        result = activate("fotello", "VALID-FOTELLO-KEY")

        assert result.valid is True
        assert result.engine == "fotello"
        assert result.level == "plus"
        assert result.code == CODE_OK

        # Verify key was persisted
        assert get_key("fotello") == "VALID-FOTELLO-KEY"


def test_activate_failure_does_not_persist_key(tmp_licensing_dir: Path):
    with patch("core.shared.licensing.client.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "valid": False,
            "product": "fotello",
            "message": "Key không hợp lệ",
            "code": CODE_KEY_INVALID,
        }
        mock_post.return_value = mock_resp

        result = activate("fotello", "BAD-KEY")

        assert result.valid is False
        assert result.code == CODE_KEY_INVALID
        assert get_key("fotello") is None


def test_check_online_validation(tmp_licensing_dir: Path):
    set_key("autoenhance", "AE-SAVED-KEY")

    with patch("core.shared.licensing.client.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "valid": True,
            "product": "autoenhance",
            "level": "lite",
            "message": "Key hợp lệ",
        }
        mock_post.return_value = mock_resp

        result = check("autoenhance")
        assert result.valid is True
        assert result.level == "lite"


def test_check_without_stored_key(tmp_licensing_dir: Path):
    result = check("autohdr")
    assert result.valid is False
    assert "chưa kích hoạt" in result.message.lower()


def test_clear_removes_key(tmp_licensing_dir: Path):
    set_key("fotello", "FOTELLO-KEY-TO-CLEAR")
    assert get_key("fotello") == "FOTELLO-KEY-TO-CLEAR"

    clear("fotello")
    assert get_key("fotello") is None
    assert check("fotello").valid is False


def test_require_access_lite_and_plus(tmp_licensing_dir: Path):
    # Lite: allowed for single, rejected for batch
    with patch("core.shared.licensing.check", return_value=LicenseResult(valid=True, engine="autoenhance", level="lite")):
        res_single = require_access("autoenhance", "single")
        assert res_single.valid is True
        assert res_single.level == "lite"

        with pytest.raises(LicensingAccessError, match="chỉ hỗ trợ chế độ 'single'"):
            require_access("autoenhance", "batch")

    # Plus: allowed for both single and batch
    with patch("core.shared.licensing.check", return_value=LicenseResult(valid=True, engine="autoenhance", level="plus")):
        res_single = require_access("autoenhance", "single")
        assert res_single.valid is True
        res_batch = require_access("autoenhance", "batch")
        assert res_batch.valid is True

    # Invalid license: rejected
    with patch("core.shared.licensing.check", return_value=LicenseResult(valid=False, engine="autoenhance", message="Hết hạn")):
        with pytest.raises(LicensingAccessError, match="Hết hạn"):
            require_access("autoenhance", "single")


# ---------------------------------------------------------------------------
# 2. Key status codes: wrong product, expired, machine mismatch, revoked
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "server_payload, expected_code",
    [
        ({"valid": False, "message": "Sai product"}, CODE_KEY_INVALID),
        ({"valid": False, "message": "Key hết hạn"}, CODE_KEY_INVALID),
        ({"valid": False, "message": "Key đã được kích hoạt trên máy khác"}, CODE_MACHINE_MISMATCH),
        ({"valid": False, "message": "Key đã bị thu hồi"}, CODE_KEY_INVALID),
    ],
)
def test_verify_key_status_codes(server_payload: dict, expected_code: str):
    with patch("core.shared.licensing.client.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = server_payload
        mock_post.return_value = mock_resp

        res = verify_license_remotely("fotello", "SOME-KEY", "mach123")
        assert res.valid is False
        assert res.code == expected_code


# ---------------------------------------------------------------------------
# 3. Network & protocol errors: timeout, HTTP 5xx, non-JSON, missing valid, invalid level
# ---------------------------------------------------------------------------

def test_server_timeout_does_not_crash():
    with patch("core.shared.licensing.client.requests.post", side_effect=requests.exceptions.Timeout("Timeout")):
        res = verify_license_remotely("fotello", "SOME-KEY", "mach123")
        assert res.valid is False
        assert res.code == CODE_NETWORK_TIMEOUT
        assert "vượt quá thời gian" in res.message or "timeout" in res.message.lower()


def test_server_500_does_not_crash():
    with patch("core.shared.licensing.client.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_post.return_value = mock_resp

        res = verify_license_remotely("fotello", "SOME-KEY", "mach123")
        assert res.valid is False
        assert res.code == CODE_SERVER_ERROR
        assert "500" in res.message


def test_server_invalid_json_does_not_crash():
    with patch("core.shared.licensing.client.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("Invalid JSON")
        mock_resp.text = "<html>502 Bad Gateway</html>"
        mock_post.return_value = mock_resp

        res = verify_license_remotely("fotello", "SOME-KEY", "mach123")
        assert res.valid is False
        assert res.code == CODE_INVALID_RESPONSE
        assert "JSON" in res.message


def test_server_missing_valid_field():
    with patch("core.shared.licensing.client.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok", "message": "No valid key"}
        mock_post.return_value = mock_resp

        res = verify_license_remotely("fotello", "SOME-KEY", "mach123")
        assert res.valid is False
        assert res.code == CODE_INVALID_RESPONSE
        assert "valid" in res.message


def test_server_invalid_level():
    with patch("core.shared.licensing.client.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"valid": True, "level": "super_vip"}
        mock_post.return_value = mock_resp

        res = verify_license_remotely("fotello", "SOME-KEY", "mach123")
        assert res.valid is False
        assert res.code == CODE_INVALID_RESPONSE
        assert "cấp độ" in res.message.lower()


# ---------------------------------------------------------------------------
# 4. Tampering: manual keys.json editing requires online validation
# ---------------------------------------------------------------------------

def test_tampered_keys_file_cannot_bypass_online_verification(tmp_licensing_dir: Path):
    tmp_licensing_dir.parent.mkdir(parents=True, exist_ok=True)
    # Manually write fake key into keys.json
    tmp_licensing_dir.write_text(json.dumps({"fotello": "FAKE-INJECTED-KEY"}), encoding="utf-8")

    # Server rejects the fake key
    with patch("core.shared.licensing.client.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "valid": False,
            "message": "Key không tồn tại trên hệ thống",
        }
        mock_post.return_value = mock_resp

        res = check("fotello")
        assert res.valid is False
        assert res.code == CODE_KEY_INVALID


# ---------------------------------------------------------------------------
# 5. Machine ID compatibility & deterministic hashing
# ---------------------------------------------------------------------------

def test_machine_id_sha256_16_char_compatibility():
    raw_guid = "9b64c8d5-1234-4567-89ab-cdef01234567"
    expected_hash = hashlib.sha256(raw_guid.encode("utf-8")).hexdigest()[:16]

    with patch("core.shared.licensing.machine.get_raw_machine_id", return_value=raw_guid):
        mid = get_machine_id()
        assert mid == expected_hash
        assert len(mid) == 16


def test_machine_id_linux_reading(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("sys.platform", "linux")
    with patch("os.path.exists", side_effect=lambda p: str(p) == "/etc/machine-id"):
        with patch("builtins.open", mock_open(read_data="linux-unique-machine-uuid-12345\n")):
            raw = get_raw_machine_id()
            assert "linux-unique-machine-uuid-12345" in raw


def test_machine_id_windows_registry_reading(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("sys.platform", "win32")
    mock_winreg = MagicMock()
    mock_winreg.OpenKey.return_value = "mock_key"
    mock_winreg.QueryValueEx.return_value = ("win-registry-guid-987654", 1)
    monkeypatch.setitem(sys.modules, "winreg", mock_winreg)

    raw = get_raw_machine_id()
    assert raw == "win-registry-guid-987654"


def test_machine_id_windows_powershell_fallback(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("sys.platform", "win32")
    mock_winreg = MagicMock()
    mock_winreg.OpenKey.side_effect = OSError("Registry key not found")
    monkeypatch.setitem(sys.modules, "winreg", mock_winreg)

    with patch("subprocess.check_output", return_value="win-powershell-uuid-112233\n"):
        raw = get_raw_machine_id()
        assert raw == "win-powershell-uuid-112233"


def test_machine_id_macos_darwin_reading(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("sys.platform", "darwin")
    ioreg_output = (
        '  | |   "IOPlatformSerialNumber" = "C02XYZ1234"\n'
        '  | |   "IOPlatformUUID" = "E2B9616D-63E8-5B17-9104-ABCD1234EF56"\n'
    )
    with patch("subprocess.check_output", return_value=ioreg_output):
        raw = get_raw_machine_id()
        assert raw == "E2B9616D-63E8-5B17-9104-ABCD1234EF56"


def test_machine_id_failure_raises_machine_id_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("sys.platform", "linux")
    with patch("os.path.exists", return_value=False):
        with pytest.raises(MachineIdError):
            get_raw_machine_id()


# ---------------------------------------------------------------------------
# 6. Privacy: raw keys never leaked into logs, events, or manifests
# ---------------------------------------------------------------------------

def test_raw_key_not_leaked_in_events_or_manifests(tmp_licensing_dir: Path):
    secret_key = "TOP-SECRET-KEY-12345"
    set_key("fotello", secret_key)

    with patch("core.shared.licensing.client.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"valid": True, "level": "plus", "message": "OK"}
        mock_post.return_value = mock_resp

        res = check("fotello")
        assert secret_key not in res.message
        assert secret_key not in str(res.to_dict())
