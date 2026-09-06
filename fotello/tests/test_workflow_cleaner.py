"""Tests for the workflow watermark-cleaning adapter."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from backend.watermark_cleaner.config import WatermarkCleanerConfig
from backend.watermark_workflow.cleaner import clean_output, compare_variant_pair


class TestWorkflowCleaner(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="workflow_cleaner_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @staticmethod
    def _base(width: int = 640, height: int = 480) -> Image.Image:
        image = Image.new("RGB", (width, height), (90, 120, 160))
        draw = ImageDraw.Draw(image)
        for x in range(0, width, 32):
            draw.line((x, 0, x, height), fill=((x * 3) % 255, 80, 130), width=3)
        for y in range(0, height, 32):
            draw.line((0, y, width, y), fill=(90, (y * 2) % 255, 170), width=2)
        return image

    @staticmethod
    def _watermark(
        source: Image.Image,
        corner: str,
        variant: int,
        *,
        overlap: bool = False,
    ) -> Image.Image:
        image = source.copy()
        width, height = image.size
        corner_width = width // 3
        if overlap:
            # This lies in both the legacy 52%-high top and bottom ROIs.  The
            # adapter must still count it as one physical corner.
            top = height // 2 - 35
            bottom = height // 2 + 35
        else:
            top = 0 if corner[0] == "T" else height - height // 3
            bottom = top + height // 3
        left = 0 if corner[1] == "L" else width - corner_width
        right = left + corner_width

        draw = ImageDraw.Draw(image)
        colour = (255, 255, 255) if variant % 2 else (248, 30, 30)
        for y in range(top, bottom, 9):
            draw.line((left, y, right, min(bottom - 1, y + 55)), fill=colour, width=5)
        draw.rectangle((left + 20, top + 25, right - 20, min(bottom - 20, top + 80)), outline=colour, width=5)
        return image

    def _write_pair(self, first: Image.Image, second: Image.Image) -> list[str]:
        first_path = self.temp_dir / "raw01" / "img01.jpg"
        second_path = self.temp_dir / "raw02" / "img01.jpg"
        first_path.parent.mkdir(exist_ok=True)
        second_path.parent.mkdir(exist_ok=True)
        first.save(first_path, quality=96)
        second.save(second_path, quality=96)
        return [str(first_path), str(second_path)]

    def test_same_corner_different_watermark_bytes_needs_variant(self) -> None:
        base = self._base()
        paths = self._write_pair(
            self._watermark(base, "TL", 1),
            self._watermark(base, "TL", 2),
        )

        comparison = compare_variant_pair(*paths)
        self.assertEqual(comparison["status"], "duplicate")
        self.assertFalse(comparison["distinct"])

        result = clean_output("out-1", "img01", paths, self.temp_dir / "job")
        self.assertEqual(result["status"], "need_variant")
        self.assertFalse((self.temp_dir / "job" / "clean" / "img01.png").exists())

    def test_overlap_is_not_counted_as_two_corners(self) -> None:
        base = self._base()
        paths = self._write_pair(
            self._watermark(base, "TL", 1, overlap=True),
            self._watermark(base, "TL", 2, overlap=True),
        )

        comparison = compare_variant_pair(*paths)
        self.assertEqual(comparison["status"], "duplicate")
        self.assertEqual(len(comparison["changed_corners"]), 1)
        self.assertTrue(comparison["metrics"]["roi_overlap_avoided"])

    def test_distinct_corners_are_cleaned_and_published(self) -> None:
        base = self._base()
        paths = self._write_pair(
            self._watermark(base, "BL", 1),
            self._watermark(base, "TR", 2),
        )
        output_root = self.temp_dir / "job"
        result = clean_output("out-2", "img01", paths, output_root)

        self.assertEqual(result["status"], "cleaned")
        output_path = Path(result["output_path"])
        self.assertEqual(output_path, output_root / "clean" / "img01.png")
        self.assertTrue(output_path.exists())
        self.assertTrue(Path(result["report_path"]).exists())
        self.assertTrue(list((output_root / "attempts" / "out-2").iterdir()))

    def test_bad_pair_does_not_hide_later_valid_pair(self) -> None:
        base = self._base()
        first = self.temp_dir / "raw01" / "img01.jpg"
        missing = self.temp_dir / "raw02" / "img01.jpg"
        third = self.temp_dir / "raw03" / "img01.jpg"
        first.parent.mkdir()
        missing.parent.mkdir()
        third.parent.mkdir()
        self._watermark(base, "BL", 1).save(first, quality=96)
        self._watermark(base, "TR", 2).save(third, quality=96)

        # Newest-with-previous first sees the missing file; the older valid
        # pair must still be attempted and published.
        result = clean_output(
            "out-3",
            "img01.png",
            [str(first), str(missing), str(third)],
            self.temp_dir / "job",
        )
        self.assertEqual(result["status"], "cleaned")

    def test_preview_is_review_only_and_stays_in_attempts(self) -> None:
        base = self._base(400, 400)
        paths = self._write_pair(
            self._watermark(base, "BL", 1),
            self._watermark(base, "TR", 2),
        )
        strict = WatermarkCleanerConfig(
            max_seam_mean_discontinuity=0.0,
            max_seam_risk_score=0.0,
        )
        output_root = self.temp_dir / "review-job"
        result = clean_output("out-4", "img01", paths, output_root, config=strict)

        self.assertEqual(result["status"], "needs_review")
        self.assertNotIn("output_path", result)
        self.assertTrue(Path(result["preview_path"]).exists())
        self.assertFalse((output_root / "clean" / "img01.png").exists())

    def test_completed_output_requires_matching_report_and_is_preserved(self) -> None:
        base = self._base()
        paths = self._write_pair(
            self._watermark(base, "BL", 1),
            self._watermark(base, "TR", 2),
        )
        output_root = self.temp_dir / "preserve-job"
        first_result = clean_output("out-5", "img01", paths, output_root)
        self.assertEqual(first_result["status"], "cleaned")
        output_path = Path(first_result["output_path"])
        before = output_path.read_bytes()

        duplicate_paths = self._write_pair(
            self._watermark(base, "TL", 3),
            self._watermark(base, "TL", 4),
        )
        second_result = clean_output("out-5", "img01", duplicate_paths, output_root)

        self.assertEqual(second_result["status"], "cleaned")
        self.assertEqual(output_path.read_bytes(), before)

    def test_real_fixture_pair_is_distinct(self) -> None:
        fixture_paths = sorted(Path("test_wm/01").glob("*.jpg"))
        if len(fixture_paths) < 2:
            self.skipTest("test_wm/01 fixture not found")
        comparison = compare_variant_pair(*fixture_paths[:2])
        self.assertEqual(comparison["status"], "distinct")
        self.assertEqual(comparison["changed_corners"], ["BL", "BR"])


if __name__ == "__main__":
    unittest.main()
