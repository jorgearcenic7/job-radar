import os
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

try:
    import psycopg
except ModuleNotFoundError:
    psycopg = None

from job_radar.storage import RunRepository, SourceRunRecord, SourceSnapshot
from job_radar.storage.migrations import apply_pending


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(
    TEST_DATABASE_URL and psycopg is not None,
    "PostgreSQL de pruebas no configurado",
)
class RunRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_pending(TEST_DATABASE_URL)

    def setUp(self):
        self.repository = RunRepository(TEST_DATABASE_URL)
        self.run_ids = []

    def tearDown(self):
        if not self.run_ids:
            return

        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM ingestion_runs WHERE id = ANY(%s)",
                    (self.run_ids,),
                )

    def test_create_and_finish_ingestion_and_source_runs(self):
        run_id = self.repository.create_ingestion_run(2)
        self.run_ids.append(run_id)
        source_run_id = self.repository.create_source_run(
            run_id,
            company="Example",
            source="test:connector",
        )

        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT status, sources_total, finished_at
                    FROM ingestion_runs
                    WHERE id = %s
                    """,
                    (run_id,),
                )
                self.assertEqual(cursor.fetchone(), ("running", 2, None))

        self.repository.finish_source_run(
            source_run_id,
            status="failed",
            jobs_seen=4,
            jobs_new=2,
            jobs_closed=1,
            matches=3,
            duration_ms=1250,
            error_type="ValueError",
            error_message="invalid payload",
        )
        self.repository.finish_ingestion_run(
            run_id,
            status="partial",
            sources_succeeded=1,
            sources_failed=1,
            jobs_seen=10,
            jobs_new=3,
            jobs_closed=2,
            matches=5,
        )

        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        status,
                        jobs_seen,
                        jobs_new,
                        jobs_closed,
                        matches,
                        duration_ms,
                        snapshot_status,
                        closure_suppressed,
                        error_type,
                        error_message,
                        finished_at IS NOT NULL
                    FROM source_runs
                    WHERE id = %s
                    """,
                    (source_run_id,),
                )
                self.assertEqual(
                    cursor.fetchone(),
                    (
                        "failed",
                        4,
                        2,
                        1,
                        3,
                        1250,
                        "unknown",
                        False,
                        "ValueError",
                        "invalid payload",
                        True,
                    ),
                )
                cursor.execute(
                    """
                    SELECT
                        status,
                        sources_succeeded,
                        sources_failed,
                        jobs_seen,
                        jobs_new,
                        jobs_closed,
                        matches,
                        finished_at IS NOT NULL
                    FROM ingestion_runs
                    WHERE id = %s
                    """,
                    (run_id,),
                )
                self.assertEqual(
                    cursor.fetchone(),
                    ("partial", 1, 1, 10, 3, 2, 5, True),
                )

    def test_snapshot_history_returns_only_successful_runs(self):
        run_id = self.repository.create_ingestion_run(2)
        self.run_ids.append(run_id)

        successful_id = self.repository.create_source_run(
            run_id,
            company="History Example",
            source="test:history",
        )
        failed_id = self.repository.create_source_run(
            run_id,
            company="History Example",
            source="test:history",
        )
        self.repository.finish_source_run(
            successful_id,
            status="success",
            jobs_seen=10,
            jobs_new=1,
            jobs_closed=0,
            matches=2,
            duration_ms=100,
            snapshot_status="suspicious",
            closure_suppressed=True,
        )
        self.repository.finish_source_run(
            failed_id,
            status="failed",
            jobs_seen=99,
            jobs_new=0,
            jobs_closed=0,
            matches=0,
            duration_ms=100,
            snapshot_status="unknown",
        )

        history = self.repository.get_source_snapshot_history(
            company="History Example",
            source="test:history",
            limit=5,
        )

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].jobs_seen, 10)
        self.assertEqual(history[0].snapshot_status, "suspicious")
        self.assertTrue(history[0].closure_suppressed)

    def test_recent_source_runs_returns_completed_history_newest_first(self):
        run_id = self.repository.create_ingestion_run(3)
        self.run_ids.append(run_id)

        source_run_ids = [
            self.repository.create_source_run(
                run_id,
                company="Health Example",
                source="test:health",
            )
            for _index in range(3)
        ]
        for index, source_run_id in enumerate(source_run_ids):
            failed = index == 2
            self.repository.finish_source_run(
                source_run_id,
                status="failed" if failed else "success",
                jobs_seen=1,
                jobs_new=0,
                jobs_closed=0,
                matches=0,
                duration_ms=10,
                snapshot_status="unknown" if failed else "empty",
                error_type="TimeoutError" if failed else None,
                error_message="upstream timeout" if failed else None,
            )

        histories = self.repository.get_recent_source_runs(limit=1)
        history = histories[("Health Example", "test:health")]

        # The most recent success is retained beyond the metric window so the
        # health model can always report when this source last worked.
        self.assertEqual(len(history), 2)
        self.assertEqual([run.status for run in history], ["failed", "success"])
        self.assertEqual(history[0].error_type, "TimeoutError")
        self.assertEqual(history[0].error_message, "upstream timeout")
        self.assertIsNotNone(history[0].finished_at)
        self.assertEqual(
            [run.snapshot_status for run in history],
            ["unknown", "empty"],
        )



def fake_connection(rows=(), rowcount=1):
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchall.return_value = list(rows)
    cursor.rowcount = rowcount
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    return connection, cursor


@unittest.skipIf(psycopg is None, "psycopg no instalado")
class RunRepositoryWithoutDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.repository = RunRepository("postgresql://runs")

    def test_database_url_defaults_to_environment(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://env"}):
            self.assertEqual(
                RunRepository().database_url,
                "postgresql://env",
            )
            self.assertEqual(RunRepository("").database_url, "")

    def test_non_positive_limits_do_not_query(self):
        with patch("psycopg.connect") as connect:
            self.assertEqual(
                self.repository.get_source_snapshot_history(
                    company="Example",
                    source="test",
                    limit=0,
                ),
                [],
            )
            self.assertEqual(
                self.repository.get_recent_source_runs(limit=0),
                {},
            )

        connect.assert_not_called()

    def test_finishing_unknown_runs_fails(self):
        connection, _cursor = fake_connection(rowcount=0)
        metrics = {
            "jobs_seen": 0,
            "jobs_new": 0,
            "jobs_closed": 0,
            "matches": 0,
        }

        with patch("psycopg.connect", return_value=connection) as connect:
            with self.assertRaisesRegex(ValueError, "^ingestion_run .*: 1$"):
                self.repository.finish_ingestion_run(
                    1,
                    status="success",
                    sources_succeeded=0,
                    sources_failed=0,
                    **metrics,
                )
            with self.assertRaisesRegex(ValueError, "^source_run .*: 2$"):
                self.repository.finish_source_run(
                    2,
                    status="success",
                    duration_ms=0,
                    **metrics,
                )

        connect.assert_called_with("postgresql://runs", connect_timeout=10)

    def test_finish_source_run_defaults_and_parameter_order(self):
        connection, cursor = fake_connection()

        with patch("psycopg.connect", return_value=connection):
            self.repository.finish_source_run(
                9,
                status="success",
                jobs_seen=1,
                jobs_new=2,
                jobs_closed=3,
                matches=4,
                duration_ms=5,
            )

        self.assertEqual(
            cursor.execute.call_args.args[1],
            ("success", 1, 2, 3, 4, 5, "unknown", False, None, None, 9),
        )

    def test_snapshot_history_maps_rows(self):
        connection, cursor = fake_connection([(10, "healthy", False)])

        with patch("psycopg.connect", return_value=connection):
            history = self.repository.get_source_snapshot_history(
                company="Example",
                source="test",
                limit=3,
            )

        self.assertEqual(history, [SourceSnapshot(10, "healthy", False)])
        self.assertEqual(
            cursor.execute.call_args.args[1],
            ("Example", "test", 3),
        )

    def test_recent_source_runs_are_grouped_per_source_in_row_order(self):
        finished_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
        rows = [
            ("A", "s", "failed", finished_at, "E", "m", "unknown"),
            ("A", "s", "success", finished_at, None, None, "empty"),
            ("B", "s", "success", finished_at, None, None, None),
        ]
        connection, cursor = fake_connection(rows)

        with patch("psycopg.connect", return_value=connection):
            histories = self.repository.get_recent_source_runs(limit=4)

        self.assertEqual(cursor.execute.call_args.args[1], (4,))
        self.assertEqual(
            histories,
            {
                ("A", "s"): (
                    SourceRunRecord(*rows[0]),
                    SourceRunRecord(*rows[1]),
                ),
                ("B", "s"): (SourceRunRecord(*rows[2]),),
            },
        )


if __name__ == "__main__":
    unittest.main()
