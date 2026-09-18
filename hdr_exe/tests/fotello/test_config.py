"""Tests for Fotello configuration validation, schema enforcement, and isolation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.shared.config import (
    ConfigurationError,
    get_app_dir,
    load_app_config,
)
from core.fotello.config import (
    FotelloConfig,
    load_fotello_config,
    migrate_fotello_config_if_needed,
    serialize_fotello_config,
    validate_fotello_config_dict,
)


def test_config_missing_file_uses_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # File config.json does not exist
    app_cfg = load_app_config()
    assert app_cfg == {}
    fotello_cfg = load_fotello_config()
    assert isinstance(fotello_cfg, FotelloConfig)
    assert fotello_cfg.endpoints.firebase_project_id == "real-estate-firebase-4109e"
    assert fotello_cfg.jobs.max_outputs_per_job == 20
    assert fotello_cfg.jobs.max_jobs_per_batch == 3
    assert fotello_cfg.preferences.bracket_size == 1
    assert fotello_cfg.upload.max_workers == 4


def test_config_invalid_json_raises_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cfg_file = tmp_path / ".hdr_exe" / "fotello" / "config.json"
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text("{invalid json here", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="không đúng định dạng JSON"):
        load_fotello_config()


def test_config_non_dict_root_raises_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cfg_file = tmp_path / ".hdr_exe" / "config.json"
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text("[\"item1\", \"item2\"]", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="phải là một JSON object"):
        load_app_config()


def test_config_unknown_root_key_raises_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cfg_file = tmp_path / ".hdr_exe" / "config.json"
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(json.dumps({"unknown_engine": {}}), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="chứa khóa không hợp lệ"):
        load_app_config()


def test_config_fotello_unknown_section_raises_error():
    with pytest.raises(ConfigurationError, match="mục cấu hình không hợp lệ 'unknown'"):
        validate_fotello_config_dict({"unknown": {}})


def test_config_fotello_validation():
    # jobs: max_outputs_per_job must be > 0
    with pytest.raises(ConfigurationError, match="max_outputs_per_job phải là số nguyên > 0"):
        validate_fotello_config_dict({"jobs": {"max_outputs_per_job": 0}})

    # preferences: bracket_size must be 1, 3, or 5
    with pytest.raises(ConfigurationError, match="bracket_size phải là một trong"):
        validate_fotello_config_dict({"preferences": {"bracket_size": 2}})

    # upload: max_workers must be > 0
    with pytest.raises(ConfigurationError, match="max_workers phải là số nguyên > 0"):
        validate_fotello_config_dict({"upload": {"max_workers": -1}})

    # polling: timeout_seconds must be > 0
    with pytest.raises(ConfigurationError, match="timeout_seconds phải > 0"):
        validate_fotello_config_dict({"polling": {"timeout_seconds": -10}})


def test_one_time_migration_from_root_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HDR_EXE_HOME", str(tmp_path))
    root_cfg_file = tmp_path / "config.json"
    fotello_cfg_file = tmp_path / "fotello" / "config.json"

    # Write root config with legacy fotello entry and shared entry
    root_cfg_file.write_text(
        json.dumps({
            "shared": {"timeout_seconds": 45},
            "fotello": {
                "firebase_project_id": "migrated-project-123",
                "max_download_workers": 8,
            },
        }),
        encoding="utf-8",
    )

    migrate_fotello_config_if_needed(app_dir=tmp_path)

    # Verify fotello key was removed from root config
    new_root = json.loads(root_cfg_file.read_text(encoding="utf-8"))
    assert "fotello" not in new_root
    assert new_root.get("shared") == {"timeout_seconds": 45}

    # Verify fotello config was created with migrated values
    assert fotello_cfg_file.is_file()
    fotello_cfg = load_fotello_config(fotello_cfg_file)
    assert fotello_cfg.endpoints.firebase_project_id == "migrated-project-123"
    assert fotello_cfg.download.max_download_workers == 8


def test_defaults_round_trip_and_partial_override(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"network": {"max_retries": 0}, "download": {"chunk_size_bytes": 8192}}))
    cfg = load_fotello_config(path)
    assert cfg.network.max_retries == 0
    assert cfg.download.chunk_size_bytes == 8192
    assert cfg.jobs == FotelloConfig().jobs
    path.write_text(json.dumps(serialize_fotello_config(cfg)))
    assert load_fotello_config(path) == cfg


@pytest.mark.parametrize("raw", [
    {"endpoints": {"obsolete_url": "https://example.invalid"}},
    {"download": {"max_file_bytes": True}},
    {"preferences": {"contrast_style": 123}},
    {"preferences": {"bracket_size": True}},
    {"network": {"retry_delay_seconds": float("nan")}},
    {"browser": {"port": 65536}},
])
def test_invalid_nested_settings_are_not_silently_coerced(raw):
    with pytest.raises(ConfigurationError):
        validate_fotello_config_dict(raw)


def test_project_override_derives_matching_firestore_url():
    cfg = FotelloConfig(**validate_fotello_config_dict({
        "endpoints": {"firebase_project_id": "fixture-project"},
    }))
    assert "/projects/fixture-project/" in cfg.endpoints.firestore_url
