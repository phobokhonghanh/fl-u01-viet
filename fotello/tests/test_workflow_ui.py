import unittest

from main import FotelloJob, _apply_job_summary, _finish_job


class JobSummaryTests(unittest.TestCase):
    def test_live_complete_summary_does_not_stop_running_job(self):
        job = FotelloJob("test", "upload", "abc", status="running")
        result = dict(target_count=5, cleaned_count=5, downloaded_count=10, status="success")
        _apply_job_summary(job, result, live=True)
        self.assertEqual(job.status, "running")
        self.assertEqual(job.downloaded_count, 10)
        self.assertEqual(job.cleaned_count, 5)
        _finish_job(job, result)
        self.assertEqual(job.status, "success")

    def test_raw_variants_or_preview_never_count_as_complete(self):
        job = FotelloJob("test", "download", "abc")
        _finish_job(job, dict(status="success", target_count=5, cleaned_count=4,
                              preview_count=1, downloaded_count=10))
        self.assertEqual(job.status, "partial")
        self.assertEqual(job.snapshot()["preview_count"], 1)

    def test_stop_wins_over_success(self):
        job = FotelloJob("test", "upload", "abc", stop_requested=True)
        _finish_job(job, dict(status="success", target_count=1, cleaned_count=1))
        self.assertEqual(job.status, "stopped")
