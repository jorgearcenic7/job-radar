import os
import unittest
from pathlib import Path

import psycopg

import main


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
COMPANY = "Lifecycle Test Company"
SOURCE = "test:connector"


@unittest.skipUnless(
    TEST_DATABASE_URL,
    "TEST_DATABASE_URL no configurada",
)
class LifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = TEST_DATABASE_URL

        schema = Path("sql/001_create_jobs.sql").read_text()

        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(schema)

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

    def make_job(self, job_id, title):
        return {
            "source": SOURCE,
            "source_job_id": job_id,
            "company": COMPANY,
            "title": title,
            "location": None,
            "url": f"https://example.com/jobs/{job_id}",
            "description": "Python, SQL and data pipelines",
            "salary_text": None,
            "experience_text": "2 years of experience",
        }

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

    def test_insert_close_and_reactivate(self):
        first = self.make_job("job-1", "Data Engineer I")
        second = self.make_job("job-2", "Data Engineer II")

        new_count, closed_count = main.save_jobs(
            [first, second],
            COMPANY,
        )
        self.assertEqual((new_count, closed_count), (2, 0))

        new_count, closed_count = main.save_jobs(
            [second],
            COMPANY,
        )
        self.assertEqual((new_count, closed_count), (0, 1))

        active, closed_at = self.job_state("job-1")
        self.assertFalse(active)
        self.assertIsNotNone(closed_at)

        new_count, closed_count = main.save_jobs(
            [first, second],
            COMPANY,
        )
        self.assertEqual((new_count, closed_count), (0, 0))

        active, closed_at = self.job_state("job-1")
        self.assertTrue(active)
        self.assertIsNone(closed_at)

    def test_empty_snapshot_does_not_close_jobs(self):
        job = self.make_job("job-safe", "Junior Data Engineer")
        main.save_jobs([job], COMPANY)

        result = main.save_jobs([], COMPANY)
        self.assertEqual(result, (0, 0))

        active, closed_at = self.job_state("job-safe")
        self.assertTrue(active)
        self.assertIsNone(closed_at)


if __name__ == "__main__":
    unittest.main()
