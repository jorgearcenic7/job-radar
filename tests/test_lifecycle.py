import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

try:
    import psycopg
except ModuleNotFoundError:
    psycopg = None

from job_radar.domain import Job, PreparedJob
from job_radar.matching import (
    MATCH_RULES_VERSION,
    MatchReasonCode,
    prepare_job,
)
from job_radar.notifications import build_match_email
from job_radar.storage import (
    close_retired_companies,
    get_active_matches,
    save_jobs,
)
from job_radar.storage.postgres import STALE_AFTER_DAYS
from job_radar.storage.migrations import apply_pending


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
POSTGRES_LOGGER = "job_radar.storage.postgres"
COMPANY = "Lifecycle Test Company"
RETIRED_COMPANY = "Lifecycle Retired Company"
SOURCE = "test:connector"
PREVIOUS_SOURCE = "test:previous-ats"


def prepare_jobs(*jobs):
    return [prepare_job(job) for job in jobs]


@unittest.skipUnless(
    TEST_DATABASE_URL and psycopg is not None,
    "PostgreSQL de pruebas no configurado",
)
class LifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = TEST_DATABASE_URL

        apply_pending(TEST_DATABASE_URL)

    @classmethod
    def tearDownClass(cls):
        if cls.previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = cls.previous_database_url

    def setUp(self):
        self.delete_test_jobs()

    def tearDown(self):
        self.delete_test_jobs()

    def delete_test_jobs(self):
        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM jobs WHERE company = ANY(%s)",
                    ([COMPANY, RETIRED_COMPANY],),
                )

    def make_job(
        self,
        job_id,
        title,
        location=None,
        *,
        source=SOURCE,
        company=COMPANY,
    ):
        return Job(
            source=source,
            source_job_id=job_id,
            company=company,
            title=title,
            location=location,
            url=f"https://example.com/jobs/{job_id}",
            description="Python, SQL and data pipelines",
            salary_text=None,
            experience_text="2 years of experience",
        )

    def job_state(self, job_id, source=SOURCE):
        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT active, closed_at
                    FROM jobs
                    WHERE source = %s
                      AND source_job_id = %s
                    """,
                    (source, job_id),
                )
                return cursor.fetchone()

    def other_active_companies(self):
        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT DISTINCT company
                    FROM jobs
                    WHERE active
                      AND company <> %s
                    """,
                    (RETIRED_COMPANY,),
                )
                return [row[0] for row in cursor.fetchall()]

    def job_timestamps(self, job_id):
        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT first_seen_at, last_seen_at
                    FROM jobs
                    WHERE source = %s
                      AND source_job_id = %s
                    """,
                    (SOURCE, job_id),
                )
                return cursor.fetchone()

    def job_matching(self, job_id):
        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        selected,
                        match_status,
                        match_reason,
                        match_rules_version,
                        match_reason_codes
                    FROM jobs
                    WHERE source = %s
                      AND source_job_id = %s
                    """,
                    (SOURCE, job_id),
                )
                return cursor.fetchone()

    def test_insert_close_and_reactivate(self):
        first = self.make_job("job-1", "Data Engineer I")
        second = self.make_job("job-2", "Data Engineer II")

        new_count, closed_count = save_jobs(
            prepare_jobs(first, second),
            COMPANY,
        )
        self.assertEqual((new_count, closed_count), (2, 0))
        first_seen_at, initial_last_seen_at = self.job_timestamps("job-2")

        new_count, closed_count = save_jobs(
            prepare_jobs(second),
            COMPANY,
        )
        self.assertEqual((new_count, closed_count), (0, 1))
        next_first_seen_at, next_last_seen_at = self.job_timestamps("job-2")
        self.assertEqual(next_first_seen_at, first_seen_at)
        self.assertGreaterEqual(next_last_seen_at, initial_last_seen_at)

        active, closed_at = self.job_state("job-1")
        self.assertFalse(active)
        self.assertIsNotNone(closed_at)

        new_count, closed_count = save_jobs(
            prepare_jobs(first, second),
            COMPANY,
        )
        self.assertEqual((new_count, closed_count), (0, 0))

        active, closed_at = self.job_state("job-1")
        self.assertTrue(active)
        self.assertIsNone(closed_at)

    def shift_last_seen(self, job_id, days):
        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE jobs
                    SET last_seen_at = CURRENT_TIMESTAMP
                        - make_interval(days => %s)
                    WHERE source = %s
                      AND source_job_id = %s
                    """,
                    (days, SOURCE, job_id),
                )

    def test_active_matches_hide_jobs_stale_relative_to_last_ingestion(
        self,
    ):
        fresh, stale = (
            self.make_job(job_id, "Junior Data Engineer", "Madrid, Spain")
            for job_id in ("job-fresh", "job-stale")
        )
        save_jobs(prepare_jobs(fresh, stale), COMPANY)
        # Pipeline detenido 30 días: lo último ingerido sigue visible y solo
        # se oculta lo que ya estaba atrasado respecto a esa ingesta.
        self.shift_last_seen("job-fresh", 30)
        self.shift_last_seen("job-stale", 30 + STALE_AFTER_DAYS + 1)

        with self.assertLogs("job_radar.storage.postgres", "WARNING") as logs:
            urls = {
                match["url"]
                for match in get_active_matches()
                if match["company"] == COMPANY
            }

        self.assertIn("stale_matches_hidden count=1", logs.output[0])
        self.assertIn(fresh.url, urls)
        self.assertNotIn(stale.url, urls)
        self.assertTrue(self.job_state("job-stale")[0])

    def test_new_matches_reach_email_with_aware_first_seen(self):
        run_started_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        job = self.make_job("job-new", "Junior Data Engineer", "Madrid, Spain")
        save_jobs(prepare_jobs(job), COMPANY)

        matches = [
            match for match in get_active_matches()
            if match["company"] == COMPANY
        ]
        subject, _html, _text = build_match_email(
            matches,
            new_since=run_started_at,
        )

        self.assertIsNotNone(matches[0]["first_seen_at"].tzinfo)
        self.assertIn("1 nueva", subject)

    def test_listed_ids_without_detail_are_not_closed(self):
        listed = self.make_job("job-listed", "Data Engineer I")
        failed = self.make_job("job-detail-failed", "Data Engineer II")
        gone = self.make_job("job-gone", "Data Engineer III")
        save_jobs(prepare_jobs(listed, failed, gone), COMPANY)

        _new, closed = save_jobs(
            prepare_jobs(listed),
            COMPANY,
            keep_ids={"job-detail-failed"},
        )

        self.assertEqual(closed, 1)
        self.assertTrue(self.job_state("job-detail-failed")[0])
        self.assertFalse(self.job_state("job-gone")[0])

    def test_empty_snapshot_does_not_close_jobs(self):
        job = self.make_job("job-safe", "Junior Data Engineer")
        save_jobs(prepare_jobs(job), COMPANY)

        result = save_jobs([], COMPANY)
        self.assertEqual(result, (0, 0))

        active, closed_at = self.job_state("job-safe")
        self.assertTrue(active)
        self.assertIsNone(closed_at)

    def test_nonempty_snapshot_can_suppress_missing_job_closures(self):
        first = self.make_job("job-1", "Data Engineer I")
        second = self.make_job("job-2", "Data Engineer II")
        save_jobs(prepare_jobs(first, second), COMPANY)

        result = save_jobs(
            prepare_jobs(second),
            COMPANY,
            close_missing=False,
        )

        self.assertEqual(result, (0, 0))
        active, closed_at = self.job_state("job-1")
        self.assertTrue(active)
        self.assertIsNone(closed_at)

    def test_source_change_closes_jobs_of_previous_source(self):
        # Caso Nubank: la empresa pasa de un ATS a otro.
        old = self.make_job("job-old", "Data Engineer", source=PREVIOUS_SOURCE)
        save_jobs(prepare_jobs(old), COMPANY)

        new_count, closed_count = save_jobs(
            prepare_jobs(self.make_job("job-new", "Data Engineer")),
            COMPANY,
        )

        self.assertEqual((new_count, closed_count), (1, 1))
        self.assertFalse(self.job_state("job-old", PREVIOUS_SOURCE)[0])
        self.assertTrue(self.job_state("job-new")[0])

    def test_suppressed_closures_keep_jobs_of_previous_source(self):
        old = self.make_job("job-old", "Data Engineer", source=PREVIOUS_SOURCE)
        save_jobs(prepare_jobs(old), COMPANY)

        result = save_jobs(
            prepare_jobs(self.make_job("job-new", "Data Engineer")),
            COMPANY,
            close_missing=False,
        )

        self.assertEqual(result, (1, 0))
        self.assertTrue(self.job_state("job-old", PREVIOUS_SOURCE)[0])

    def test_retired_company_is_closed_and_reactivates_if_it_returns(self):
        retired = self.make_job(
            "job-retired",
            "Data Engineer",
            company=RETIRED_COMPANY,
        )
        kept = self.make_job("job-kept", "Data Engineer")
        save_jobs(prepare_jobs(retired), RETIRED_COMPANY)
        save_jobs(prepare_jobs(kept), COMPANY)

        # Se configuran todas las empresas activas salvo la retirada para no
        # cerrar datos de otros tests que compartan la base.
        configured = self.other_active_companies()
        self.assertIn(COMPANY, configured)

        with self.assertLogs(POSTGRES_LOGGER, "WARNING") as logs:
            closed = close_retired_companies(configured)

        self.assertEqual(closed, 1)
        self.assertIn(
            f"retired_company_jobs_closed company={RETIRED_COMPANY!r} "
            "jobs_closed=1",
            logs.output[0],
        )
        active, closed_at = self.job_state("job-retired")
        self.assertFalse(active)
        self.assertIsNotNone(closed_at)
        self.assertTrue(self.job_state("job-kept")[0])

        save_jobs(prepare_jobs(retired), RETIRED_COMPANY)
        self.assertEqual(self.job_state("job-retired"), (True, None))

    def test_matching_metadata_is_persisted_and_refreshed(self):
        job = self.make_job(
            "job-metadata",
            "Data Engineer",
            location="Madrid, Spain",
        )
        save_jobs(prepare_jobs(job), COMPANY)

        matching = self.job_matching("job-metadata")
        self.assertTrue(matching[0])
        self.assertEqual(matching[1], "Buena coincidencia")
        self.assertIsNotNone(matching[2])
        self.assertEqual(matching[3], MATCH_RULES_VERSION)
        self.assertEqual(
            matching[4],
            [
                MatchReasonCode.CORE_DATA_ROLE.value,
                MatchReasonCode.EXPERIENCE_COMPATIBLE.value,
                MatchReasonCode.STRONG_DATA_SIGNALS.value,
            ],
        )

        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE jobs
                    SET match_rules_version = 0,
                        match_reason_codes = ARRAY['STALE']::TEXT[]
                    WHERE source = %s
                      AND source_job_id = %s
                    """,
                    (SOURCE, job.source_job_id),
                )

        save_jobs(prepare_jobs(job), COMPANY)
        refreshed = self.job_matching("job-metadata")
        self.assertEqual(refreshed[3], MATCH_RULES_VERSION)
        self.assertEqual(refreshed[4], matching[4])

    def test_rejected_job_has_explicit_null_matching_metadata(self):
        job = self.make_job(
            "job-rejected",
            "Senior Data Engineer",
            location="Madrid, Spain",
        )

        save_jobs(prepare_jobs(job), COMPANY)

        self.assertEqual(
            self.job_matching("job-rejected"),
            (False, None, None, None, None),
        )

    def test_save_jobs_persists_only_prepared_values(self):
        job = self.make_job(
            "job-prepared",
            "Senior Data Engineer",
            location="London, UK",
        )
        prepared = PreparedJob(
            job=job,
            countries=("Prepared Country",),
            selected=True,
            match_status="Prepared status",
            match_reason="Prepared human reason",
            match_rules_version=999,
            match_reason_codes=("PREPARED_REASON",),
        )

        save_jobs([prepared], COMPANY)

        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        countries,
                        selected,
                        match_status,
                        match_reason,
                        match_rules_version,
                        match_reason_codes
                    FROM jobs
                    WHERE source = %s
                      AND source_job_id = %s
                    """,
                    (SOURCE, job.source_job_id),
                )
                self.assertEqual(
                    cursor.fetchone(),
                    (
                        ["Prepared Country"],
                        True,
                        "Prepared status",
                        "Prepared human reason",
                        999,
                        ["PREPARED_REASON"],
                    ),
                )



class FakeCursor:
    """Cursor that returns scripted ``(rows, rowcount)`` per statement."""

    def __init__(self, *results, columns=()):
        self.results = list(results)
        self.description = [SimpleNamespace(name=name) for name in columns]
        self.statements = []
        self.rows = []
        self.rowcount = -1

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def cursor(self):
        return self

    def _next_result(self):
        rows, self.rowcount = self.results.pop(0) if self.results else ([], -1)
        self.rows = list(rows)

    def execute(self, statement, parameters=None):
        self.statements.append((" ".join(statement.split()), parameters))
        self._next_result()

    def executemany(self, statement, parameters):
        self.statements.append((" ".join(statement.split()), list(parameters)))
        self._next_result()

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows


@unittest.skipIf(psycopg is None, "psycopg no instalado")
class LifecycleWithoutDatabaseTests(unittest.TestCase):
    def make_job(self, job_id, source=SOURCE):
        return Job(
            source=source,
            source_job_id=job_id,
            company=COMPANY,
            title="Junior Data Engineer",
            location="Madrid, Spain",
            url=f"https://example.com/jobs/{job_id}",
            description="Python, SQL and data pipelines",
            salary_text=None,
            experience_text="2 years of experience",
        )

    def test_empty_snapshot_returns_without_connecting(self):
        with patch("psycopg.connect") as connect:
            with self.assertLogs(POSTGRES_LOGGER, "WARNING") as logs:
                result = save_jobs([], COMPANY)

        self.assertEqual(result, (0, 0))
        connect.assert_not_called()
        self.assertIn("empty_snapshot_protected", logs.output[0])

    def test_snapshot_with_several_sources_is_rejected(self):
        jobs = prepare_jobs(
            self.make_job("job-1"),
            self.make_job("job-2", source="other:connector"),
        )

        with patch("psycopg.connect") as connect:
            with self.assertRaisesRegex(ValueError, "varias fuentes"):
                save_jobs(jobs, COMPANY)

        connect.assert_not_called()

    def test_snapshot_with_another_company_is_rejected(self):
        jobs = prepare_jobs(self.make_job("job-1"))

        with patch("psycopg.connect") as connect:
            with self.assertRaisesRegex(ValueError, "otra empresa"):
                save_jobs(jobs, "Renamed Company")

        connect.assert_not_called()

    def test_upsert_counts_new_ids_and_closes_missing_except_kept(self):
        cursor = FakeCursor(
            ([("job-1",), ("job-old",)], 2),
            ([], 2),
            ([], 3),
        )

        environment = {"DATABASE_URL": "postgresql://lifecycle"}

        with patch.dict(os.environ, environment):
            with patch("psycopg.connect", return_value=cursor) as connect:
                result = save_jobs(
                    prepare_jobs(
                        self.make_job("job-1"),
                        self.make_job("job-2"),
                    ),
                    COMPANY,
                    keep_ids={"job-detail-failed"},
                )

        self.assertEqual(result, (1, 3))
        connect.assert_called_once_with(
            "postgresql://lifecycle",
            connect_timeout=10,
        )
        select, upsert, close = cursor.statements
        self.assertEqual(select[1], (COMPANY, SOURCE))
        self.assertTrue(upsert[0].startswith("INSERT INTO jobs"))
        self.assertEqual(
            [row[:2] for row in upsert[1]],
            [(SOURCE, "job-1"), (SOURCE, "job-2")],
        )
        self.assertTrue(close[0].startswith("UPDATE jobs SET active = FALSE"))
        # El cierre abarca la empresa entera, no solo la fuente actual.
        self.assertIn(
            "WHERE company = %s AND active = TRUE "
            "AND NOT (source = %s AND source_job_id = ANY(%s))",
            close[0],
        )
        self.assertEqual(close[1][:2], (COMPANY, SOURCE))
        self.assertEqual(
            sorted(close[1][2]),
            ["job-1", "job-2", "job-detail-failed"],
        )

    def test_suppressed_closures_only_upsert(self):
        cursor = FakeCursor(([], 0), ([], 1))

        with patch("psycopg.connect", return_value=cursor):
            result = save_jobs(
                prepare_jobs(self.make_job("job-1")),
                COMPANY,
                close_missing=False,
            )

        self.assertEqual(result, (1, 0))
        self.assertEqual(len(cursor.statements), 2)

    def test_upsert_row_contains_prepared_values_in_column_order(self):
        prepared = PreparedJob(
            job=self.make_job("job-1"),
            countries=("Spain",),
            selected=True,
            match_status="Stretch",
            match_reason="human reason",
            match_rules_version=7,
            match_reason_codes=("CODE",),
        )
        rejected = PreparedJob(
            job=self.make_job("job-2"),
            countries=(),
            selected=False,
            match_status=None,
            match_reason=None,
            match_rules_version=None,
            match_reason_codes=None,
        )
        cursor = FakeCursor()

        with patch("psycopg.connect", return_value=cursor):
            save_jobs([prepared, rejected], COMPANY, close_missing=False)

        job = prepared.job
        self.assertEqual(
            cursor.statements[1][1],
            [
                (
                    SOURCE, "job-1", COMPANY, job.title, job.location,
                    ["Spain"], job.url, job.description, None,
                    job.experience_text, True, "Stretch", "human reason",
                    7, ["CODE"],
                ),
                (
                    SOURCE, "job-2", COMPANY, job.title, job.location,
                    [], rejected.job.url, job.description, None,
                    job.experience_text, False, None, None, None, None,
                ),
            ],
        )

    def test_active_matches_drop_stale_rows_and_freshness_column(self):
        cursor = FakeCursor(
            ([("Fresh", True), ("Stale", False), ("Unknown", None)], 3),
            columns=("company", "fresh"),
        )

        with patch("psycopg.connect", return_value=cursor):
            with self.assertLogs(POSTGRES_LOGGER, "WARNING") as logs:
                matches = get_active_matches()

        self.assertEqual(matches, [{"company": "Fresh"}])
        self.assertEqual(cursor.statements[0][1], (STALE_AFTER_DAYS,))
        self.assertEqual(
            logs.output,
            [
                "WARNING:job_radar.storage.postgres:stale_matches_hidden "
                f"count=2 stale_after_days={STALE_AFTER_DAYS}",
            ],
        )

    def test_retire_with_empty_registry_never_connects(self):
        with patch("psycopg.connect") as connect:
            for configured in ([], (), iter(())):
                with self.assertRaisesRegex(ValueError, "Registry sin"):
                    close_retired_companies(configured)

        connect.assert_not_called()

    def test_retire_closes_unconfigured_companies_and_logs_each(self):
        cursor = FakeCursor(
            ([("Revolut",), ("Old Co",), ("Revolut",)], 3),
        )

        with patch("psycopg.connect", return_value=cursor):
            with self.assertLogs(POSTGRES_LOGGER, "WARNING") as logs:
                closed = close_retired_companies(
                    ["Stripe", "Adyen", "Stripe"],
                )

        self.assertEqual(closed, 3)
        statement, parameters = cursor.statements[0]
        self.assertEqual(
            statement,
            "UPDATE jobs SET active = FALSE, closed_at = CURRENT_TIMESTAMP "
            "WHERE active = TRUE AND NOT (company = ANY(%s)) "
            "RETURNING company",
        )
        self.assertEqual(parameters, (["Adyen", "Stripe"],))
        self.assertEqual(
            logs.output,
            [
                "WARNING:job_radar.storage.postgres:"
                "retired_company_jobs_closed company='Old Co' jobs_closed=1",
                "WARNING:job_radar.storage.postgres:"
                "retired_company_jobs_closed company='Revolut' jobs_closed=2",
            ],
        )

    def test_retire_without_matches_logs_nothing(self):
        cursor = FakeCursor(([], 0))

        with patch("psycopg.connect", return_value=cursor):
            with self.assertNoLogs(POSTGRES_LOGGER, "WARNING"):
                self.assertEqual(close_retired_companies(["Stripe"]), 0)


if __name__ == "__main__":
    unittest.main()
