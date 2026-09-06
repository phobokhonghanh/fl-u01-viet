from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.watermark_workflow import (
    ManifestStore,
    WorkflowValidationError,
    attempt_name,
    build_groups,
    new_manifest,
    parse_attempt_name,
    summary,
)


class TestWorkflowModels(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="workflow_models_"))
        self.state_dir = self.temp_dir / "state"
        self.env = patch.dict(
            os.environ,
            {"FOTELLO_WORKFLOW_STATE_DIR": str(self.state_dir)},
            clear=False,
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_groups_are_natural_sorted_and_bracket_stable(self) -> None:
        images = [
            Path("img10.jpg"),
            Path("img2.jpg"),
            Path("img1.jpg"),
            Path("img3.jpg"),
            Path("img5.jpg"),
            Path("img4.jpg"),
        ]
        groups = build_groups(images, 3)
        self.assertEqual(
            [group["output_id"] for group in groups],
            ["img0001", "img0002"],
        )
        self.assertEqual(
            groups[0]["input_filenames"],
            ["img1.jpg", "img2.jpg", "img3.jpg"],
        )
        self.assertEqual(
            groups[1]["input_filenames"],
            ["img4.jpg", "img5.jpg", "img10.jpg"],
        )
        self.assertEqual(groups[0]["output_name"], "img1.png")
        self.assertEqual(groups[0]["status"], "need_variant")
        self.assertEqual(groups[0]["variants"], [])
        self.assertEqual(
            set(groups[0]["input_fingerprints"]),
            {
                str(Path("img1.jpg").resolve()),
                str(Path("img2.jpg").resolve()),
                str(Path("img3.jpg").resolve()),
            },
        )

    def test_invalid_brackets_and_incomplete_groups_are_rejected(self) -> None:
        with self.assertRaises(WorkflowValidationError):
            build_groups([Path("a.jpg")], 2)
        with self.assertRaises(WorkflowValidationError):
            build_groups([Path("a.jpg")], 7)

    def test_duplicate_sanitized_output_names_are_rejected(self) -> None:
        with self.assertRaises(WorkflowValidationError):
            build_groups([Path("a?one.jpg"), Path("a_one.jpg")], 1)

    def test_output_name_preserves_valid_spaces_and_parentheses(self) -> None:
        groups = build_groups([Path("Family Photo (Front) 01.jpg")], 1)
        self.assertEqual(groups[0]["output_name"], "Family Photo (Front) 01.png")

    def test_output_name_handles_reserved_and_trailing_windows_characters(self) -> None:
        groups = build_groups([Path("CON. .jpg")], 1)
        self.assertEqual(groups[0]["output_name"], "_CON.png")

    def test_manifest_copies_preferences_and_sanitizes_prefix(self) -> None:
        groups = build_groups([Path("img1.jpg"), Path("img2.jpg")], 1)
        preferences = {"bracket_size": 1, "nested": {"enabled": True}}
        manifest = new_manifest(
            groups,
            preferences,
            "team-a",
            "abc01",
            self.temp_dir / "out",
        )
        self.assertEqual(manifest["bracket_size"], 1)
        self.assertEqual(manifest["prefix"], "abc")
        self.assertEqual(manifest["requested_prefix"], "abc01")
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(manifest["attempts"], [])
        preferences["nested"]["enabled"] = False
        self.assertTrue(manifest["preferences"]["nested"]["enabled"])

    def test_attempt_name_round_trip(self) -> None:
        name = attempt_name("abc", 3, "family-123", 4)
        self.assertEqual(name, "abc03 [wm:family-123:3:4]")
        self.assertEqual(
            parse_attempt_name(name),
            {
                "family_id": "family-123",
                "attempt": 3,
                "chunk": 4,
                "prefix": "abc",
            },
        )
        self.assertIsNone(parse_attempt_name("abc03 [wm:not-enough-fields]"))

    def test_store_is_atomic_and_find_by_listing_filters_team(self) -> None:
        groups = build_groups([Path("img1.jpg"), Path("img2.jpg")], 1)
        manifest = new_manifest(
            groups,
            {},
            "team-a",
            "abc",
            self.temp_dir / "out",
        )
        manifest["attempts"] = [
            {
                "number": 1,
                "listings": [
                    {
                        "chunk": 1,
                        "status": "submission_unknown",
                        "enhances": [
                            {
                                "output_id": "img0001",
                                "status": "submission_unknown",
                            }
                        ],
                    }
                ],
            }
        ]
        store = ManifestStore(manifest["output_dir"])
        store.save(manifest)
        manifest["attempts"][0]["listings"][0].update(
            listing_id="listing-1",
            status="created",
        )
        manifest["attempts"][0]["listings"][0]["enhances"][0].update(
            enhance_id="enhance-1",
            status="pending",
        )
        store.save(manifest)
        self.assertEqual(store.load(), manifest)
        found = ManifestStore.find_by_listing("listing-1", "team-a")
        self.assertIsNotNone(found)
        self.assertEqual(
            len(found["attempts"][0]["listings"]),
            1,
        )
        self.assertEqual(found["attempts"][0]["listings"][0]["listing_id"], "listing-1")
        self.assertEqual(len(found["attempts"][0]["listings"][0]["enhances"]), 1)
        self.assertEqual(
            found["attempts"][0]["listings"][0]["enhances"][0]["enhance_id"],
            "enhance-1",
        )
        self.assertIsNone(ManifestStore.find_by_listing("listing-1", "team-b"))
        self.assertTrue(store.manifest_path.exists())
        self.assertTrue(store.registry_path.exists())

    def test_summary_counts_variants_and_requires_cleaned_outputs(self) -> None:
        groups = build_groups(
            [Path("img1.jpg"), Path("img2.jpg"), Path("img3.jpg")],
            1,
        )
        groups[0]["variants"] = [
            {"path": "/tmp/a.jpg"},
            {"status": "downloaded"},
        ]
        groups[0]["status"] = "cleaned"
        groups[1]["variants"] = [{"status": "downloaded"}]
        groups[1]["status"] = "preview"
        groups[2]["status"] = "blocked"
        manifest = new_manifest(
            groups,
            {},
            "team-a",
            "abc",
            self.temp_dir / "out",
        )
        manifest["attempts"] = [{"number": 3}]
        result = summary(manifest)
        self.assertEqual(result["target_count"], 3)
        self.assertEqual(result["downloaded_count"], 3)
        self.assertEqual(result["cleaned_count"], 1)
        self.assertEqual(result["preview_count"], 1)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["pending_count"], 0)
        self.assertEqual(result["attempt"], 3)
        self.assertEqual(result["status"], "partial")


if __name__ == "__main__":
    unittest.main()
