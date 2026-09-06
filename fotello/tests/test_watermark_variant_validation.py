"""Copies in different attempts deliberately share a basename."""
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from backend.watermark_cleaner.config import WatermarkCleanerConfig
from backend.watermark_cleaner.exceptions import (
    DimensionMismatchError, ModeMismatchError, FileSizeDeltaExceededError,
)
from backend.watermark_cleaner.validator import validate_case_inputs


class SameNameValidationTests(unittest.TestCase):
    def test_same_basename_does_not_hide_dimension_or_mode_mismatch(self):
        with tempfile.TemporaryDirectory() as root:
            first = Path(root) / "01" / "image.png"
            second = Path(root) / "02" / "image.png"
            first.parent.mkdir()
            second.parent.mkdir()
            Image.new("RGB", (30, 30)).save(first)
            Image.new("RGB", (31, 30)).save(second)
            with self.assertRaises(DimensionMismatchError):
                validate_case_inputs([first, second])
            Image.new("RGBA", (30, 30)).save(second)
            with self.assertRaises(ModeMismatchError):
                validate_case_inputs([first, second])

    def test_same_basename_does_not_hide_size_delta(self):
        with tempfile.TemporaryDirectory() as root:
            first = Path(root) / "01" / "image.png"
            second = Path(root) / "02" / "image.png"
            first.parent.mkdir()
            second.parent.mkdir()
            Image.new("RGB", (30, 30)).save(first)
            second.write_bytes(first.read_bytes() + b"\0" * 100)
            with self.assertRaises(FileSizeDeltaExceededError):
                validate_case_inputs([first, second], config=WatermarkCleanerConfig(max_file_size_delta_bytes=50))
