"""Behavioral tests for the watermark workflow coordinator.

The coordinator talks to Fotello through injected callables.  These tests use
small fake API callbacks so that the assertions cover workflow state and
mapping without requiring a browser session or a live account.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest

from backend.watermark_workflow.coordinator import run_auto
from backend.watermark_workflow.models import build_groups, new_manifest


class TestWorkflowCoordinator(unittest.TestCase):
    """Exercise selective retries, bracket mapping, and failure boundaries."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="workflow_coordinator_"))
        self._manifest_sequence = 0
        self.state_dir = self.temp_dir / "state"
        self.state_dir.mkdir()
        self.previous_state_dir = os.environ.get("FOTELLO_WORKFLOW_STATE_DIR")
        os.environ["FOTELLO_WORKFLOW_STATE_DIR"] = str(self.state_dir)

    def tearDown(self) -> None:
        if self.previous_state_dir is None:
            os.environ.pop("FOTELLO_WORKFLOW_STATE_DIR", None)
        else:
            os.environ["FOTELLO_WORKFLOW_STATE_DIR"] = self.previous_state_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_manifest(self, bracket_size: int, output_count: int = 5):
        """Create deterministic source groups through the production helpers."""
        self._manifest_sequence += 1
        sequence = self._manifest_sequence
        input_dir = self.temp_dir / f"inputs_b{bracket_size}_{output_count}_{sequence}"
        input_dir.mkdir(parents=True)
        input_paths = []
        for number in range(1, bracket_size * output_count + 1):
            path = input_dir / f"img{number:02d}.jpg"
            path.write_bytes(f"source-{number}".encode("ascii"))
            input_paths.append(path)

        groups = build_groups(input_paths, bracket_size)
        output_dir = self.temp_dir / f"output_b{bracket_size}_{output_count}_{sequence}"
        manifest = new_manifest(
            groups,
            {"bracket_size": bracket_size},
            "team-test",
            "abc",
            str(output_dir),
        )
        return manifest, input_paths

    @staticmethod
    def _path_key(path: Path | str) -> str:
        return str(Path(path))

    def _run_retry_matrix(self, bracket_size: int):
        """Run the five-output matrix and return all fake API observations."""
        manifest, _ = self._make_manifest(bracket_size)
        groups = list(manifest["groups"])
        output_ids = [group["output_id"] for group in groups]
        output_names = {group["output_id"]: group["output_name"] for group in groups}
        expected_paths = {
            group["output_id"]: tuple(self._path_key(path) for path in group["input_paths"])
            for group in groups
        }

        upload_calls: list[str] = []
        upload_id_to_path: dict[str, str] = {}
        upload_occurrences: Counter[str] = Counter()
        listing_calls: list[dict] = []
        listing_by_id: dict[str, dict] = {}
        enhance_calls: list[dict] = []
        enhance_by_id: dict[str, dict] = {}
        download_calls: list[dict] = []
        clean_calls: list[dict] = []
        first_poll_snapshot: list[tuple[int, int]] = []
        lock = threading.Lock()

        def upload(path: Path):
            path_key = self._path_key(path)
            with lock:
                upload_occurrences[path_key] += 1
                occurrence = upload_occurrences[path_key]
                upload_id = f"upload-{occurrence}-{Path(path).name}"
                upload_calls.append(path_key)
                upload_id_to_path[upload_id] = path_key
            return upload_id

        def create_listing(name, selected):
            listing_id = f"listing-{len(listing_calls) + 1}"
            snapshot = {
                "listing_id": listing_id,
                "name": str(name),
                "output_ids": [group["output_id"] for group in selected],
                "groups": {
                    group["output_id"]: tuple(
                        self._path_key(path) for path in group["input_paths"]
                    )
                    for group in selected
                },
            }
            listing_calls.append(snapshot)
            listing_by_id[listing_id] = snapshot
            return listing_id

        def create_enhance(listing_id, upload_ids):
            listing = listing_by_id[listing_id]
            position = sum(
                1 for record in enhance_calls if record["listing_id"] == listing_id
            )
            output_id = listing["output_ids"][position]
            enhance_id = f"enhance-{len(enhance_calls) + 1}"
            record = {
                "enhance_id": enhance_id,
                "listing_id": listing_id,
                "output_id": output_id,
                "upload_ids": list(upload_ids),
                "upload_paths": tuple(upload_id_to_path[upload_id] for upload_id in upload_ids),
            }
            enhance_calls.append(record)
            enhance_by_id[enhance_id] = record
            return enhance_id

        def check_ready(enhance_id):
            if not first_poll_snapshot:
                first_poll_snapshot.append((len(listing_calls), len(enhance_calls)))
            return True

        def download(enhance_id, directory, name, rendition):
            record = enhance_by_id[enhance_id]
            path = Path(directory) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(enhance_id.encode("ascii"))
            download_calls.append(
                {
                    "enhance_id": enhance_id,
                    "output_id": record["output_id"],
                    "directory": Path(directory),
                    "name": name,
                    "rendition": rendition,
                    "path": path,
                }
            )
            return {"path": path, "rendition": "edited"}

        def clean(output_id, output_name, variant_paths, output_dir, is_cancelled=None):
            variants = tuple(str(path) for path in variant_paths)
            clean_calls.append(
                {
                    "output_id": output_id,
                    "output_name": output_name,
                    "variant_paths": variants,
                }
            )
            index = output_ids.index(output_id)
            if len(variants) == 2 and index in (1, 3):
                status = "need_variant"
            elif len(variants) == 3 and index == 3:
                status = "need_variant"
            else:
                status = "cleaned"

            clean_path = Path(output_dir) / "clean" / f"{Path(output_name).stem}.png"
            if status == "cleaned":
                clean_path.parent.mkdir(parents=True, exist_ok=True)
                clean_path.write_bytes(b"clean")
            return {
                "status": status,
                "output_path": str(clean_path),
                "completion": 100 if status == "cleaned" else 0,
                "reason": "same watermark corner" if status == "need_variant" else "",
            }

        result = run_auto(
            manifest,
            upload=upload,
            create_listing=create_listing,
            create_enhance=create_enhance,
            check_ready=check_ready,
            download=download,
            clean=clean,
            max_workers=4,
            chunk_size=2,
            poll_timeout=2,
            poll_interval=lambda attempt, ready: 0,
            sleep=lambda delay: None,
        )
        return {
            "manifest": manifest,
            "groups": groups,
            "output_ids": output_ids,
            "output_names": output_names,
            "expected_paths": expected_paths,
            "upload_calls": upload_calls,
            "upload_occurrences": upload_occurrences,
            "listing_calls": listing_calls,
            "enhance_calls": enhance_calls,
            "download_calls": download_calls,
            "clean_calls": clean_calls,
            "first_poll_snapshot": first_poll_snapshot,
            "result": result,
        }

    def test_selective_retries_keep_bracket_groups_and_names_for_all_brackets(self):
        """Only unresolved outputs get new jobs, with immutable bracket inputs."""
        for bracket_size in (1, 3, 5):
            with self.subTest(bracket_size=bracket_size):
                observed = self._run_retry_matrix(bracket_size)
                manifest = observed["manifest"]
                output_ids = observed["output_ids"]
                expected_paths = observed["expected_paths"]
                output_names = observed["output_names"]

                # Both initial rounds are fully created before the first ready poll.
                self.assertEqual(
                    observed["first_poll_snapshot"],
                    [(2 * ((len(output_ids) + 1) // 2), 2 * len(output_ids))],
                )

                expected_attempt_outputs = [
                    output_ids,
                    output_ids,
                    [output_ids[1], output_ids[3]],
                    [output_ids[3]],
                ]
                self.assertEqual(
                    [
                        [
                            output_id
                            for listing in attempt["listings"]
                            for output_id in listing["output_ids"]
                        ]
                        for attempt in manifest["attempts"]
                    ],
                    expected_attempt_outputs,
                )
                self.assertEqual(
                    [attempt["number"] for attempt in manifest["attempts"]],
                    [1, 2, 3, 4],
                )

                # Every listing owns complete groups and chunking never splits one.
                for attempt, expected_outputs in zip(
                    manifest["attempts"], expected_attempt_outputs
                ):
                    for chunk_number, listing in enumerate(attempt["listings"], 1):
                        self.assertEqual(listing["chunk"], chunk_number)
                        start = (chunk_number - 1) * 2
                        self.assertEqual(
                            listing["output_ids"], expected_outputs[start : start + 2]
                        )
                        self.assertLessEqual(len(listing["output_ids"]), 2)

                for listing in observed["listing_calls"]:
                    self.assertEqual(
                        listing["output_ids"], list(listing["groups"])
                    )
                    for output_id, paths in listing["groups"].items():
                        self.assertEqual(paths, expected_paths[output_id])

                # Each enhance receives exactly the immutable source group for its
                # output, using upload IDs from the same attempt/listing.
                for enhance in observed["enhance_calls"]:
                    self.assertEqual(
                        enhance["upload_paths"], expected_paths[enhance["output_id"]]
                    )

                expected_upload_counts = {}
                for index, group in enumerate(observed["groups"]):
                    rounds = 2 + (index in (1, 3)) + (index == 3)
                    for path in group["input_paths"]:
                        expected_upload_counts[self._path_key(path)] = rounds
                self.assertEqual(
                    observed["upload_occurrences"], Counter(expected_upload_counts)
                )

                # Download and cleaner calls retain the original output name on
                # every retry, even when the bracket contains several inputs.
                for record in observed["download_calls"]:
                    expected_name = f"{Path(output_names[record['output_id']]).stem}.jpg"
                    self.assertEqual(record["name"], expected_name)
                for record in observed["clean_calls"]:
                    self.assertEqual(
                        record["output_name"], output_names[record["output_id"]]
                    )

                clean_lengths = defaultdict(list)
                for record in observed["clean_calls"]:
                    clean_lengths[record["output_id"]].append(
                        len(record["variant_paths"])
                    )
                self.assertEqual(
                    clean_lengths[output_ids[0]], [2]
                )
                self.assertEqual(
                    clean_lengths[output_ids[1]], [2, 3]
                )
                self.assertEqual(
                    clean_lengths[output_ids[2]], [2]
                )
                self.assertEqual(
                    clean_lengths[output_ids[3]], [2, 3, 4]
                )
                self.assertEqual(
                    clean_lengths[output_ids[4]], [2]
                )
                self.assertTrue(all(group["status"] == "cleaned" for group in manifest["groups"]))
                self.assertEqual(observed["result"]["cleaned_count"], 5)
                self.assertEqual(observed["result"]["target_count"], 5)

    def test_many_duplicate_variants_continue_until_cancellation_without_round_cap(self):
        """A repeated watermark is retried until cancellation, regardless of count."""
        manifest, _ = self._make_manifest(1, output_count=1)
        create_enhance_calls = []
        clean_calls = []
        cancelled = {"value": False}
        attempt_to_cancel = 24

        def upload(path):
            return f"upload-{Path(path).name}-{len(create_enhance_calls)}"

        def create_listing(name, selected):
            return f"listing-{len(manifest['attempts'])}"

        def create_enhance(listing_id, upload_ids):
            enhance_id = f"enhance-{len(create_enhance_calls) + 1}"
            create_enhance_calls.append(enhance_id)
            return enhance_id

        def check_ready(enhance_id):
            return True

        def download(enhance_id, directory, name, rendition):
            path = Path(directory) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(enhance_id.encode("ascii"))
            return {"path": path, "rendition": "edited"}

        def clean(output_id, output_name, variant_paths, output_dir, is_cancelled=None):
            clean_calls.append(tuple(variant_paths))
            if len(manifest["attempts"]) >= attempt_to_cancel:
                cancelled["value"] = True
            return {"status": "need_variant", "reason": "same corner"}

        result = run_auto(
            manifest,
            upload=upload,
            create_listing=create_listing,
            create_enhance=create_enhance,
            check_ready=check_ready,
            download=download,
            clean=clean,
            max_workers=1,
            chunk_size=30,
            poll_timeout=2,
            poll_interval=lambda attempt, ready: 0,
            sleep=lambda delay: None,
            is_cancelled=lambda: cancelled["value"],
        )

        self.assertEqual(len(manifest["attempts"]), attempt_to_cancel)
        self.assertEqual(len(create_enhance_calls), attempt_to_cancel)
        self.assertGreaterEqual(len(clean_calls), attempt_to_cancel - 1)
        self.assertEqual(manifest["status"], "stopped")
        self.assertEqual(result["status"], "stopped")

    def test_incomplete_bracket_upload_never_creates_partial_enhance(self):
        """A missing source blocks the whole bracket before listing creation."""
        manifest, input_paths = self._make_manifest(3, output_count=1)
        failing_path = self._path_key(input_paths[1])
        listing_calls = []
        enhance_calls = []

        def upload(path):
            if self._path_key(path) == failing_path:
                return None
            return f"upload-{Path(path).name}"

        def create_listing(name, selected):
            listing_calls.append(selected)
            return "listing-should-not-exist"

        def create_enhance(listing_id, upload_ids):
            enhance_calls.append((listing_id, upload_ids))
            return "enhance-should-not-exist"

        result = run_auto(
            manifest,
            upload=upload,
            create_listing=create_listing,
            create_enhance=create_enhance,
            check_ready=lambda enhance_id: True,
            download=lambda *args: None,
            clean=lambda *args, **kwargs: {"status": "cleaned"},
            max_workers=3,
            chunk_size=30,
            poll_timeout=1,
            poll_interval=lambda attempt, ready: 0,
            sleep=lambda delay: None,
        )

        self.assertEqual(listing_calls, [])
        self.assertEqual(enhance_calls, [])
        self.assertEqual(manifest["groups"][0]["status"], "blocked")
        self.assertEqual(result["failed_count"], 1)

    def test_source_changed_between_attempts_prevents_second_upload(self):
        """Retries must use the original source snapshot, not a changed file."""
        manifest, input_paths = self._make_manifest(1, output_count=1)
        source_path = input_paths[0]
        upload_calls = []
        listing_calls = []
        enhance_calls = []

        def upload(path):
            upload_calls.append(self._path_key(path))
            # Simulate a source edit after attempt 1 has uploaded it and before
            # attempt 2 starts its own upload validation.
            source_path.write_bytes(b"source-edited-after-attempt-one")
            return "upload-attempt-one"

        def create_listing(name, selected):
            listing_calls.append(str(name))
            return f"listing-{len(listing_calls)}"

        def create_enhance(listing_id, upload_ids):
            enhance_calls.append((listing_id, list(upload_ids)))
            return f"enhance-{len(enhance_calls)}"

        def download(enhance_id, directory, name, rendition):
            path = Path(directory) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(enhance_id.encode("ascii"))
            return {"path": path, "rendition": "edited"}

        result = run_auto(
            manifest,
            upload=upload,
            create_listing=create_listing,
            create_enhance=create_enhance,
            check_ready=lambda enhance_id: True,
            download=download,
            clean=lambda *args, **kwargs: {"status": "cleaned"},
            max_workers=1,
            chunk_size=30,
            poll_timeout=1,
            poll_interval=lambda attempt, ready: 0,
            sleep=lambda delay: None,
        )

        self.assertEqual(upload_calls, [self._path_key(source_path)])
        self.assertEqual(len(listing_calls), 1)
        self.assertEqual(len(enhance_calls), 1)
        self.assertEqual(len(manifest["attempts"]), 2)
        self.assertEqual(manifest["attempts"][1]["listings"], [])
        self.assertEqual(manifest["groups"][0]["status"], "blocked")
        self.assertIn("đã thay đổi", manifest["groups"][0]["reason"])
        self.assertEqual(result["failed_count"], 1)

    def test_transient_download_retries_same_enhance_without_new_job(self):
        """I/O failure is retried against its existing enhance ID."""
        manifest, _ = self._make_manifest(1, output_count=1)
        enhance_ids = []
        download_calls = []
        failed_once = {"value": False}

        def upload(path):
            return f"upload-{Path(path).name}-{len(enhance_ids)}"

        def create_listing(name, selected):
            return f"listing-{len(manifest['attempts'])}"

        def create_enhance(listing_id, upload_ids):
            enhance_id = f"enhance-{len(enhance_ids) + 1}"
            enhance_ids.append(enhance_id)
            return enhance_id

        def download(enhance_id, directory, name, rendition):
            download_calls.append(enhance_id)
            if enhance_id == "enhance-1" and not failed_once["value"]:
                failed_once["value"] = True
                raise OSError("temporary download failure")
            path = Path(directory) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(enhance_id.encode("ascii"))
            return {"path": path, "rendition": "edited"}

        result = run_auto(
            manifest,
            upload=upload,
            create_listing=create_listing,
            create_enhance=create_enhance,
            check_ready=lambda enhance_id: True,
            download=download,
            clean=lambda output_id, output_name, variant_paths, output_dir, is_cancelled=None: {
                "status": "cleaned",
                "output_path": str(Path(output_dir) / "clean" / "img01.png"),
            },
            max_workers=1,
            chunk_size=30,
            poll_timeout=2,
            poll_interval=lambda attempt, ready: 0,
            sleep=lambda delay: None,
        )

        self.assertEqual(enhance_ids, ["enhance-1", "enhance-2"])
        self.assertEqual(len(manifest["attempts"]), 2)
        self.assertEqual(download_calls.count("enhance-1"), 2)
        self.assertEqual(download_calls.count("enhance-2"), 1)
        self.assertEqual(manifest["groups"][0]["status"], "cleaned")
        self.assertEqual(result["cleaned_count"], 1)

    def test_listing_creation_failure_is_checkpointed_without_blind_retry(self):
        manifest, _ = self._make_manifest(1, output_count=1)
        listing_calls = []
        enhance_calls = []

        def upload(path):
            return f"upload-{Path(path).name}"

        def create_listing(name, selected):
            listing_calls.append(str(name))
            raise TimeoutError("listing response lost")

        def create_enhance(listing_id, upload_ids):
            enhance_calls.append((listing_id, upload_ids))
            return "enhance-should-not-exist"

        run_auto(
            manifest,
            upload=upload,
            create_listing=create_listing,
            create_enhance=create_enhance,
            check_ready=lambda enhance_id: True,
            download=lambda *args: None,
            clean=lambda *args, **kwargs: {"status": "cleaned"},
            max_workers=1,
            chunk_size=30,
            poll_timeout=1,
            poll_interval=lambda attempt, ready: 0,
            sleep=lambda delay: None,
        )

        self.assertEqual(len(listing_calls), 1)
        self.assertEqual(enhance_calls, [])
        self.assertEqual(len(manifest["attempts"]), 2)
        self.assertEqual(manifest["groups"][0]["status"], "blocked")
        self.assertIn("submission_unknown", manifest["attempts"][0]["listings"][0]["status"])

    def test_enhance_creation_failure_is_checkpointed_without_blind_retry(self):
        manifest, _ = self._make_manifest(1, output_count=1)
        listing_calls = []
        enhance_calls = []

        def upload(path):
            return f"upload-{Path(path).name}"

        def create_listing(name, selected):
            listing_calls.append(str(name))
            return f"listing-{len(listing_calls)}"

        def create_enhance(listing_id, upload_ids):
            enhance_calls.append((listing_id, list(upload_ids)))
            raise TimeoutError("enhance response lost")

        run_auto(
            manifest,
            upload=upload,
            create_listing=create_listing,
            create_enhance=create_enhance,
            check_ready=lambda enhance_id: True,
            download=lambda *args: None,
            clean=lambda *args, **kwargs: {"status": "cleaned"},
            max_workers=1,
            chunk_size=30,
            poll_timeout=1,
            poll_interval=lambda attempt, ready: 0,
            sleep=lambda delay: None,
        )

        self.assertEqual(len(listing_calls), 1)
        self.assertEqual(len(enhance_calls), 1)
        self.assertEqual(len(manifest["attempts"]), 2)
        self.assertEqual(manifest["groups"][0]["status"], "blocked")
        record = manifest["attempts"][0]["listings"][0]["enhances"][0]
        self.assertEqual(record["status"], "submission_unknown")

    def test_preview_and_blocked_results_do_not_auto_regenerate(self):
        for terminal_status in ("preview", "blocked"):
            with self.subTest(status=terminal_status):
                manifest, _ = self._make_manifest(1, output_count=1)
                enhance_calls = []

                def upload(path):
                    return f"upload-{Path(path).name}"

                def create_listing(name, selected):
                    return f"listing-{len(manifest['attempts'])}"

                def create_enhance(listing_id, upload_ids):
                    enhance_id = f"enhance-{len(enhance_calls) + 1}"
                    enhance_calls.append(enhance_id)
                    return enhance_id

                def download(enhance_id, directory, name, rendition):
                    path = Path(directory) / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(enhance_id.encode("ascii"))
                    return {"path": path, "rendition": "edited"}

                def clean(output_id, output_name, variant_paths, output_dir, is_cancelled=None):
                    return {"status": terminal_status, "reason": "manual review"}

                result = run_auto(
                    manifest,
                    upload=upload,
                    create_listing=create_listing,
                    create_enhance=create_enhance,
                    check_ready=lambda enhance_id: True,
                    download=download,
                    clean=clean,
                    max_workers=1,
                    chunk_size=30,
                    poll_timeout=1,
                    poll_interval=lambda attempt, ready: 0,
                    sleep=lambda delay: None,
                )

                self.assertEqual(len(manifest["attempts"]), 2)
                self.assertEqual(len(enhance_calls), 2)
                self.assertEqual(manifest["groups"][0]["status"], terminal_status)
                self.assertEqual(result["cleaned_count"], 0)
                count_key = "preview_count" if terminal_status == "preview" else "failed_count"
                self.assertEqual(result[count_key], 1)


if __name__ == "__main__":
    unittest.main()
