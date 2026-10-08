import unittest

from job_radar.domain import Job, PreparedJob
from job_radar.matching import (
    MATCH_RULES_VERSION,
    MatchReasonCode,
    prepare_job,
)


def make_job(title="Data Engineer", location="Madrid, Spain"):
    return Job(
        source="test:preparation",
        source_job_id="job-1",
        company="Example",
        title=title,
        location=location,
        url="https://example.com/jobs/job-1",
        description="Python, SQL and data pipelines",
        salary_text=None,
        experience_text="2 years of experience",
    )


class PrepareJobTests(unittest.TestCase):
    def test_prepares_matching_countries_and_preserves_job(self):
        job = make_job()

        prepared = prepare_job(job)

        self.assertIsInstance(prepared, PreparedJob)
        self.assertIs(prepared.job, job)
        self.assertEqual(prepared.countries, ("Spain",))
        self.assertTrue(prepared.selected)
        self.assertEqual(prepared.match_status, "Buena coincidencia")
        self.assertIsNotNone(prepared.match_reason)
        self.assertEqual(prepared.match_rules_version, MATCH_RULES_VERSION)
        self.assertEqual(
            prepared.match_reason_codes,
            (
                MatchReasonCode.CORE_DATA_ROLE.value,
                MatchReasonCode.EXPERIENCE_COMPATIBLE.value,
                MatchReasonCode.STRONG_DATA_SIGNALS.value,
            ),
        )

    def test_rejected_job_is_explicitly_prepared(self):
        job = make_job(title="Senior Data Engineer")

        prepared = prepare_job(job)

        self.assertIs(prepared.job, job)
        self.assertEqual(prepared.countries, ("Spain",))
        self.assertFalse(prepared.selected)
        self.assertIsNone(prepared.match_status)
        self.assertIsNone(prepared.match_reason)
        self.assertIsNone(prepared.match_rules_version)
        self.assertIsNone(prepared.match_reason_codes)


if __name__ == "__main__":
    unittest.main()
