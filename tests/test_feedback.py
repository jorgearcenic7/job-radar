import io
import os
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

try:
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
except ModuleNotFoundError:
    psycopg = None

from job_radar.domain import (
    Job,
    PreparedJob,
    Relevance,
    RelevanceStats,
)
from job_radar.matching import MATCH_RULES_VERSION, MatchReasonCode
from job_radar.storage import (
    FeedbackJobNotFoundError,
    FeedbackJobNotSelectedError,
    FeedbackRepository,
    MissingMatchMetadataError,
    save_jobs,
)
from job_radar.storage.migrations import apply_pending
from scripts import job_feedback


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
COMPANY = "Feedback Test Company"
SOURCE = "test:feedback"
GOOD = "Buena coincidencia"
STRETCH = "Stretch"
NEXT_VERSION = MATCH_RULES_VERSION + 1


def make_prepared(
    job_id,
    *,
    selected=True,
    status=GOOD,
    codes=(MatchReasonCode.CORE_DATA_ROLE,),
    rules_version=MATCH_RULES_VERSION,
):
    return PreparedJob(
        job=Job(
            source=SOURCE,
            source_job_id=job_id,
            company=COMPANY,
            title=f"Data Engineer {job_id}",
            location="Madrid, Spain",
            url=f"https://example.com/jobs/{job_id}",
            description="Python, SQL and data pipelines",
            salary_text=None,
            experience_text=None,
        ),
        countries=("Spain",),
        selected=selected,
        match_status=status if selected else None,
        match_reason=f"{status}: test" if selected else None,
        match_rules_version=rules_version if selected else None,
        match_reason_codes=(
            tuple(str(code) for code in codes) if selected else None
        ),
    )


@unittest.skipUnless(
    TEST_DATABASE_URL and psycopg is not None,
    "PostgreSQL de pruebas no configurado",
)
class FeedbackRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = f"feedback_test_{uuid.uuid4().hex}"

        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("CREATE SCHEMA {}").format(
                        sql.Identifier(cls.schema)
                    )
                )

        cls.database_url = make_conninfo(
            TEST_DATABASE_URL,
            options=f"-c search_path={cls.schema}",
        )
        apply_pending(cls.database_url)

        cls.previous_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = cls.database_url

    @classmethod
    def tearDownClass(cls):
        if cls.previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = cls.previous_database_url

        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(cls.schema)
                    )
                )

    def setUp(self):
        self.execute("TRUNCATE job_feedback, jobs")
        self.repository = FeedbackRepository(self.database_url)

    def execute(self, query, parameters=None):
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, parameters)
                if cursor.description is not None:
                    return cursor.fetchall()
                return None

    def feedback_rows(self, job_id=None):
        return self.execute(
            """
            SELECT
                source_job_id,
                match_rules_version,
                relevance,
                match_status,
                match_reason_codes,
                created_at,
                updated_at
            FROM job_feedback
            WHERE %(job_id)s::TEXT IS NULL OR source_job_id = %(job_id)s
            ORDER BY source_job_id, match_rules_version
            """,
            {"job_id": job_id},
        )

    def rate(self, job_id, relevance):
        return self.repository.record(
            source=SOURCE,
            source_job_id=job_id,
            relevance=relevance,
        )

    def test_records_relevant_feedback(self):
        save_jobs([make_prepared("job-1")], COMPANY)

        recorded = self.rate("job-1", Relevance.RELEVANT)

        self.assertIsNone(recorded.previous_relevance)
        self.assertEqual(recorded.feedback.relevance, Relevance.RELEVANT)
        self.assertEqual(recorded.company, COMPANY)
        self.assertEqual(
            [row[2] for row in self.feedback_rows()],
            ["relevant"],
        )

    def test_records_not_relevant_feedback(self):
        save_jobs([make_prepared("job-1")], COMPANY)

        recorded = self.rate("job-1", Relevance.NOT_RELEVANT)

        self.assertEqual(
            recorded.feedback.relevance,
            Relevance.NOT_RELEVANT,
        )
        self.assertEqual(
            [row[2] for row in self.feedback_rows()],
            ["not_relevant"],
        )

    def test_rejects_unknown_job(self):
        with self.assertRaises(FeedbackJobNotFoundError):
            self.rate("missing", Relevance.RELEVANT)

        self.assertEqual(self.feedback_rows(), [])

    def test_rejects_job_that_was_not_selected(self):
        save_jobs([make_prepared("job-1", selected=False)], COMPANY)

        with self.assertRaises(FeedbackJobNotSelectedError):
            self.rate("job-1", Relevance.NOT_RELEVANT)

        self.assertEqual(self.feedback_rows(), [])

    def test_rejects_selected_job_without_rules_version(self):
        save_jobs([make_prepared("job-1")], COMPANY)
        self.execute(
            """
            UPDATE jobs
            SET match_rules_version = NULL,
                match_reason_codes = NULL
            """
        )

        with self.assertRaises(MissingMatchMetadataError):
            self.rate("job-1", Relevance.RELEVANT)

        self.assertEqual(self.feedback_rows(), [])

    def test_copies_matching_context_at_feedback_time(self):
        codes = (
            MatchReasonCode.CORE_DATA_ROLE,
            MatchReasonCode.LEVEL_UNSPECIFIED,
        )
        save_jobs(
            [make_prepared("job-1", status=STRETCH, codes=codes)],
            COMPANY,
        )

        recorded = self.rate("job-1", Relevance.RELEVANT)

        expected_codes = ["CORE_DATA_ROLE", "LEVEL_UNSPECIFIED"]
        self.assertEqual(recorded.feedback.match_status, STRETCH)
        self.assertEqual(
            recorded.feedback.match_rules_version,
            MATCH_RULES_VERSION,
        )
        self.assertEqual(
            list(recorded.feedback.match_reason_codes),
            expected_codes,
        )
        row = self.feedback_rows()[0]
        self.assertEqual(
            row[1:5],
            (MATCH_RULES_VERSION, "relevant", STRETCH, expected_codes),
        )

    def test_updates_existing_feedback_for_same_version(self):
        save_jobs([make_prepared("job-1")], COMPANY)
        first = self.rate("job-1", Relevance.RELEVANT)

        second = self.rate("job-1", Relevance.NOT_RELEVANT)

        self.assertEqual(second.previous_relevance, Relevance.RELEVANT)
        rows = self.feedback_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], "not_relevant")
        self.assertEqual(rows[0][5], first.feedback.created_at)
        self.assertGreater(rows[0][6], first.feedback.updated_at)

    def test_new_rules_version_gets_independent_feedback(self):
        save_jobs([make_prepared("job-1")], COMPANY)
        self.rate("job-1", Relevance.RELEVANT)

        save_jobs(
            [
                make_prepared(
                    "job-1",
                    status=STRETCH,
                    codes=(MatchReasonCode.TECHNICAL_ML_ROLE,),
                    rules_version=NEXT_VERSION,
                )
            ],
            COMPANY,
        )
        self.rate("job-1", Relevance.NOT_RELEVANT)

        self.assertEqual(
            [row[1:5] for row in self.feedback_rows()],
            [
                (
                    MATCH_RULES_VERSION,
                    "relevant",
                    GOOD,
                    ["CORE_DATA_ROLE"],
                ),
                (
                    NEXT_VERSION,
                    "not_relevant",
                    STRETCH,
                    ["TECHNICAL_ML_ROLE"],
                ),
            ],
        )

    def seed_report_data(self):
        save_jobs(
            [
                make_prepared(
                    "a",
                    codes=(
                        MatchReasonCode.CORE_DATA_ROLE,
                        MatchReasonCode.EXPERIENCE_COMPATIBLE,
                    ),
                ),
                make_prepared(
                    "b",
                    codes=(
                        MatchReasonCode.CORE_DATA_ROLE,
                        MatchReasonCode.LEVEL_I_II,
                    ),
                ),
                make_prepared(
                    "c",
                    status=STRETCH,
                    codes=(
                        MatchReasonCode.CORE_DATA_ROLE,
                        MatchReasonCode.LEVEL_UNSPECIFIED,
                    ),
                ),
                make_prepared(
                    "d",
                    status=STRETCH,
                    codes=(
                        MatchReasonCode.TECHNICAL_ML_ROLE,
                        MatchReasonCode.LEVEL_UNSPECIFIED,
                    ),
                ),
            ],
            COMPANY,
        )
        self.rate("a", Relevance.RELEVANT)
        self.rate("b", Relevance.RELEVANT)
        self.rate("c", Relevance.NOT_RELEVANT)
        self.rate("d", Relevance.RELEVANT)
        save_jobs(
            [
                make_prepared(
                    "d",
                    status=STRETCH,
                    codes=(MatchReasonCode.TECHNICAL_ML_ROLE,),
                    rules_version=NEXT_VERSION,
                )
            ],
            COMPANY,
            close_missing=False,
        )
        self.rate("d", Relevance.NOT_RELEVANT)

    def test_report_overall(self):
        self.seed_report_data()

        overall = self.repository.get_report().overall

        self.assertEqual(
            (overall.labeled, overall.relevant, overall.not_relevant),
            (5, 3, 2),
        )
        self.assertAlmostEqual(overall.relevance_rate, 0.6)

    def test_empty_report_has_no_rate(self):
        report = self.repository.get_report()

        self.assertEqual(report.overall.labeled, 0)
        self.assertIsNone(report.overall.relevance_rate)
        self.assertEqual(report.by_reason_code, ())

    def test_report_by_match_status(self):
        self.seed_report_data()

        self.assertEqual(
            self.repository.get_report().by_match_status,
            (
                RelevanceStats(key=GOOD, labeled=2, relevant=2),
                RelevanceStats(key=STRETCH, labeled=3, relevant=1),
            ),
        )

    def test_report_by_rules_version(self):
        self.seed_report_data()

        self.assertEqual(
            self.repository.get_report().by_rules_version,
            (
                RelevanceStats(
                    key=str(MATCH_RULES_VERSION),
                    labeled=4,
                    relevant=3,
                ),
                RelevanceStats(
                    key=str(NEXT_VERSION),
                    labeled=1,
                    relevant=0,
                ),
            ),
        )

        filtered = self.repository.get_report(
            rules_version=MATCH_RULES_VERSION,
        )
        self.assertEqual(
            (filtered.overall.labeled, filtered.overall.relevant),
            (4, 3),
        )
        self.assertEqual(
            filtered.by_match_status,
            (
                RelevanceStats(key=GOOD, labeled=2, relevant=2),
                RelevanceStats(key=STRETCH, labeled=2, relevant=1),
            ),
        )

    def test_report_by_reason_code_lists_false_positives_first(self):
        self.seed_report_data()

        self.assertEqual(
            self.repository.get_report().by_reason_code,
            (
                RelevanceStats(
                    key="CORE_DATA_ROLE",
                    labeled=3,
                    relevant=2,
                ),
                RelevanceStats(
                    key="LEVEL_UNSPECIFIED",
                    labeled=2,
                    relevant=1,
                ),
                RelevanceStats(
                    key="TECHNICAL_ML_ROLE",
                    labeled=2,
                    relevant=1,
                ),
                RelevanceStats(
                    key="EXPERIENCE_COMPATIBLE",
                    labeled=1,
                    relevant=1,
                ),
                RelevanceStats(key="LEVEL_I_II", labeled=1, relevant=1),
            ),
        )

    def test_closed_job_keeps_feedback(self):
        save_jobs([make_prepared("job-1"), make_prepared("job-2")], COMPANY)
        self.rate("job-1", Relevance.RELEVANT)
        before = self.feedback_rows("job-1")

        new_count, closed_count = save_jobs(
            [make_prepared("job-2")],
            COMPANY,
        )

        self.assertEqual((new_count, closed_count), (0, 1))
        self.assertEqual(
            self.execute(
                "SELECT active FROM jobs WHERE source_job_id = 'job-1'"
            ),
            [(False,)],
        )
        self.assertEqual(self.feedback_rows("job-1"), before)

    def test_lifecycle_does_not_modify_feedback(self):
        save_jobs([make_prepared("job-1"), make_prepared("job-2")], COMPANY)
        self.rate("job-1", Relevance.RELEVANT)
        before = self.feedback_rows("job-1")

        save_jobs([make_prepared("job-2")], COMPANY)
        save_jobs(
            [make_prepared("job-1", selected=False), make_prepared("job-2")],
            COMPANY,
        )

        self.assertEqual(
            self.execute(
                """
                SELECT active, selected, match_rules_version
                FROM jobs
                WHERE source_job_id = 'job-1'
                """
            ),
            [(True, False, None)],
        )
        self.assertEqual(self.feedback_rows("job-1"), before)

        with self.assertRaises(psycopg.IntegrityError):
            self.execute("DELETE FROM jobs WHERE source_job_id = 'job-1'")

    def test_ingestion_continues_with_existing_feedback(self):
        save_jobs([make_prepared("job-1"), make_prepared("job-2")], COMPANY)
        self.rate("job-1", Relevance.RELEVANT)
        self.rate("job-2", Relevance.NOT_RELEVANT)

        result = save_jobs(
            [
                make_prepared("job-1", status=STRETCH),
                make_prepared("job-3"),
            ],
            COMPANY,
        )

        self.assertEqual(result, (1, 1))
        self.assertEqual(
            self.execute(
                """
                SELECT source_job_id, active, match_status
                FROM jobs
                ORDER BY source_job_id
                """
            ),
            [
                ("job-1", True, STRETCH),
                ("job-2", False, GOOD),
                ("job-3", True, GOOD),
            ],
        )
        self.assertEqual(len(self.feedback_rows()), 2)

    def test_schema_rejects_unknown_relevance(self):
        save_jobs([make_prepared("job-1")], COMPANY)

        with self.assertRaises(psycopg.errors.CheckViolation):
            self.execute(
                """
                INSERT INTO job_feedback (
                    source,
                    source_job_id,
                    match_rules_version,
                    relevance,
                    match_status,
                    match_reason_codes
                )
                VALUES (%s, 'job-1', %s, 'maybe', %s, ARRAY['CORE_DATA_ROLE'])
                """,
                (SOURCE, MATCH_RULES_VERSION, GOOD),
            )

    def test_pending_lists_unlabeled_active_recommendations(self):
        save_jobs(
            [
                make_prepared("job-1"),
                make_prepared("job-2"),
                make_prepared("job-3", selected=False),
            ],
            COMPANY,
        )
        self.rate("job-1", Relevance.RELEVANT)

        self.assertEqual(
            [job.source_job_id for job in self.repository.get_pending()],
            ["job-2"],
        )

        save_jobs(
            [
                make_prepared("job-1", rules_version=NEXT_VERSION),
                make_prepared("job-2"),
            ],
            COMPANY,
        )

        self.assertEqual(
            sorted(
                job.source_job_id
                for job in self.repository.get_pending()
            ),
            ["job-1", "job-2"],
        )

    def run_command(self, argv):
        output = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            result = job_feedback.main(argv)
        return result, output.getvalue(), errors.getvalue()

    def test_command_rates_updates_and_reports(self):
        save_jobs([make_prepared("job-1")], COMPANY)
        rate = [
            "rate",
            "--source",
            SOURCE,
            "--job-id",
            "job-1",
            "--relevance",
        ]

        result, output, _ = self.run_command(rate + ["relevant"])
        self.assertEqual(result, 0)
        self.assertTrue(output.startswith("CREATED relevance=relevant"))

        result, output, _ = self.run_command(rate + ["not_relevant"])
        self.assertEqual(result, 0)
        self.assertTrue(
            output.startswith(
                "UPDATED from=relevant relevance=not_relevant"
            )
        )

        result, output, _ = self.run_command(["report"])
        self.assertEqual(result, 0)
        self.assertIn("TOTAL LABELED   1", output)
        self.assertIn("RELEVANCE RATE  0.0% (0/1)", output)
        self.assertIn("no mide recall", output)
        self.assertEqual(len(self.feedback_rows()), 1)

    def test_command_reports_feedback_errors(self):
        result, output, errors = self.run_command(
            [
                "rate",
                "--source",
                SOURCE,
                "--job-id",
                "missing",
                "--relevance",
                "relevant",
            ]
        )

        self.assertEqual(result, 1)
        self.assertEqual(output, "")
        self.assertIn("oferta inexistente", errors)
        self.assertNotIn(self.database_url, errors)


class FeedbackCommandArgumentTests(unittest.TestCase):
    def test_rejects_unknown_relevance(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://test"}):
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    job_feedback.main(
                        [
                            "rate",
                            "--source",
                            SOURCE,
                            "--job-id",
                            "job-1",
                            "--relevance",
                            "maybe",
                        ]
                    )

        self.assertEqual(raised.exception.code, 2)

    def test_requires_database_url(self):
        with patch.dict(os.environ, {"DATABASE_URL": ""}):
            with redirect_stderr(io.StringIO()) as errors:
                with self.assertRaises(SystemExit) as raised:
                    job_feedback.main(["report"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("Falta DATABASE_URL", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
