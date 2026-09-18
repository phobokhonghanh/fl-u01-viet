"""Tests for token storage atomicity, credential security, and auth states."""
from __future__ import annotations

import json
import os
import stat
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from core.fotello import auth
from core.fotello.client import (
    SafeRedirectHandler,
    is_presigned_url,
    redact_sensitive,
)


def test_atomic_token_saving_and_permissions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    tokens_file = tmp_path / ".hdr_exe" / "fotello" / "tokens.json"

    dummy_tokens = {
        "access_token": "secret_acc",
        "id_token": "secret_id",
        "refresh_token": "secret_ref",
    }
    auth.save_tokens(dummy_tokens)

    assert tokens_file.is_file()
    saved = json.loads(tokens_file.read_text(encoding="utf-8"))
    assert saved == dummy_tokens

    # Check POSIX permissions
    if hasattr(os, "chmod") and os.name == "posix":
        file_mode = stat.S_IMODE(tokens_file.stat().st_mode)
        assert file_mode == 0o600
        dir_mode = stat.S_IMODE(tokens_file.parent.stat().st_mode)
        assert dir_mode == 0o700


def test_auth_states_distinction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    # 1. Missing token
    state, msg, _ = auth.check_auth_session()
    assert state == "missing_token"
    assert auth.validate_session() is False

    # 2. Expired token without refresh token
    expired_token = {
        "id_token": "header.eyJleHAiOiAxMDAwfQ.sig",
        "access_token": "expired_acc",
    }
    auth.save_tokens(expired_token)
    state, msg, _ = auth.check_auth_session()
    assert state == "expired"
    assert auth.validate_session() is False

    # 3. Refresh failed (400 Invalid Grant)
    expired_with_refresh = {
        "id_token": "header.eyJleHAiOiAxMDAwfQ.sig",
        "refresh_token": "bad_refresh",
    }
    auth.save_tokens(expired_with_refresh)
    with patch("core.fotello.auth.refresh_firebase_token", side_effect=urllib.error.HTTPError("url", 400, "Bad Request", {}, None)):
        state, msg, _ = auth.check_auth_session()
        assert state == "refresh_failed"
        assert auth.validate_session() is False

    # 4. Network error during refresh
    with patch("core.fotello.auth.refresh_firebase_token", side_effect=urllib.error.URLError("Connection refused")):
        state, msg, _ = auth.check_auth_session()
        assert state == "network_error"
        assert auth.validate_session() is False

    # 5. Valid token (future expiration)
    future_token = {
        "id_token": "header.eyJleHAiOiAyMDAwMDAwMDAwLCAiZW1haWwiOiAidGVzdEBmb3RlbGxvLmNvIn0.sig",
        "access_token": "valid_acc",
    }
    auth.save_tokens(future_token)
    state, msg, toks = auth.check_auth_session()
    assert state == "valid"
    assert auth.validate_session() is True
    status = auth.get_status()
    assert status["connected"] is True
    assert status["email"] == "test@fotello.co"


def test_redact_sensitive_credentials():
    raw_error = "HTTP 401: Failed to call url https://example.com/api?key=fake_secret_key_12345 with Bearer eyJhbGciOiJIUzI1NiJ9.test and Signature=abcdef123456"
    cleaned = redact_sensitive(raw_error)

    assert "fake_secret_key" not in cleaned
    assert "eyJhbGci" not in cleaned
    assert "abcdef123456" not in cleaned
    assert "key=[REDACTED]" in cleaned
    assert "Bearer [REDACTED]" in cleaned
    assert "Signature=[REDACTED]" in cleaned


def test_is_presigned_url():
    assert is_presigned_url("https://storage.googleapis.com/bucket/img.jpg?X-Goog-Signature=abc") is True
    assert is_presigned_url("https://storage.googleapis.com/bucket/img.jpg?Signature=abc&GoogleAccessId=123") is True
    assert is_presigned_url("https://firebasestorage.googleapis.com/v0/b/bucket/o/img.jpg?alt=media&token=xyz") is True
    assert is_presigned_url("https://firebasestorage.googleapis.com/v0/b/bucket/o/img.jpg?alt=media") is False


def test_safe_redirect_strips_authorization_on_different_host():
    handler = SafeRedirectHandler()
    req = urllib.request.Request("https://app.fotello.co/download", headers={"Authorization": "Bearer token_secret"})
    new_req = handler.redirect_request(req, None, 302, "Found", {}, "https://external-storage.cdn.com/file.jpg")

    assert new_req is not None
    assert "Authorization" not in new_req.headers
    assert "authorization" not in new_req.headers
