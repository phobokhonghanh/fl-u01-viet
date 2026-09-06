"""Exercise orchestration with the real cleaner and saved listing mapping."""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.watermark_workflow.cleaner import clean_output
from backend.watermark_workflow.coordinator import run_auto
from backend.watermark_workflow.models import build_groups, new_manifest
from backend.watermark_workflow.store import ManifestStore


class WorkflowIntegrationTests(unittest.TestCase):
    def test_auto_real_cleaner_then_manual_after_registry_reload(self):
        fixture = sorted(Path("test_wm/01").glob("*.jpg"))
        if len(fixture) < 2:
            self.skipTest("Real fixture test_wm/01 unavailable")
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"FOTELLO_WORKFLOW_STATE_DIR": str(Path(directory) / "state")}
        ):
            root = Path(directory)
            manifest = new_manifest(build_groups([fixture[0]], 1), {"bracket_size": 1}, "team", "abc", root / "auto")
            listing_ids = []
            enhance_ids = []

            def listing(name, groups):
                listing_id = f"listing{len(listing_ids) + 1}"
                listing_ids.append(listing_id)
                return listing_id

            def enhance(listing_id, uploads):
                enhance_id = f"enhance{len(enhance_ids) + 1}"
                enhance_ids.append(enhance_id)
                return enhance_id

            def download(enhance_id, output, filename, rendition):
                source = fixture[enhance_ids.index(enhance_id)]
                target = Path(output) / filename
                shutil.copyfile(source, target)
                return {"path": str(target), "rendition": "edited"}

            auto = run_auto(manifest, upload=lambda path: "upload", create_listing=listing,
                            create_enhance=enhance, check_ready=lambda _: True,
                            download=download, clean=clean_output, poll_timeout=10)
            self.assertEqual(auto["status"], "success")
            self.assertEqual(auto["downloaded_count"], 2)
            recovered = ManifestStore.find_by_listing(listing_ids[1], team_id="team")
            self.assertEqual(len(recovered["groups"]), 1)
            self.assertTrue(all(isinstance(v, dict) for v in recovered["groups"][0]["variants"]))

            # No live API or in-memory job manager: manual finds the persisted
            # mapping and reuses the files already on disk in a new destination.
            from backend.watermark_workflow import manual
            with patch.object(manual, "fotello_list_listings", side_effect=AssertionError("Unexpected network")), \
                    patch.object(manual, "fotello_list_enhances_for_listing", side_effect=AssertionError("Unexpected network")), \
                    patch.object(manual, "download_variant", side_effect=AssertionError("Unexpected network")):
                result = manual.download_manual_workflow(listing_ids, root / "manual", team_id="team")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["cleaned_count"], 1)
            self.assertEqual(len(list((root / "manual").rglob("clean/*.png"))), 1)
            self.assertTrue(Path(recovered["groups"][0]["output_path"]).is_file())
