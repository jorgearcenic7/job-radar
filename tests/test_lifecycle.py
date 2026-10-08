import os
import unittest

try:
    import psycopg
except ModuleNotFoundError:
    psycopg = None

from job_radar.domain import Job
from job_radar.matching import MATCH_RULES_VERSION, MatchReasonCode
from job_radar.storage import save_jobs
from job_radar.storage.migrations import apply_pending


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
COMPANY = "Lifecycle Test Company"
SOURCE = "test:connector"


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
                    "DELETE FROM jobs WHERE company = %s",
                    (COMPANY,),
                )

    def make_job(self, job_id, title, location=None):
        return Job(
            source=SOURCE,
            source_job_id=job_id,
            company=COMPANY,
            title=title,
            location=location,
            url=f"https://example.com/jobs/{job_id}",
            description="Python, SQL and data pipelines",
            salary_text=None,
            experience_text="2 years of experience",
        )

    def job_state(self, job_id):
        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT active, closed_at
                    FROM jobs
                    WHERE source = %s
                      AND source_job_id = %s
                    """,
                    (SOURCE, job_id),
                )
                return cursor.fetchone()

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
            [first, second],
            COMPANY,
        )
        self.assertEqual((new_count, closed_count), (2, 0))
        first_seen_at, initial_last_seen_at = self.job_timestamps("job-2")

        new_count, closed_count = save_jobs(
            [second],
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
            [first, second],
            COMPANY,
        )
        self.assertEqual((new_count, closed_count), (0, 0))

        active, closed_at = self.job_state("job-1")
        self.assertTrue(active)
        self.assertIsNone(closed_at)

    def test_empty_snapshot_does_not_close_jobs(self):
        job = self.make_job("job-safe", "Junior Data Engineer")
        save_jobs([job], COMPANY)

        result = save_jobs([], COMPANY)
        self.assertEqual(result, (0, 0))

        active, closed_at = self.job_state("job-safe")
        self.assertTrue(active)
        self.assertIsNone(closed_at)

    def test_nonempty_snapshot_can_suppress_missing_job_closures(self):
        first = self.make_job("job-1", "Data Engineer I")
        second = self.make_job("job-2", "Data Engineer II")
        save_jobs([first, second], COMPANY)

        result = save_jobs(
            [second],
            COMPANY,
            close_missing=False,
        )

        self.assertEqual(result, (0, 0))
        active, closed_at = self.job_state("job-1")
        self.assertTrue(active)
        self.assertIsNone(closed_at)

    def test_matching_metadata_is_persisted_and_refreshed(self):
        job = self.make_job(
            "job-metadata",
            "Data Engineer",
            location="Madrid, Spain",
        )
        save_jobs([job], COMPANY)

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

        save_jobs([job], COMPANY)
        refreshed = self.job_matching("job-metadata")
        self.assertEqual(refreshed[3], MATCH_RULES_VERSION)
        self.assertEqual(refreshed[4], matching[4])

    def test_rejected_job_has_explicit_null_matching_metadata(self):
        job = self.make_job(
            "job-rejected",
            "Senior Data Engineer",
            location="Madrid, Spain",
        )

        save_jobs([job], COMPANY)

        self.assertEqual(
            self.job_matching("job-rejected"),
            (False, None, None, None, None),
        )


if __name__ == "__main__":
    unittest.main()
