"""Tests for Fotello bracket grouping and input integrity validation."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.fotello.brackets import (
    BracketValidationError,
    build_bracket_outputs,
    natural_sort_key,
    verify_inputs_integrity,
)


def _create_sample_files(tmp_dir: Path, filenames: list[str]) -> list[Path]:
    created = []
    for fname in filenames:
        p = tmp_dir / fname
        p.write_bytes(f"dummy content for {fname}".encode("utf-8"))
        created.append(p)
    return created


def test_natural_sorting():
    filenames = ["img10.jpg", "img2.jpg", "img1.jpg", "img20.jpg"]
    paths = [Path(f) for f in filenames]
    paths.sort(key=natural_sort_key)
    assert [p.name for p in paths] == ["img1.jpg", "img2.jpg", "img10.jpg", "img20.jpg"]


def test_bracket_grouping_success(tmp_path: Path):
    # 6 images with bracket_size = 3 -> 2 outputs
    filenames = ["DSC_0001.jpg", "DSC_0002.jpg", "DSC_0003.jpg", "DSC_0004.jpg", "DSC_0005.jpg", "DSC_0006.jpg"]
    files = _create_sample_files(tmp_path, filenames)

    outputs = build_bracket_outputs(input_paths=[tmp_path], bracket_size=3)
    assert len(outputs) == 2
    assert len(outputs[0].input_files) == 3
    assert len(outputs[1].input_files) == 3
    assert [p.name for p in outputs[0].input_files] == ["DSC_0001.jpg", "DSC_0002.jpg", "DSC_0003.jpg"]
    assert [p.name for p in outputs[1].input_files] == ["DSC_0004.jpg", "DSC_0005.jpg", "DSC_0006.jpg"]


def test_bracket_grouping_incomplete_rejected(tmp_path: Path):
    # 5 images with bracket_size = 3 -> fails because 5 % 3 != 0
    filenames = ["DSC_0001.jpg", "DSC_0002.jpg", "DSC_0003.jpg", "DSC_0004.jpg", "DSC_0005.jpg"]
    _create_sample_files(tmp_path, filenames)

    with pytest.raises(BracketValidationError) as exc_info:
        build_bracket_outputs(input_paths=[tmp_path], bracket_size=3)
    assert "không chia hết cho bracket_size (3)" in str(exc_info.value)


def test_bracket_invalid_bracket_size(tmp_path: Path):
    _create_sample_files(tmp_path, ["img1.jpg", "img2.jpg"])
    with pytest.raises(BracketValidationError, match="bracket_size không hợp lệ: 2"):
        build_bracket_outputs(input_paths=[tmp_path], bracket_size=2)


def test_input_integrity_detects_modified_or_deleted_file(tmp_path: Path):
    files = _create_sample_files(tmp_path, ["a.jpg", "b.jpg"])
    outputs = build_bracket_outputs(input_paths=files, bracket_size=1)

    # All files intact -> verification passes
    verify_inputs_integrity(outputs)

    # Modify file a.jpg
    files[0].write_bytes(b"modified new content")
    with pytest.raises(BracketValidationError, match="đã bị sửa đổi|đã bị thay đổi"):
        verify_inputs_integrity([outputs[0]])

    # Delete file b.jpg
    files[1].unlink()
    with pytest.raises(BracketValidationError, match="đã bị xóa hoặc không tìm thấy"):
        verify_inputs_integrity([outputs[1]])
