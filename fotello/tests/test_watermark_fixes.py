"""Comprehensive tests for watermark workflow fixes.

Validates:
1. Centralized Vietnamese log formatter for both coordinator (WF1) and manual (WF2).
2. Elimination of raw machine status and English exception prose from UI logs.
3. Dimension mismatch formatting with exact pixel dimensions (e.g. 3840x2558 vs 3840x2560).
4. Attempt-specific timestamp locking and listing naming across retry rounds.
5. Step 03 upload logging accuracy (no success on cancelled or incomplete uploads).
6. UI display_name backward compatibility and Firestore createdAt fallback.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from backend.watermark_workflow.cleaner import format_cleaner_result_vn
from backend.watermark_workflow.coordinator import run_auto
from backend.watermark_workflow.models import (
    attempt_name,
    build_groups,
    new_manifest,
    parse_attempt_name,
)
from backend.watermark_workflow.store import ManifestStore


class TestVietnameseLogFormatter(unittest.TestCase):
    """Ensure all cleaner outcomes produce specific Vietnamese user logs without raw machine strings."""

    def test_cleaned_success_logs_vietnamese(self) -> None:
        result = {"status": "cleaned", "reason": "A distinct watermark-corner pair was cleaned successfully."}
        
        # WF1 (Coordinator with step)
        msg_wf1, level_wf1 = format_cleaner_result_vn("IMG_0001.png", result, with_step=True)
        self.assertEqual(level_wf1, "success")
        self.assertEqual(msg_wf1, "Step 10: Ghép thành công - IMG_0001.png")
        self.assertNotIn("cleaned", msg_wf1.lower())

        # WF2 (Manual without step)
        msg_wf2, level_wf2 = format_cleaner_result_vn("IMG_0001.png", result, with_step=False)
        self.assertEqual(level_wf2, "success")
        self.assertEqual(msg_wf2, "IMG_0001.png: Ghép thành công, đã xóa sạch watermark.")
        self.assertNotIn("cleaned", msg_wf2.lower())

    def test_duplicate_watermark_logs_vietnamese(self) -> None:
        result = {
            "status": "need_variant",
            "reason": "The variants differ in one physical watermark corner (BR); no alternate corner was found.",
            "comparisons": [{"status": "duplicate", "changed_corners": ["BR"]}],
        }

        # WF1
        msg_wf1, level_wf1 = format_cleaner_result_vn("IMG_0002.png", result, with_step=True)
        self.assertEqual(level_wf1, "info")
        self.assertIn("Step 09: Watermark trùng góc - cần lấy thêm biến thể cho IMG_0002.png", msg_wf1)
        self.assertNotIn("need_variant", msg_wf1)
        self.assertNotIn("duplicate", msg_wf1.lower())

        # WF2
        msg_wf2, level_wf2 = format_cleaner_result_vn("IMG_0002.png", result, with_step=False)
        self.assertEqual(level_wf2, "info")
        self.assertIn("Watermark trùng góc", msg_wf2)
        self.assertNotIn("need_variant", msg_wf2)

    def test_uncertain_watermark_logs_vietnamese(self) -> None:
        result = {
            "status": "need_variant",
            "reason": "The changed regions do not provide reliable evidence of two distinct watermark corners.",
            "comparisons": [{"status": "uncertain"}],
        }

        msg_wf1, level_wf1 = format_cleaner_result_vn("IMG_0003.png", result, with_step=True)
        self.assertEqual(level_wf1, "info")
        self.assertIn("Step 09: Chưa đủ cặp góc sạch - cần lấy thêm biến thể cho IMG_0003.png", msg_wf1)
        self.assertNotIn("need_variant", msg_wf1)
        self.assertNotIn("uncertain", msg_wf1.lower())

    def test_seam_warning_logs_vietnamese(self) -> None:
        result = {
            "status": "needs_review",
            "reason": "The cleaner produced a preview or quality warning; the preview was kept for review.",
            "preview_path": "/path/to/preview.png",
        }

        msg_wf1, level_wf1 = format_cleaner_result_vn("IMG_0004.png", result, with_step=True)
        self.assertEqual(level_wf1, "warn")
        self.assertIn("Step 10: Cần kiểm tra đường nối (seam) - IMG_0004.png", msg_wf1)
        self.assertIn("Ảnh xem trước đã tạo nhưng có đường nối/chất lượng chưa tối ưu.", msg_wf1)
        self.assertNotIn("needs_review", msg_wf1)

    def test_dimension_mismatch_shows_exact_dimensions_and_vietnamese_explanation(self) -> None:
        result = {
            "status": "blocked",
            "error_type": "DimensionMismatchError",
            "reason": "Cannot compare variants: DimensionMismatchError: Images have conflicting pixel dimensions: {'img1.jpg': (3840, 2558), 'img2.jpg': (3840, 2560)}",
            "comparisons": [
                {
                    "status": "blocked",
                    "reason": "Cannot compare variants: DimensionMismatchError: Images have conflicting pixel dimensions: {'img1.jpg': (3840, 2558), 'img2.jpg': (3840, 2560)}",
                }
            ],
        }

        # WF1
        msg_wf1, level_wf1 = format_cleaner_result_vn("IMG_0005.png", result, with_step=True)
        self.assertEqual(level_wf1, "error")
        self.assertIn("Step 10: Kích thước ảnh không khớp (3840x2558 vs 3840x2560) - IMG_0005.png: Không thể ghép ảnh.", msg_wf1)
        self.assertNotIn("DimensionMismatchError", msg_wf1)
        self.assertNotIn("blocked", msg_wf1.lower())

        # WF2
        msg_wf2, level_wf2 = format_cleaner_result_vn("IMG_0005.png", result, with_step=False)
        self.assertEqual(level_wf2, "error")
        self.assertIn("IMG_0005.png: Kích thước ảnh không khớp (3840x2558 vs 3840x2560) - Không thể ghép ảnh.", msg_wf2)
        self.assertNotIn("DimensionMismatchError", msg_wf2)

    def test_source_mismatch_logs_vietnamese(self) -> None:
        result = {
            "status": "blocked",
            "error_type": "SourceImageMismatchError",
            "reason": "Source images do not match.",
        }

        msg_wf1, level_wf1 = format_cleaner_result_vn("IMG_0006.png", result, with_step=True)
        self.assertEqual(level_wf1, "error")
        self.assertIn("Ảnh gốc không khớp nội dung", msg_wf1)
        self.assertNotIn("SourceImageMismatchError", msg_wf1)


class TestAttemptTimestampAndNaming(unittest.TestCase):
    """Ensure each attempt locks its own timestamp and chunk listings use attempt-specific times."""

    def test_attempt_name_uses_attempt_created_time_and_supports_chunks(self) -> None:
        t1 = "06 09, 2026 10:00"
        t2 = "06 09, 2026 11:30"
        
        # Single chunk listing
        name_att1 = attempt_name("abc", 1, "fam-1", chunk=1, total_chunks=1, created_at=t1)
        self.assertEqual(name_att1, "abc01 [wm:fam-1:1:1] - 06 09, 2026 10:00")

        # Retry round 2 has later timestamp
        name_att2 = attempt_name("abc", 2, "fam-1", chunk=1, total_chunks=1, created_at=t2)
        self.assertEqual(name_att2, "abc02 [wm:fam-1:2:1] - 06 09, 2026 11:30")

        # Multi-part chunk listing
        name_att1_part2 = attempt_name("abc", 1, "fam-1", chunk=2, total_chunks=3, created_at=t1)
        self.assertEqual(name_att1_part2, "[Part 2] - abc01 [wm:fam-1:1:2] - 06 09, 2026 10:00")

    def test_parse_attempt_name_full_mode_extracts_display_name_and_timestamp(self) -> None:
        raw_name = "[Part 2] - abc01 [wm:fam-1:1:2] - 06 09, 2026 10:00"
        parsed = parse_attempt_name(raw_name, full=True)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["family_id"], "fam-1")
        self.assertEqual(parsed["attempt"], 1)
        self.assertEqual(parsed["chunk"], 2)
        self.assertEqual(parsed["part_chunk"], 2)
        self.assertEqual(parsed["prefix"], "abc")
        self.assertEqual(parsed["display_name"], "[Part 2] - abc01")
        self.assertEqual(parsed["timestamp"], "06 09, 2026 10:00")

        # Standard non-full mode remains backward compatible
        legacy_parsed = parse_attempt_name(raw_name, full=False)
        self.assertEqual(
            legacy_parsed,
            {"family_id": "fam-1", "attempt": 1, "chunk": 2, "prefix": "abc"},
        )


class TestStep03UploadLogging(unittest.TestCase):
    """Ensure Step 03 does not log success if upload is cancelled or missing files."""

    def test_step03_incomplete_upload_logs_error_not_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_path = Path(tmp_dir)
            p1 = temp_path / "img1.jpg"
            p2 = temp_path / "img2.jpg"
            p1.write_bytes(b"content1")
            p2.write_bytes(b"content2")
            groups = build_groups([p1, p2], 1)
            manifest = new_manifest(groups, {}, "team-1", "prefix", temp_path / "out")

            logs: list[tuple[str, str]] = []

            def fake_upload(path: Path) -> str | None:
                if path.name == "img1.jpg":
                    return "up-1"
                return None

            raw_file = temp_path / "raw.jpg"
            raw_file.write_bytes(b"downloaded")
            run_auto(
                manifest,
                upload=fake_upload,
                create_listing=lambda name, chunk: "list-1",
                create_enhance=lambda list_id, ids: "enh-1",
                check_ready=lambda enh_id: True,
                download=lambda enh_id, out_dir, filename, rendition=None: {
                    "path": str(raw_file),
                    "rendition": "edited",
                },
                clean=lambda out_id, out_name, variants, out_dir, is_cancelled=None: {
                    "status": "cleaned",
                    "reason": "Ghép thành công",
                },
                poll_timeout=1,
                poll_interval=lambda *a: 0,
                sleep=lambda delay: None,
                log=lambda msg, level: logs.append((msg, level)),
                is_cancelled=lambda: False,
            )

            step03_logs = [entry for entry in logs if "Step 03" in entry[0]]
            self.assertTrue(step03_logs)
            first_msg, first_level = step03_logs[0]
            self.assertEqual(first_level, "error")
            self.assertIn("không hoàn tất", first_msg)
            self.assertIn("1/2", first_msg)

    def test_step03_cancelled_upload_logs_warn_not_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_path = Path(tmp_dir)
            p1 = temp_path / "img1.jpg"
            p1.write_bytes(b"content1")
            groups = build_groups([p1], 1)
            manifest = new_manifest(groups, {}, "team-1", "prefix", temp_path / "out")

            logs: list[tuple[str, str]] = []

            run_auto(
                manifest,
                upload=lambda path: "up-1",
                create_listing=MagicMock(),
                create_enhance=MagicMock(),
                check_ready=MagicMock(),
                download=MagicMock(),
                clean=MagicMock(),
                poll_timeout=1,
                poll_interval=lambda *a: 0,
                sleep=lambda delay: None,
                log=lambda msg, level: logs.append((msg, level)),
                is_cancelled=lambda: True,
            )

            step03_logs = [entry for entry in logs if "Step 03" in entry[0]]
            for msg, level in step03_logs:
                self.assertNotEqual(level, "success")


class TestListingDownloadTimezoneAndFallback(unittest.TestCase):
    """Verify timezone consistency, roundtrip, and attempt timestamp prioritization in downloads.py."""

    def test_aware_iso_utc_to_vn_name_and_parse_roundtrip(self) -> None:
        from backend.downloads import _parse_listing_name_datetime
        from backend.watermark_workflow.models import VN_TZ

        iso_utc = "2026-09-06T06:30:00+00:00"
        name = attempt_name("abc", 1, "fam-1", created_at=iso_utc)
        self.assertIn("06 09, 2026 13:30", name)

        dt_parsed = _parse_listing_name_datetime(name)
        self.assertIsNotNone(dt_parsed)
        dt_utc = datetime.fromisoformat(iso_utc)
        self.assertEqual(dt_parsed.timestamp(), dt_utc.timestamp())

    def test_manifest_fallback_prioritizes_attempt_timestamp_over_family(self) -> None:
        from backend.downloads import fotello_list_listings
        from backend.auth import FOTELLO_STATE
        from unittest.mock import patch

        fake_manifest = {
            "family_id": "fam-xyz",
            "created_at": "2026-09-06T01:00:00Z",  # Round 1 / Family: 08:00 VN
            "attempts": [
                {"number": 1, "created_at": "2026-09-06T01:00:00Z"},
                {"number": 2, "created_at": "2026-09-06T04:30:00Z"},  # Round 2: 11:30 VN
            ],
        }

        fake_rows = [
            {
                "document": {
                    "name": "projects/p/databases/(default)/documents/listings/list-2",
                    "fields": {
                        "name": {"stringValue": "abc02 [wm:fam-xyz:2:1]"},
                        # Notice: no createdAt field in Firestore doc!
                    },
                }
            }
        ]

        with patch.dict(FOTELLO_STATE, {"connected": True, "team_id": "team-1"}), \
             patch("backend.downloads.fotello_get_tokens", return_value={"access_token": "tok"}), \
             patch("backend.downloads.firestore_run_query", return_value=fake_rows), \
             patch("backend.watermark_workflow.store.ManifestStore.find_by_family", return_value=fake_manifest):
            listings = fotello_list_listings()
            self.assertEqual(len(listings), 1)
            item = listings[0]
            # Should reflect attempt 2 (11:30 VN), NOT attempt 1/family (08:00 VN)
            self.assertEqual(item["created_at"], "2026-09-06 11:30")
            self.assertEqual(item["display_name"], "abc02")
            self.assertEqual(item["attempt"], 2)

    def test_no_now_assigned_to_old_listing_missing_timestamp(self) -> None:
        from backend.downloads import fotello_list_listings
        from backend.auth import FOTELLO_STATE
        from unittest.mock import patch

        fake_rows = [
            {
                "document": {
                    "name": "projects/p/databases/(default)/documents/listings/old-1",
                    "fields": {
                        "name": {"stringValue": "old_listing_without_date"},
                        # No createdAt, no date in name
                    },
                }
            }
        ]

        with patch.dict(FOTELLO_STATE, {"connected": True, "team_id": "team-1"}), \
             patch("backend.downloads.fotello_get_tokens", return_value={"access_token": "tok"}), \
             patch("backend.downloads.firestore_run_query", return_value=fake_rows):
            listings = fotello_list_listings()
            self.assertEqual(len(listings), 1)
            item = listings[0]
            self.assertEqual(item["created_at"], "-")


class TestDirectoryStructureAndCleanup(unittest.TestCase):
    """Test raw directory structure (no part01), cleanup of attempts/reports, and UUID avoidance."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="dir_clean_test_"))
        self.state_dir = self.temp_dir / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.prev_state = os.environ.get("FOTELLO_WORKFLOW_STATE_DIR")
        os.environ["FOTELLO_WORKFLOW_STATE_DIR"] = str(self.state_dir)

    def tearDown(self) -> None:
        if self.prev_state is None:
            os.environ.pop("FOTELLO_WORKFLOW_STATE_DIR", None)
        else:
            os.environ["FOTELLO_WORKFLOW_STATE_DIR"] = self.prev_state
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_wf1_raw_has_no_part01_and_attempts_reports_cleaned(self) -> None:
        from backend.watermark_workflow.coordinator import run_auto
        from backend.watermark_workflow.models import build_groups, new_manifest

        input_dir = self.temp_dir / "inputs"
        input_dir.mkdir(parents=True, exist_ok=True)
        in_path = input_dir / "img01.jpg"
        in_path.write_bytes(b"image")

        output_dir = self.temp_dir / "wf1_out"
        groups = build_groups([in_path], 1)
        manifest = new_manifest(groups, {"bracket_size": 1}, "team-1", "abc", str(output_dir))

        downloaded_raw_dirs: list[Path] = []

        def fake_download(enhance_id, raw_dir, raw_name, rendition=None):
            downloaded_raw_dirs.append(Path(raw_dir))
            path = Path(raw_dir) / raw_name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"variant")
            return {"path": path, "rendition": "edited"}

        def fake_clean(output_id, output_name, variants, out_dir, is_cancelled=None):
            # simulate cleaner creating attempts and reports
            (Path(out_dir) / "attempts" / output_id).mkdir(parents=True, exist_ok=True)
            (Path(out_dir) / "reports").mkdir(parents=True, exist_ok=True)
            (Path(out_dir) / "reports" / f"{Path(output_name).stem}.json").write_text("{}", encoding="utf-8")
            clean_file = Path(out_dir) / "clean" / output_name
            clean_file.parent.mkdir(parents=True, exist_ok=True)
            clean_file.write_bytes(b"cleaned")
            return {"status": "cleaned", "output_path": str(clean_file)}

        result = run_auto(
            manifest,
            upload=lambda path: "upl-1",
            create_listing=lambda name, chunk: "lst-1",
            create_enhance=lambda listing_id, outputs: [{"enhance_id": "enh-1", "output_id": "img0001"}],
            check_ready=lambda eid: True,
            download=fake_download,
            clean=fake_clean,
        )

        self.assertEqual(result["status"], "success")
        self.assertTrue(len(downloaded_raw_dirs) >= 1)
        # raw_dir must be raw/abc01 without part01
        for d in downloaded_raw_dirs:
            self.assertNotIn("part", str(d))
            self.assertTrue(str(d).endswith("raw/abc01") or str(d).endswith("raw/abc02"))

        # attempts and reports must be cleaned up
        self.assertFalse((output_dir / "attempts").exists())
        self.assertFalse((output_dir / "reports").exists())
        # clean and raw must exist
        self.assertTrue((output_dir / "clean").exists())
        self.assertTrue((output_dir / "raw").exists())

    def test_wf2_single_family_has_no_uuid_and_raw_synced_without_part01(self) -> None:
        from backend.watermark_workflow.manual import download_manual_workflow
        from backend.watermark_workflow.models import new_manifest, build_groups
        from backend.watermark_workflow.store import ManifestStore
        from unittest.mock import patch

        source_root = self.temp_dir / "wf1_source"
        source_raw = source_root / "raw" / "abc01" / "img01.jpg"
        source_raw.parent.mkdir(parents=True, exist_ok=True)
        source_raw.write_bytes(b"variant1")

        groups = [
            {
                "output_id": "img0001",
                "output_name": "img01.png",
                "input_paths": [str(source_raw)],
                "input_filenames": ["img01.jpg"],
                "input_fingerprints": {},
                "status": "need_variant",
                "variants": [
                    {"enhance_id": "enh-1", "attempt": 1, "path": str(source_raw), "rendition": "edited"}
                ],
            }
        ]
        manifest = {
            "family_id": "9c6f5d92-d70e-4b1f-8df8-7eebd907cead",
            "team_id": "team-1",
            "prefix": "abc",
            "output_dir": str(source_root),
            "groups": groups,
            "attempts": [
                {
                    "number": 1,
                    "listings": [
                        {
                            "listing_id": "lst-1",
                            "chunk": 1,
                            "enhances": [
                                {"enhance_id": "enh-1", "output_id": "img0001", "name": "img01.jpg"}
                            ],
                        }
                    ],
                }
            ],
        }
        ManifestStore(source_root).save(manifest)

        dest_root = self.temp_dir / "0dd7db08"
        clean_dirs: list[Path] = []

        def fake_clean(output_id, output_name, variants, out_dir, is_cancelled=None):
            clean_dirs.append(Path(out_dir))
            (Path(out_dir) / "attempts").mkdir(parents=True, exist_ok=True)
            (Path(out_dir) / "reports").mkdir(parents=True, exist_ok=True)
            clean_file = Path(out_dir) / "clean" / output_name
            clean_file.parent.mkdir(parents=True, exist_ok=True)
            clean_file.write_bytes(b"clean")
            return {"status": "cleaned", "output_path": str(clean_file)}

        with patch("backend.watermark_workflow.manual.fotello_list_enhances_for_listing", return_value=[{"enhance_id": "enh-1", "id": "enh-1", "name": "img01.jpg"}]), \
             patch("backend.watermark_workflow.manual.clean_output", side_effect=fake_clean):
            res = download_manual_workflow(["lst-1"], dest_root, team_id="team-1")

        # Destination must be dest_root directly, NOT dest_root / <uuid>
        self.assertEqual(clean_dirs[0], dest_root)
        uuid_dir = dest_root / "9c6f5d92-d70e-4b1f-8df8-7eebd907cead"
        self.assertFalse(uuid_dir.exists())

        # dest_root must contain clean/ and raw/ directly
        self.assertTrue((dest_root / "clean").exists())
        self.assertTrue((dest_root / "raw").exists())
        # raw must contain abc01 without part01
        self.assertTrue((dest_root / "raw" / "abc01" / "img01.jpg").exists())
        self.assertFalse((dest_root / "raw" / "abc01" / "part01").exists())

        # attempts and reports must be cleaned up
        self.assertFalse((dest_root / "attempts").exists())
        self.assertFalse((dest_root / "reports").exists())


if __name__ == '__main__':
    unittest.main()
