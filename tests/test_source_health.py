import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from job_radar.observability import (
    SourceHealth,
    calculate_source_health,
    violates_required_slo,
)
from job_radar.storage import SourceRunRecord
from scripts import source_health


NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def make_runs(statuses):
    return tuple(
        SourceRunRecord(
            company="Example",
            source="greenhouse",
            status=status,
            finished_at=NOW - timedelta(days=index),
            error_type="TimeoutError" if status == "failed" else None,
            error_message=f"failure {index}" if status == "failed" else None,
        )
        for index, status in enumerate(statuses)
    )


def calculate(statuses, *, required=True):
    return calculate_source_health(
        company="Example",
        source="greenhouse",
        required=required,
        runs=make_runs(statuses),
    )


class SourceHealthTests(unittest.TestCase):
    def test_ten_of_ten_is_healthy(self):
        health = calculate(["success"] * 10)

        self.assertEqual(health.health_status, "healthy")
        self.assertEqual(health.success_rate, 1.0)

    def test_nine_of_ten_is_healthy(self):
        health = calculate(["success"] * 9 + ["failed"])

        self.assertEqual(health.health_status, "healthy")
        self.assertEqual(health.success_rate, 0.9)

    def test_eight_of_ten_is_degraded_and_breaches_required_slo(self):
        health = calculate(["success"] * 8 + ["failed"] * 2)

        self.assertEqual(health.health_status, "degraded")
        self.assertTrue(violates_required_slo(health))

    def test_isolated_latest_failure_is_degraded(self):
        health = calculate(["failed"] + ["success"] * 9)

        self.assertEqual(health.health_status, "degraded")
        self.assertEqual(health.consecutive_failures, 1)

    def test_two_consecutive_failures_are_unhealthy(self):
        health = calculate(["failed", "failed"] + ["success"] * 8)

        self.assertEqual(health.health_status, "unhealthy")

    def test_success_rate_below_seventy_percent_is_unhealthy(self):
        health = calculate(["success"] * 6 + ["failed"] * 4)

        self.assertEqual(health.health_status, "unhealthy")
        self.assertEqual(health.success_rate, 0.6)

    def test_no_history_is_unknown(self):
        health = calculate([])

        self.assertEqual(health.health_status, "unknown")
        self.assertIsNone(health.success_rate)
        self.assertFalse(violates_required_slo(health))

    def test_last_success_failure_and_error_come_from_latest_matches(self):
        health = calculate(["failed", "success", "failed", "success"])

        self.assertEqual(health.last_failure_at, NOW)
        self.assertEqual(health.last_success_at, NOW - timedelta(days=1))
        self.assertEqual(health.last_error_type, "TimeoutError")
        self.assertEqual(health.last_error_message, "failure 0")

    def test_last_success_can_precede_the_health_window(self):
        health = calculate(["failed"] * 10 + ["success"])

        self.assertEqual(health.recent_runs, 10)
        self.assertEqual(health.successful_runs, 0)
        self.assertEqual(health.last_success_at, NOW - timedelta(days=10))


class SourceHealthCommandTests(unittest.TestCase):
    def run_check(self, health: SourceHealth):
        output = io.StringIO()
        errors = io.StringIO()
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://test"}):
            with patch.object(source_health, "_load_health", return_value=(health,)):
                with redirect_stdout(output), redirect_stderr(errors):
                    result = source_health.main(["--check"])
        return result, output.getvalue(), errors.getvalue()

    def test_optional_unhealthy_does_not_fail_check(self):
        health = calculate(["failed", "failed"], required=False)

        result, output, errors = self.run_check(health)

        self.assertEqual(result, 0)
        self.assertIn("SLO de fuentes required cumplido", output)
        self.assertEqual(errors, "")

    def test_required_unhealthy_fails_check(self):
        health = calculate(["failed", "failed"], required=True)

        result, output, errors = self.run_check(health)

        self.assertEqual(result, 1)
        self.assertIn("UNHEALTHY", output)
        self.assertIn("SLO incumplido", errors)


if __name__ == "__main__":
    unittest.main()
