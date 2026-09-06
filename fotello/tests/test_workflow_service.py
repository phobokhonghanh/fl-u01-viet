import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import service


class ServiceWorkflowTests(unittest.TestCase):
    def test_service_wires_mapped_download_and_non_retried_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            for index in range(1, 7):
                (Path(folder) / f"photo{index}.jpg").write_bytes(b"input")
            created = []

            def run(manifest, **dependencies):
                self.assertEqual(len(manifest["groups"]), 2)
                self.assertEqual(manifest["groups"][1]["input_filenames"],
                                 ["photo4.jpg", "photo5.jpg", "photo6.jpg"])
                dependencies["create_listing"]("abc03", [manifest["groups"][1]])
                dependencies["create_enhance"]("listing3", ["u4", "u5", "u6"])
                return {"status": "success", "cleaned_count": 2}

            def post(endpoint, body, token, **kwargs):
                self.assertFalse(kwargs["retry_requests"])
                created.append(body)
                return {"id": "remote-id"}

            with patch.dict(service.FOTELLO_STATE, connected=True, team_id="team"), \
                    patch.object(service, "fotello_get_tokens", return_value={"id_token": "test", "access_token": "test"}), \
                    patch.object(service, "check_level_access", return_value=True), \
                    patch.object(service, "api_post", side_effect=post), \
                    patch("backend.watermark_workflow.coordinator.run_auto", side_effect=run):
                result = service.fotello_upload_and_enhance(folder, folder + "/out", preferences={"bracket_size": 3})
            self.assertEqual(result["cleaned_count"], 2)
            self.assertEqual(created[0]["filenames"], ["photo4.jpg", "photo5.jpg", "photo6.jpg"])
            self.assertEqual(created[0]["num_total_brackets"], 1)
            self.assertEqual(created[1]["upload_ids"], ["u4", "u5", "u6"])
            self.assertEqual(created[1]["preferences"]["bracket_size"], 3)

    def test_incomplete_bracket_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "photo.jpg").write_bytes(b"input")
            with patch.dict(service.FOTELLO_STATE, connected=True, team_id="team"), \
                    patch.object(service, "api_post") as post, \
                    patch.object(service, "upload_image_resumable") as upload:
                with self.assertRaises(ValueError):
                    service.fotello_upload_and_enhance(folder, folder + "/out", preferences={"bracket_size": 3})
                post.assert_not_called()
                upload.assert_not_called()
