import unittest

from job_radar.orchestration.snapshots import evaluate_snapshot
from job_radar.storage import SourceSnapshot


def snapshot(
    jobs_seen,
    status="healthy",
    closure_suppressed=False,
):
    return SourceSnapshot(
        jobs_seen=jobs_seen,
        snapshot_status=status,
        closure_suppressed=closure_suppressed,
    )


class SnapshotAssessmentTests(unittest.TestCase):
    def test_small_drop_is_healthy(self):
        result = evaluate_snapshot(95, [snapshot(100)])

        self.assertEqual(result.status, "healthy")
        self.assertFalse(result.closure_suppressed)

    def test_large_drop_is_suspicious(self):
        result = evaluate_snapshot(10, [snapshot(100)])

        self.assertEqual(result.status, "suspicious")
        self.assertTrue(result.closure_suppressed)
        self.assertEqual(result.baseline_jobs, 100)
        self.assertEqual(result.current_ratio, 0.1)

    def test_empty_snapshot_is_empty(self):
        result = evaluate_snapshot(0, [snapshot(100)])

        self.assertEqual(result.status, "empty")
        self.assertTrue(result.closure_suppressed)

    def test_small_source_drop_is_healthy(self):
        result = evaluate_snapshot(1, [snapshot(2)])

        self.assertEqual(result.status, "healthy")
        self.assertFalse(result.closure_suppressed)

    def test_repeated_reduced_snapshots_stabilize(self):
        history = [
            snapshot(10, "suspicious", True),
            snapshot(11, "suspicious", True),
            snapshot(100),
        ]

        result = evaluate_snapshot(10, history)

        self.assertEqual(result.status, "healthy")
        self.assertFalse(result.closure_suppressed)
        self.assertTrue(result.stabilized)

    def test_stabilized_level_remains_healthy(self):
        history = [
            snapshot(10),
            snapshot(10, "suspicious", True),
            snapshot(10, "suspicious", True),
            snapshot(100),
        ]

        result = evaluate_snapshot(11, history)

        self.assertEqual(result.status, "healthy")
        self.assertTrue(result.stabilized)

    def test_insufficient_history_does_not_suppress_closures(self):
        result = evaluate_snapshot(7, [])

        self.assertEqual(result.status, "healthy")
        self.assertFalse(result.closure_suppressed)


if __name__ == "__main__":
    unittest.main()
