"""Comprehensive automated test suite for watermark cleaner pipeline."""

from __future__ import annotations
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw

from backend.watermark_cleaner.cli import main as cli_main
from backend.watermark_cleaner.config import WatermarkCleanerConfig
from backend.watermark_cleaner.exceptions import (
    AmbiguousCornerError,
    DimensionMismatchError,
    DuplicateWatermarkError,
    FileSizeDeltaExceededError,
    InputValidationError,
    InsufficientInputsError,
    ModeMismatchError,
    SourceImageMismatchError,
    WatermarkCleanerError,
)
from backend.watermark_cleaner.pipeline import (
    batch_clean,
    clean_case,
    clean_directory,
)
from backend.watermark_cleaner.validator import validate_case_inputs


class TestWatermarkCleaner(unittest.TestCase):
    """Unit and integration tests for watermark cleaning pipeline."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="wm_test_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_synthetic_base_image(self, width: int = 800, height: int = 600) -> Image.Image:
        """Create a synthetic photographic-like image with rich colors and textures."""
        img = Image.new("RGB", (width, height), (120, 140, 180))
        draw = ImageDraw.Draw(img)
        # Add background shapes and gradients
        for i in range(0, width, 40):
            draw.rectangle([i, 0, i + 20, height], fill=(i % 255, (i * 2) % 255, (i * 3) % 255))
        for j in range(0, height, 40):
            draw.line([(0, j), (width, j)], fill=((j * 3) % 255, (j * 2) % 255, j % 255), width=2)
        return img

    def _stamp_watermark(self, img: Image.Image, corner: str, text: str = "WATERMARK") -> Image.Image:
        """Stamp an artificial watermark (text + diagonal lines) strictly within a specific corner."""
        wm_img = img.copy()
        w, h = wm_img.size
        cw, ch = w // 4, h // 4

        if corner == "TL":
            box = (0, 0, cw, ch)
        elif corner == "TR":
            box = (w - cw, 0, w, ch)
        elif corner == "BL":
            box = (0, h - ch, cw, h)
        elif corner == "BR":
            box = (w - cw, h - ch, w, h)
        else:
            raise ValueError(f"Unknown corner: {corner}")

        patch = wm_img.crop(box)
        draw = ImageDraw.Draw(patch)

        # Draw dense diagonal hatch lines within corner bounds
        for offset in range(-ch, cw, 15):
            draw.line([(offset, 0), (offset + ch, ch)], fill=(255, 255, 255), width=3)

        # Draw text inside corner patch
        draw.text((10, ch // 2), text, fill=(255, 255, 255))
        wm_img.paste(patch, box)
        return wm_img

    def test_synthetic_success(self) -> None:
        """Test successful reconstruction of synthetic image from copies with distinct watermarked corners."""
        case_dir = self.temp_dir / "case_synth"
        case_dir.mkdir(parents=True)

        base_clean = self._create_synthetic_base_image(800, 600)
        im_bl = self._stamp_watermark(base_clean, "BL", "TEST_FOTELLO_1")
        im_tr = self._stamp_watermark(base_clean, "TR", "TEST_FOTELLO_2")

        path1 = case_dir / "img1.png"
        path2 = case_dir / "img2.png"
        im_bl.save(path1, "PNG")
        im_tr.save(path2, "PNG")

        out_dir = self.temp_dir / "out"
        result = clean_directory(case_dir, output_dir=out_dir)

        self.assertTrue(result.success)
        self.assertIsNotNone(result.output_path)
        self.assertEqual(result.dimensions, (800, 600))
        self.assertEqual(result.mode, "RGB")

        # Verify output exists and exactly matches the clean ground truth
        with Image.open(result.output_path) as cleaned_img:
            self.assertEqual(cleaned_img.size, (800, 600))
            self.assertEqual(cleaned_img.mode, "RGB")
            diff = ImageChops.difference(cleaned_img, base_clean)
            bbox = diff.getbbox()
            self.assertIsNone(bbox, f"Clean image should have 0 diff with ground truth, but diff at {bbox}")

    def test_dimension_mismatch(self) -> None:
        """Test that images with differing dimensions raise DimensionMismatchError."""
        case_dir = self.temp_dir / "case_dim_mismatch"
        case_dir.mkdir(parents=True)

        im1 = Image.new("RGB", (800, 600), (100, 100, 100))
        im2 = Image.new("RGB", (800, 601), (100, 100, 100))

        p1 = case_dir / "img1.png"
        p2 = case_dir / "img2.png"
        im1.save(p1)
        im2.save(p2)

        with self.assertRaises(DimensionMismatchError):
            validate_case_inputs([p1, p2])

        result = clean_directory(case_dir)
        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "DimensionMismatchError")
        self.assertIsNone(result.output_path)

    def test_mode_mismatch(self) -> None:
        """Test that images with differing color modes raise ModeMismatchError."""
        case_dir = self.temp_dir / "case_mode_mismatch"
        case_dir.mkdir(parents=True)

        im1 = Image.new("RGB", (400, 300), (100, 100, 100))
        im2 = Image.new("RGBA", (400, 300), (100, 100, 100, 255))

        p1 = case_dir / "img1.png"
        p2 = case_dir / "img2.png"
        im1.save(p1)
        im2.save(p2)

        with self.assertRaises(ModeMismatchError):
            validate_case_inputs([p1, p2])

        result = clean_directory(case_dir)
        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "ModeMismatchError")
        self.assertIsNone(result.output_path)

    def test_unrelated_same_size_images_are_rejected(self) -> None:
        """Same dimensions are insufficient when central source content differs."""
        case_dir = self.temp_dir / "case_unrelated"
        case_dir.mkdir(parents=True)
        first = self._create_synthetic_base_image(400, 300)
        second = Image.new("RGB", (400, 300), (240, 20, 30))
        first.save(case_dir / "first.png")
        second.save(case_dir / "second.png")

        result = clean_directory(case_dir)
        self.assertFalse(result.success)
        self.assertEqual(result.error_type, SourceImageMismatchError.__name__)
        self.assertIsNone(result.output_path)

    def test_failed_rerun_removes_stale_output(self) -> None:
        """A failed run must not leave an older image looking like a new result."""
        case_dir = self.temp_dir / "case_stale"
        case_dir.mkdir(parents=True)
        base = self._create_synthetic_base_image(400, 300)
        watermarked = self._stamp_watermark(base, "TR")
        watermarked.save(case_dir / "first.png")
        watermarked.save(case_dir / "second.png")
        stale = case_dir / "clean_result.png"
        base.save(stale)

        result = clean_directory(case_dir)
        self.assertFalse(result.success)
        self.assertFalse(stale.exists())

    def test_identical_duplicate_watermark(self) -> None:
        """Test that identical watermarked images fail-safe without generating pseudo-clean image."""
        case_dir = self.temp_dir / "case_dup"
        case_dir.mkdir(parents=True)

        base = self._create_synthetic_base_image(600, 400)
        im_wm = self._stamp_watermark(base, "TR", "IDENTICAL_WM")

        p1 = case_dir / "img1.png"
        p2 = case_dir / "img2.png"
        im_wm.save(p1)
        im_wm.save(p2)

        result = clean_directory(case_dir)
        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "DuplicateWatermarkError")
        self.assertIsNone(result.output_path)

    def test_insufficient_inputs(self) -> None:
        """Test that fewer than 2 images raises InsufficientInputsError."""
        case_dir = self.temp_dir / "case_single"
        case_dir.mkdir(parents=True)

        im1 = Image.new("RGB", (400, 300))
        im1.save(case_dir / "only_one.png")

        result = clean_directory(case_dir)
        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "InsufficientInputsError")

    def test_file_size_delta_tolerance(self) -> None:
        """Test that byte-size delta exceeding configured threshold raises FileSizeDeltaExceededError."""
        case_dir = self.temp_dir / "case_filesize"
        case_dir.mkdir(parents=True)

        p1 = case_dir / "img1.jpg"
        p2 = case_dir / "img2.jpg"

        # Create two identical small images
        im = Image.new("RGB", (200, 200), (50, 50, 50))
        im.save(p1, "JPEG")
        im.save(p2, "JPEG")

        # Pad p2 with 5000 bytes
        with open(p2, "ab") as f:
            f.write(b"\x00" * 5000)

        # Config with threshold 1000 bytes
        strict_config = WatermarkCleanerConfig(max_file_size_delta_bytes=1000)

        with self.assertRaises(FileSizeDeltaExceededError):
            validate_case_inputs([p1, p2], config=strict_config)

        # But with generous threshold 10000 bytes, it should validate successfully
        generous_config = WatermarkCleanerConfig(max_file_size_delta_bytes=10000)
        validated = validate_case_inputs([p1, p2], config=generous_config)
        self.assertEqual(len(validated.image_paths), 2)

    def test_preserve_dimensions_and_lossless(self) -> None:
        """Test that compositing preserves exact original pixel dimensions and saves lossless PNG."""
        case_dir = self.temp_dir / "case_preserve"
        case_dir.mkdir(parents=True)

        width, height = 1024, 768
        base = self._create_synthetic_base_image(width, height)
        im1 = self._stamp_watermark(base, "TL")
        im2 = self._stamp_watermark(base, "BR")

        p1 = case_dir / "img1.png"
        p2 = case_dir / "img2.png"
        im1.save(p1)
        im2.save(p2)

        result = clean_directory(case_dir)
        self.assertTrue(result.success)
        self.assertEqual(result.dimensions, (width, height))

        with Image.open(result.output_path) as out_img:
            self.assertEqual(out_img.size, (width, height))
            self.assertEqual(out_img.format, "PNG")

    def test_real_case_01(self) -> None:
        """Integration test on real fixture test_wm/01 (4096x2726, BL & BR watermarks)."""
        fixture_dir = Path("test_wm/01")
        if not fixture_dir.exists():
            self.skipTest("Fixture test_wm/01 not found")

        out_dir = self.temp_dir / "real_01"
        result = clean_directory(fixture_dir, output_dir=out_dir)

        self.assertTrue(result.success, f"Case 01 should succeed: {result.error_message}")
        self.assertEqual(result.dimensions, (4096, 2726))
        self.assertEqual(result.mode, "RGB")
        self.assertIsNotNone(result.output_path)
        self.assertTrue(Path(result.output_path).exists())

        with Image.open(result.output_path) as out_img:
            self.assertEqual(out_img.size, (4096, 2726))
            self.assertEqual(out_img.format, "PNG")

    def test_real_case_02(self) -> None:
        """Integration test on real fixture test_wm/02 (identical images with same watermark)."""
        fixture_dir = Path("test_wm/02")
        if not fixture_dir.exists():
            self.skipTest("Fixture test_wm/02 not found")

        out_dir = self.temp_dir / "real_02"
        result = clean_directory(fixture_dir, output_dir=out_dir)

        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "DuplicateWatermarkError")
        self.assertIsNone(result.output_path)

        # Verify no clean image file was generated in the output directory
        case_out_dir = out_dir / "02"
        image_files = [f for f in case_out_dir.iterdir() if f.suffix.lower() == ".png"]
        self.assertEqual(len(image_files), 0, "No clean image should be generated for Case 02")

    def test_batch_cli_execution(self) -> None:
        """Test CLI batch execution on test_wm with overrides and exit code."""
        out_dir = self.temp_dir / "cli_out"
        code = cli_main([
            "test_wm",
            "--output-dir", str(out_dir),
            "--corner-width-fraction", "0.35",
            "--corner-height-fraction", "0.52",
            "--max-file-size-delta", "1048576",
            "--jpeg-noise-threshold", "20.0",
            "--quiet",
        ])

        # Batch contains case 02 which fails, so exit code must be 1
        self.assertEqual(code, 1)

        # Case 01 should have clean output and report
        case1_out = out_dir / "01"
        self.assertTrue((case1_out / "clean_result.png").exists())
        self.assertTrue((case1_out / "report.json").exists())

        # Case 02 should have report but NO clean output image
        case2_out = out_dir / "02"
        self.assertTrue((case2_out / "report.json").exists())
        self.assertFalse((case2_out / "clean_result.png").exists())

        # Check case 01 report
        rep1 = json.loads((case1_out / "report.json").read_text(encoding="utf-8"))
        self.assertTrue(rep1["success"])
        self.assertEqual(rep1["dimensions"], [4096, 2726])
        self.assertEqual(rep1["completion_percentage"], 100.0)
        self.assertGreater(rep1["quality_score_percentage"], 0.0)

        # Check case 02 report
        rep2 = json.loads((case2_out / "report.json").read_text(encoding="utf-8"))
        self.assertFalse(rep2["success"])
        self.assertEqual(rep2["error_type"], "DuplicateWatermarkError")

    def test_three_images_synthetic(self) -> None:
        """Test consensus-based cleaning with 3 copies watermarked in different corners."""
        case_dir = self.temp_dir / "case_3_images"
        case_dir.mkdir(parents=True)

        base_clean = self._create_synthetic_base_image(600, 600)
        im_tl = self._stamp_watermark(base_clean, "TL", "WM_TL")
        im_tr = self._stamp_watermark(base_clean, "TR", "WM_TR")
        im_br = self._stamp_watermark(base_clean, "BR", "WM_BR")

        im_tl.save(case_dir / "im1.png")
        im_tr.save(case_dir / "im2.png")
        im_br.save(case_dir / "im3.png")

        out_dir = self.temp_dir / "out_3"
        result = clean_directory(case_dir, output_dir=out_dir)

        self.assertTrue(result.success)
        self.assertEqual(len(result.source_images), 3)

        with Image.open(result.output_path) as cleaned:
            diff = ImageChops.difference(cleaned, base_clean)
            self.assertIsNone(diff.getbbox())

    def test_corrupted_image(self) -> None:
        """Test that invalid/corrupted image file raises CorruptedImageError."""
        case_dir = self.temp_dir / "case_corrupt"
        case_dir.mkdir(parents=True)

        im = Image.new("RGB", (200, 200))
        im.save(case_dir / "good.png")

        with open(case_dir / "bad.png", "wb") as f:
            f.write(b"NOT_A_VALID_IMAGE_DATA")

        result = clean_directory(case_dir)
        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "CorruptedImageError")

    def test_full_roi_and_seam_metrics_in_report(self) -> None:
        """Test that replacement covers the FULL corner ROI and report contains full_roi, clean_source, and seam_metrics."""
        case_dir = self.temp_dir / "case_full_roi"
        case_dir.mkdir(parents=True)

        w, h = 800, 600
        base = self._create_synthetic_base_image(w, h)
        im1 = self._stamp_watermark(base, "BL")
        im2 = self._stamp_watermark(base, "TR")

        im1.save(case_dir / "im1.png")
        im2.save(case_dir / "im2.png")

        result = clean_directory(case_dir)
        self.assertTrue(result.success)
        self.assertGreater(len(result.regions_replaced), 0)

        cw, ch = int(w * 0.35), h - int(h * 0.48)
        for reg in result.regions_replaced:
            # Verify FULL ROI was used
            self.assertIn("full_roi", reg)
            self.assertIn("clean_source", reg)
            self.assertIn("seam_metrics", reg)

            box = reg["box"]
            full_roi = reg["full_roi"]
            self.assertEqual(box, full_roi)
            self.assertEqual(full_roi[2] - full_roi[0], cw)
            self.assertEqual(full_roi[3] - full_roi[1], ch)

            # Verify seam metrics
            sm = reg["seam_metrics"]
            self.assertIn("horizontal_border", sm)
            self.assertIn("vertical_border", sm)
            self.assertIn("overall_mean_discontinuity", sm)
            self.assertIn("overall_risk_score", sm)
            self.assertTrue(sm["is_acceptable"])

        # Also verify report file written to disk contains them
        rep = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
        for reg in rep["regions_replaced"]:
            self.assertIn("full_roi", reg)
            self.assertIn("clean_source", reg)
            self.assertIn("seam_metrics", reg)

    def test_seam_discontinuity_saves_scored_preview(self) -> None:
        """A replaceable result is saved even when seam risk needs visual review."""
        case_dir = self.temp_dir / "case_seam_fail"
        case_dir.mkdir(parents=True)

        w, h = 400, 400
        cw = int(w * 0.35)
        bottom_y = int(h * 0.48)
        base1 = Image.new("RGB", (w, h), (100, 100, 100))
        base2 = Image.new("RGB", (w, h), (100, 100, 100))

        # Stamp watermarks
        im1 = self._stamp_watermark(base1, "BL")
        im2 = self._stamp_watermark(base2, "TR")

        # Deliberately paint high contrast pixels immediately outside the BL
        # horizontal seam, simulating source mismatch at the splice boundary.
        draw2 = ImageDraw.Draw(im2)
        draw2.line([(0, bottom_y - 1), (cw - 1, bottom_y - 1)], fill=(255, 0, 0), width=1)

        im1.save(case_dir / "im1.png")
        im2.save(case_dir / "im2.png")

        # Strict seam threshold
        strict_config = WatermarkCleanerConfig(
            max_seam_mean_discontinuity=5.0,
            max_seam_risk_score=0.05,
        )

        result = clean_directory(case_dir, config=strict_config)
        self.assertTrue(result.success)
        self.assertEqual(result.status, "preview")
        self.assertEqual(result.completion_percentage, 100.0)
        self.assertGreaterEqual(result.quality_score_percentage, 0.0)
        self.assertLessEqual(result.quality_score_percentage, 100.0)
        self.assertTrue(Path(result.output_path).exists())
        self.assertTrue(result.warnings)

    def test_measured_safe_roi_coordinates(self) -> None:
        """Default ROIs match the measured 35%-wide, 52%-high edge boxes."""
        from backend.watermark_cleaner.detector import compute_corner_regions
        from backend.watermark_cleaner.models import CornerName

        w, h = 4096, 2726
        regions = compute_corner_regions(w, h, WatermarkCleanerConfig())
        self.assertEqual(regions[CornerName.BL].box, (0, int(h * 0.48), int(w * 0.35), h))
        self.assertEqual(
            regions[CornerName.BR].box,
            (w - int(w * 0.35), int(h * 0.48), w, h),
        )


if __name__ == "__main__":
    unittest.main()
