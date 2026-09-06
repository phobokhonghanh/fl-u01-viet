"""WF2 mapping tests; all remote operations are mocked."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from backend.auth import FOTELLO_STATE
from backend.constants import FLD_EDITED, FLD_EDITED_UPSIZED, FLD_SV
from backend.downloads import (
    download_variant,
    fotello_batch_download,
    fotello_list_enhances_for_listing,
    fotello_list_listings,
)
from backend.watermark_workflow import build_groups, new_manifest
from backend.watermark_workflow.manual import download_manual_workflow
from backend.watermark_workflow.store import ManifestStore


class TestManualWorkflow(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="workflow_manual_"))
        self.state_dir = self.temp_dir / "state"
        self.old_state_dir = os.environ.get("FOTELLO_WORKFLOW_STATE_DIR")
        os.environ["FOTELLO_WORKFLOW_STATE_DIR"] = str(self.state_dir)

    def tearDown(self) -> None:
        if self.old_state_dir is None:
            os.environ.pop("FOTELLO_WORKFLOW_STATE_DIR", None)
        else:
            os.environ["FOTELLO_WORKFLOW_STATE_DIR"] = self.old_state_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _manifest(
        self,
        *,
        family_id: str,
        team_id: str,
        output_root: Path,
        listing_attempts: list[dict],
        bracket_size: int = 3,
        output_count: int = 2,
    ) -> dict:
        input_dir = self.temp_dir / f"inputs-{family_id}"
        input_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for number in range(1, bracket_size * output_count + 1):
            path = input_dir / f"img{number:02d}.jpg"
            path.write_bytes(f"source-{family_id}-{number}".encode("ascii"))
            paths.append(path)
        groups = build_groups(paths, bracket_size)
        manifest = new_manifest(groups, {"bracket_size": bracket_size}, team_id, "abc", output_root)
        manifest["family_id"] = family_id
        manifest["attempts"] = listing_attempts
        ManifestStore(output_root).save(manifest)
        return manifest

    @staticmethod
    def _enhance(enhance_id: str, output_id: str, names: list[str]) -> dict:
        return {
            "enhance_id": enhance_id,
            "output_id": output_id,
            "input_filenames": list(names),
        }

    def _fake_download(self, calls: list[dict]):
        def download(enhance_id, access_token, output_dir, output_name, **kwargs):
            path = Path(output_dir) / output_name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(enhance_id.encode("ascii"))
            calls.append(
                {
                    "enhance_id": enhance_id,
                    "access_token": access_token,
                    "output_dir": Path(output_dir),
                    "output_name": output_name,
                    "rendition": kwargs.get("rendition"),
                }
            )
            return {"path": path, "rendition": kwargs.get("rendition") or "edited"}

        return download

    def test_shuffled_attempts_keep_bracket_mapping_and_full_target_universe(self) -> None:
        """Enhances from different attempts map by output ID, never list order."""

        names_one = ["img01.jpg", "img02.jpg", "img03.jpg"]
        names_two = ["img04.jpg", "img05.jpg", "img06.jpg"]
        source_root = self.temp_dir / "wf1"
        manifest = self._manifest(
            family_id="family-shuffled",
            team_id="team-a",
            output_root=source_root,
            listing_attempts=[
                {
                    "number": 1,
                    "listings": [
                        {
                            "listing_id": "listing-01",
                            "chunk": 1,
                            "enhances": [
                                self._enhance("enhance-01", "img0001", names_one),
                                self._enhance("enhance-02", "img0002", names_two),
                            ],
                        }
                    ],
                },
                {
                    "number": 2,
                    "listings": [
                        {
                            "listing_id": "listing-02",
                            "chunk": 1,
                            # Deliberately reversed relative to attempt 1.
                            "enhances": [
                                self._enhance("enhance-04", "img0002", names_two),
                                self._enhance("enhance-03", "img0001", names_one),
                            ],
                        }
                    ],
                },
            ],
        )
        output_root = self.temp_dir / "manual"
        downloads: list[dict] = []
        cleans: list[dict] = []

        def clean(output_id, output_name, variants, output_dir, is_cancelled=None):
            cleans.append(
                {
                    "output_id": output_id,
                    "output_name": output_name,
                    "variants": tuple(Path(path).name for path in variants),
                    "output_dir": Path(output_dir),
                }
            )
            cleaned = len(set(variants)) >= 2
            return {
                "status": "cleaned" if cleaned else "need_variant",
                "output_path": str(Path(output_dir) / "clean" / output_name),
                "report_path": str(Path(output_dir) / "reports" / f"{Path(output_name).stem}.json"),
            }

        with patch("backend.watermark_workflow.manual.fotello_get_tokens", return_value={"access_token": "token"}), \
             patch("backend.watermark_workflow.manual.download_variant", side_effect=self._fake_download(downloads)), \
             patch("backend.watermark_workflow.manual.clean_output", side_effect=clean), \
             patch("backend.watermark_workflow.manual.fotello_list_enhances_for_listing") as list_enhances:
            result = download_manual_workflow(
                ["listing-02", "listing-01"],
                output_root,
                team_id="team-a",
            )

        list_enhances.assert_not_called()
        self.assertEqual(result["target_count"], 2)
        self.assertEqual(result["cleaned_count"], 2)
        self.assertEqual({item["output_id"] for item in cleans}, {"img0001", "img0002"})
        by_output = {item["output_id"]: item for item in cleans}
        self.assertEqual(len(by_output["img0001"]["variants"]), 2)
        self.assertEqual(len(by_output["img0002"]["variants"]), 2)
        self.assertEqual(len(downloads), 4)
        self.assertTrue(all(item["output_name"].endswith(".png") for item in cleans))
        # The source manifest remains the complete WF1 record after manual
        # processing; selected order does not rewrite attempt history.
        saved = ManifestStore(source_root).load()
        self.assertEqual(len(saved["groups"]), 2)
        self.assertEqual(len(saved["attempts"]), 2)

    def test_unknown_metadata_is_pending_and_never_success(self) -> None:
        """An enhance without an exact output mapping must remain unresolved."""

        source_root = self.temp_dir / "unknown-source"
        manifest = self._manifest(
            family_id="family-unknown",
            team_id="team-a",
            output_root=source_root,
            listing_attempts=[
                {
                    "number": 1,
                    "listings": [
                        {
                            "listing_id": "listing-unknown",
                            "chunk": 1,
                            "enhances": [{"enhance_id": "enhance-unknown"}],
                        }
                    ],
                }
            ],
            output_count=1,
        )
        with patch("backend.watermark_workflow.manual.download_variant") as download, \
             patch("backend.watermark_workflow.manual.clean_output") as clean:
            result = download_manual_workflow(
                ["listing-unknown"],
                self.temp_dir / "manual-unknown",
                team_id="team-a",
            )

        download.assert_not_called()
        self.assertNotEqual(result["status"], "success")
        self.assertEqual(result["cleaned_count"], 0)
        self.assertGreaterEqual(result["pending_count"], 1)
        self.assertGreaterEqual(result["unresolved_count"], 1)
        saved = ManifestStore(Path(result["manifest_path"]).parent).load()
        self.assertTrue(saved.get("unresolved"))

    def test_selected_family_is_cloned_to_requested_output_and_keeps_all_groups(self) -> None:
        """Manual output is isolated while the source family remains intact."""

        source_root = self.temp_dir / "wf1-output"
        raw_one = source_root / "raw" / "abc01" / "img01.jpg"
        raw_two = source_root / "raw" / "abc02" / "img01.jpg"
        raw_one.parent.mkdir(parents=True, exist_ok=True)
        raw_two.parent.mkdir(parents=True, exist_ok=True)
        raw_one.write_bytes(b"one")
        raw_two.write_bytes(b"two")
        manifest = self._manifest(
            family_id="family-clone",
            team_id="team-a",
            output_root=source_root,
            listing_attempts=[
                {
                    "number": 1,
                    "listings": [
                        {
                            "listing_id": "listing-clone",
                            "chunk": 1,
                            "enhances": [self._enhance("e1", "img0001", ["img01.jpg"])],
                        }
                    ],
                },
                {
                    "number": 2,
                    "listings": [
                        {
                            "listing_id": "listing-clone-2",
                            "chunk": 1,
                            "enhances": [self._enhance("e2", "img0001", ["img01.jpg"])],
                        }
                    ],
                },
            ],
            bracket_size=1,
            output_count=1,
        )
        manifest["groups"][0]["variants"] = [
            {"enhance_id": "e1", "listing_id": "listing-clone", "attempt": 1, "path": str(raw_one), "rendition": "edited"},
            {"enhance_id": "e2", "listing_id": "listing-clone-2", "attempt": 2, "path": str(raw_two), "rendition": "edited"},
        ]
        manifest["groups"][0]["status"] = "need_variant"
        ManifestStore(source_root).save(manifest)
        requested_root = self.temp_dir / "manual-output"
        clean_dirs: list[Path] = []

        def clean(output_id, output_name, variants, output_dir, is_cancelled=None):
            clean_dirs.append(Path(output_dir))
            return {
                "status": "cleaned",
                "output_path": str(Path(output_dir) / "clean" / output_name),
                "report_path": str(Path(output_dir) / "reports" / "img01.json"),
            }

        with patch("backend.watermark_workflow.manual.clean_output", side_effect=clean) as clean_mock:
            result = download_manual_workflow(["listing-clone"], requested_root, team_id="team-a")

        clean_mock.assert_called_once()
        self.assertEqual(result["cleaned_count"], 1)
        self.assertTrue(clean_dirs[0] == requested_root or requested_root in clean_dirs[0].parents)
        self.assertNotEqual(clean_dirs[0].resolve(), source_root.resolve())
        self.assertEqual(len(result["manifests"]), 1)
        cloned_manifest_path = Path(result["manifest_path"])
        self.assertTrue(cloned_manifest_path.exists())
        cloned = ManifestStore(cloned_manifest_path.parent).load()
        self.assertEqual(len(cloned["groups"]), 1)
        self.assertEqual(len(cloned["attempts"]), 2)
        self.assertTrue(ManifestStore(source_root).load()["groups"][0]["variants"])

    def test_mixed_families_are_kept_separate(self) -> None:
        """The same manual selection can contain unrelated workflow families."""

        manifests = []
        listing_ids = []
        for index, family_id in enumerate(("family-a", "family-b"), 1):
            source_root = self.temp_dir / family_id
            paths = [source_root / "a.jpg", source_root / "b.jpg"]
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(path.name.encode("ascii"))
            manifest = self._manifest(
                family_id=family_id,
                team_id="team-a",
                output_root=source_root,
                listing_attempts=[
                    {
                        "number": 1,
                        "listings": [
                            {
                                "listing_id": f"listing-family-{index}",
                                "chunk": 1,
                                "enhances": [
                                    self._enhance(f"{family_id}-1", "img0001", ["img01.jpg"]),
                                    self._enhance(f"{family_id}-2", "img0001", ["img01.jpg"]),
                                ],
                            }
                        ],
                    }
                ],
                bracket_size=1,
                output_count=1,
            )
            manifest["groups"][0]["variants"] = [
                {"enhance_id": f"{family_id}-1", "path": str(paths[0]), "rendition": "edited"},
                {"enhance_id": f"{family_id}-2", "path": str(paths[1]), "rendition": "edited"},
            ]
            ManifestStore(source_root).save(manifest)
            listing_ids.append(f"listing-family-{index}")
            manifests.append(manifest)
        clean_dirs: list[Path] = []

        def clean(output_id, output_name, variants, output_dir, is_cancelled=None):
            clean_dirs.append(Path(output_dir))
            return {"status": "cleaned", "output_path": str(Path(output_dir) / "clean" / output_name)}

        with patch("backend.watermark_workflow.manual.clean_output", side_effect=clean):
            result = download_manual_workflow(listing_ids, self.temp_dir / "mixed", team_id="team-a")

        self.assertEqual(result["target_count"], 2)
        self.assertEqual(result["cleaned_count"], 2)
        self.assertIsNone(result["family_id"])
        self.assertEqual(len({path.name for path in clean_dirs}), 2, clean_dirs)

    def test_deleted_raw_variant_is_downloaded_again(self) -> None:
        """A manifest path is reusable only while the file still exists."""

        source_root = self.temp_dir / "deleted-raw"
        missing = source_root / "raw" / "abc01" / "img01.jpg"
        present = source_root / "raw" / "abc02" / "img01.jpg"
        present.parent.mkdir(parents=True, exist_ok=True)
        present.write_bytes(b"present")
        manifest = self._manifest(
            family_id="family-deleted",
            team_id="team-a",
            output_root=source_root,
            listing_attempts=[
                {
                    "number": 1,
                    "listings": [
                        {
                            "listing_id": "listing-deleted",
                            "chunk": 1,
                            "enhances": [
                                self._enhance("deleted-e", "img0001", ["img01.jpg"]),
                                self._enhance("present-e", "img0001", ["img01.jpg"]),
                            ],
                        }
                    ],
                }
            ],
            bracket_size=1,
            output_count=1,
        )
        manifest["groups"][0]["variants"] = [
            {"enhance_id": "deleted-e", "path": str(missing), "rendition": "edited"},
            {"enhance_id": "present-e", "path": str(present), "rendition": "edited"},
        ]
        ManifestStore(source_root).save(manifest)
        calls: list[dict] = []
        with patch("backend.watermark_workflow.manual.fotello_get_tokens", return_value={"access_token": "token"}), \
             patch("backend.watermark_workflow.manual.download_variant", side_effect=self._fake_download(calls)), \
             patch("backend.watermark_workflow.manual.clean_output", return_value={"status": "cleaned"}):
            result = download_manual_workflow(["listing-deleted"], self.temp_dir / "manual", team_id="team-a")

        self.assertEqual(result["cleaned_count"], 1)
        self.assertEqual([item["enhance_id"] for item in calls], ["deleted-e"])
        self.assertTrue((calls[0]["output_dir"] / calls[0]["output_name"]).exists())
        self.assertFalse(missing.exists())

    def test_cancelled_workflow_does_not_download_following_variants(self) -> None:
        """Cancellation is checked between mapped enhance downloads."""

        source_root = self.temp_dir / "cancel-source"
        manifest = self._manifest(
            family_id="family-cancel",
            team_id="team-a",
            output_root=source_root,
            listing_attempts=[
                {
                    "number": 1,
                    "listings": [
                        {
                            "listing_id": "listing-cancel",
                            "chunk": 1,
                            "enhances": [
                                self._enhance("cancel-1", "img0001", ["img01.jpg"]),
                                self._enhance("cancel-2", "img0001", ["img01.jpg"]),
                            ],
                        }
                    ],
                }
            ],
            bracket_size=1,
            output_count=1,
        )
        cancelled = {"value": False}
        calls: list[dict] = []

        def fake_download(*args, **kwargs):
            result = self._fake_download(calls)(*args, **kwargs)
            cancelled["value"] = True
            return result

        with patch("backend.watermark_workflow.manual.fotello_get_tokens", return_value={"access_token": "token"}), \
             patch("backend.watermark_workflow.manual.download_variant", side_effect=fake_download), \
             patch("backend.watermark_workflow.manual.clean_output"):
            result = download_manual_workflow(
                ["listing-cancel"],
                self.temp_dir / "manual-cancel",
                team_id="team-a",
                is_cancelled=lambda: cancelled["value"],
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(result["status"], "stopped")

    def test_registry_filters_team(self) -> None:
        """A listing ID cannot select another team's family manifest."""

        for team, family in (("team-a", "family-team-a"), ("team-b", "family-team-b")):
            self._manifest(
                family_id=family,
                team_id=team,
                output_root=self.temp_dir / family,
                listing_attempts=[
                    {
                        "number": 1,
                        "listings": [{"listing_id": "shared-listing", "chunk": 1, "enhances": []}],
                    }
                ],
                bracket_size=1,
                output_count=1,
            )
        self.assertEqual(
            ManifestStore.find_by_listing("shared-listing", "team-a")["family_id"],
            "family-team-a",
        )
        self.assertEqual(
            ManifestStore.find_by_listing("shared-listing", "team-b")["family_id"],
            "family-team-b",
        )
        self.assertIsNone(ManifestStore.find_by_listing("shared-listing", "team-c"))

    def test_batch_download_forwards_connected_team_to_manual_workflow(self) -> None:
        old_state = dict(FOTELLO_STATE)
        FOTELLO_STATE.update({"connected": True, "team_id": "team-forward"})
        try:
            with patch(
                "backend.watermark_workflow.manual.download_manual_workflow",
                return_value={"status": "partial"},
            ) as workflow:
                result = fotello_batch_download(["listing-1"], str(self.temp_dir / "out"))
        finally:
            FOTELLO_STATE.clear()
            FOTELLO_STATE.update(old_state)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(workflow.call_args.kwargs["team_id"], "team-forward")

    def test_batch_download_rejects_disconnected_state(self) -> None:
        old_state = dict(FOTELLO_STATE)
        FOTELLO_STATE.update({"connected": False, "team_id": "team-forward"})
        try:
            with self.assertRaises(RuntimeError):
                fotello_batch_download(["listing-1"], str(self.temp_dir / "out"))
        finally:
            FOTELLO_STATE.clear()
            FOTELLO_STATE.update(old_state)


class TestDownloadMetadataAndRendition(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="download_metadata_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_enhance_metadata_keeps_full_input_filename_tuple(self) -> None:
        rows = [
            {
                "document": {
                    "name": "projects/p/databases/(default)/documents/enhances/enhance-1",
                    "fields": {
                        "status": {FLD_SV: "enhance_success"},
                        FLD_EDITED: {FLD_SV: "gs://bucket/edited"},
                        "inputFilenames": {
                            "arrayValue": {
                                "values": [
                                    {FLD_SV: "img01.jpg"},
                                    {FLD_SV: "img02.jpg"},
                                    {FLD_SV: "img03.jpg"},
                                ]
                            }
                        },
                    },
                }
            }
        ]
        with patch("backend.downloads.fotello_get_tokens", return_value={"access_token": "token"}), \
             patch("backend.downloads.firestore_run_query", return_value=rows):
            result = fotello_list_enhances_for_listing("listing-1")

        self.assertEqual(result[0]["filename"], "img01.jpg")
        self.assertEqual(
            result[0]["input_filenames"],
            ["img01.jpg", "img02.jpg", "img03.jpg"],
        )
        self.assertEqual(result[0]["inputFilenames"], result[0]["input_filenames"])

    def test_listing_metadata_exposes_attempt_marker_fields(self) -> None:
        rows = [
            {
                "document": {
                    "name": "projects/p/databases/(default)/documents/listings/listing-1",
                    "fields": {
                        "address": {FLD_SV: "abc02 [wm:family-1:2:3]"},
                        "num_total_brackets": {"integerValue": "4"},
                    },
                }
            }
        ]
        old_state = dict(FOTELLO_STATE)
        FOTELLO_STATE.update({"connected": True, "team_id": "team-a"})
        try:
            with patch("backend.downloads.fotello_get_tokens", return_value={"access_token": "token"}), \
                 patch("backend.downloads.firestore_run_query", return_value=rows):
                result = fotello_list_listings()
        finally:
            FOTELLO_STATE.clear()
            FOTELLO_STATE.update(old_state)

        self.assertEqual(result[0]["family_id"], "family-1")
        self.assertEqual(result[0]["attempt"], 2)
        self.assertEqual(result[0]["chunk"], 3)
        self.assertEqual(result[0]["prefix"], "abc")

    def test_forced_rendition_is_exact_and_missing_does_not_fallback(self) -> None:
        fields = {
            FLD_EDITED: {FLD_SV: "gs://bucket/edited"},
            FLD_EDITED_UPSIZED: {FLD_SV: "gs://bucket/upsized"},
        }
        with patch("backend.downloads.firestore_get", return_value={"fields": fields}), \
             patch("backend.downloads.storage_download", return_value=b"image") as storage:
            selected = download_variant(
                "enhance-1",
                "token",
                self.temp_dir,
                "../safe/image.jpg",
                rendition="edited_upsized",
            )
        self.assertEqual(selected["rendition"], "edited_upsized")
        self.assertEqual(storage.call_args.args[0], "gs://bucket/upsized")
        self.assertEqual(Path(selected["path"]).parent, self.temp_dir)
        self.assertEqual(Path(selected["path"]).name, "image.jpg")

        with patch("backend.downloads.firestore_get", return_value={"fields": {FLD_EDITED: {FLD_SV: "gs://bucket/edited"}}}), \
             patch("backend.downloads.storage_download") as missing_storage:
            missing = download_variant(
                "enhance-2",
                "token",
                self.temp_dir,
                "image-2.jpg",
                rendition="edited_upsized",
            )
        self.assertIsNone(missing)
        missing_storage.assert_not_called()


if __name__ == "__main__":
    unittest.main()
