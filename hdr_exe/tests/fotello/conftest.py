"""Shared fixtures for Fotello unit tests."""
from __future__ import annotations

import pytest

from core.shared.licensing.models import LicenseResult


@pytest.fixture(autouse=True)
def mock_fotello_licensing(monkeypatch: pytest.MonkeyPatch):
    """Mặc định cho phép xác thực bản quyền hợp lệ cho các test nghiệp vụ Fotello."""
    monkeypatch.setattr(
        "core.shared.licensing.check",
        lambda engine, **kwargs: LicenseResult(valid=True, engine=engine, level="plus"),
    )
    monkeypatch.setattr(
        "core.fotello.download.check",
        lambda engine, **kwargs: LicenseResult(valid=True, engine=engine, level="plus"),
    )
    monkeypatch.setattr(
        "core.shared.licensing.require_access",
        lambda engine, mode, **kwargs: LicenseResult(valid=True, engine=engine, level="plus"),
    )
    monkeypatch.setattr(
        "core.shared.jobs.runner.require_access",
        lambda engine, mode, **kwargs: LicenseResult(valid=True, engine=engine, level="plus"),
    )
