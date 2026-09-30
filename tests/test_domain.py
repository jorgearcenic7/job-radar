import unittest

from job_radar.domain import Job


class JobTests(unittest.TestCase):
    def make_mapping(self):
        return {
            "source": "test:source",
            "source_job_id": "job-123",
            "company": "Example",
            "title": "Data Engineer",
            "location": None,
            "url": "https://example.com/jobs/job-123",
            "description": "Python and SQL",
            "salary_text": None,
            "experience_text": "2 years of experience",
        }

    def test_from_mapping_preserves_every_pipeline_field(self):
        values = self.make_mapping()

        job = Job.from_mapping(values)

        for field, value in values.items():
            self.assertEqual(getattr(job, field), value)

    def test_from_mapping_rejects_an_incomplete_connector_result(self):
        values = self.make_mapping()
        del values["salary_text"]

        with self.assertRaises(KeyError):
            Job.from_mapping(values)


if __name__ == "__main__":
    unittest.main()
