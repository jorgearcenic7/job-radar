import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import main


class JsonResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class ExtractionTests(unittest.TestCase):
    def test_plain_text(self):
        result = main.plain_text(
            "<p>Build &amp; maintain</p><p>data pipelines</p>"
        )
        self.assertEqual(result, "Build & maintain data pipelines")

    def test_salary_extraction(self):
        description = "Salary: €50,000 - €60,000 per year"
        self.assertEqual(
            main.extract_salary(description),
            "€50,000 - €60,000",
        )

    def test_experience_extraction(self):
        description = "Minimum 2 years of relevant experience required"
        self.assertEqual(
            main.extract_experience(description),
            "Minimum 2 years of relevant experience",
        )


class MatchingTests(unittest.TestCase):
    def make_job(self, title, description="", experience_text=None):
        return {
            "title": title,
            "description": description,
            "experience_text": experience_text,
        }

    def test_junior_data_engineer_is_good_match(self):
        result = main.classify(
            self.make_job(
                "Junior Data Engineer",
                "Python, SQL and data pipelines",
                "2 years of experience",
            )
        )
        self.assertEqual(result[0], "Buena coincidencia")

    def test_unspecified_data_engineer_is_stretch(self):
        result = main.classify(
            self.make_job("Data Engineer")
        )
        self.assertEqual(result[0], "Stretch")

    def test_data_focused_software_engineer_is_stretch(self):
        result = main.classify(
            self.make_job(
                "Software Engineer",
                "Build data pipelines, ETL systems and a data warehouse",
            )
        )
        self.assertEqual(result[0], "Stretch")

    def test_senior_role_is_rejected(self):
        result = main.classify(
            self.make_job("Senior Data Engineer")
        )
        self.assertIsNone(result)

    def test_four_year_requirement_is_rejected(self):
        result = main.classify(
            self.make_job(
                "Data Engineer",
                experience_text="4 years of experience",
            )
        )
        self.assertIsNone(result)


class GreenhouseTests(unittest.TestCase):
    @patch("main.fetch_json")
    def test_greenhouse_mapping(self, fetch_json):
        fetch_json.return_value = {
            "jobs": [
                {
                    "id": 123,
                    "title": "Data Engineer II",
                    "location": {"name": "Madrid, Spain"},
                    "absolute_url": "https://example.com/jobs/123",
                    "content": (
                        "<p>Build pipelines.</p>"
                        "<p>2 years of experience.</p>"
                    ),
                }
            ]
        }

        jobs = main.fetch_greenhouse("Example", "example")

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["source"], "greenhouse")
        self.assertEqual(jobs[0]["source_job_id"], "123")
        self.assertEqual(jobs[0]["company"], "Example")
        self.assertEqual(jobs[0]["location"], "Madrid, Spain")
        self.assertEqual(
            jobs[0]["experience_text"],
            "2 years of experience",
        )


class AshbyTests(unittest.TestCase):
    @patch("main.fetch_json")
    def test_ashby_mapping_and_filtering(self, fetch_json):
        fetch_json.return_value = {
            "jobs": [
                {
                    "title": "Analytics Engineer",
                    "location": "Madrid",
                    "secondaryLocations": [
                        {"location": "Remote - Spain"},
                        {"location": "Madrid"},
                    ],
                    "descriptionPlain": "Build dbt models",
                    "compensation": {
                        "scrapeableCompensationSalarySummary":
                            "€45k - €55k"
                    },
                    "jobUrl": "https://jobs.example.com/abc",
                    "isListed": True,
                },
                {
                    "title": "Hidden role",
                    "jobUrl": "https://jobs.example.com/hidden",
                    "isListed": False,
                },
            ]
        }

        jobs = main.fetch_ashby("Example", "example")

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["source"], "ashby")
        self.assertEqual(jobs[0]["source_job_id"], "abc")
        self.assertEqual(
            jobs[0]["location"],
            "Madrid; Remote - Spain",
        )
        self.assertEqual(jobs[0]["salary_text"], "€45k - €55k")


class WorkdayTests(unittest.TestCase):
    def test_relevant_title_filter(self):
        self.assertTrue(
            main.workday_relevant_title("Data Platform Engineer")
        )
        self.assertFalse(
            main.workday_relevant_title("Commercial Account Manager")
        )

    @patch("main.urlopen")
    def test_workday_listing_and_enrichment(self, urlopen):
        urlopen.side_effect = [
            JsonResponse({
                "jobPostings": [
                    {
                        "title": "Data Engineer II",
                        "locationsText": "Madrid, Spain",
                        "externalPath": "/job/Madrid/Data-Engineer_R123",
                    },
                    {
                        "title": "Sales Executive",
                        "locationsText": "Barcelona, Spain",
                        "externalPath": "/job/Barcelona/Sales_R456",
                    },
                ]
            }),
            JsonResponse({
                "jobPostingInfo": {
                    "location": "Madrid, Spain",
                    "externalUrl": "https://example.com/R123",
                    "jobDescription": (
                        "<p>Build data pipelines with Python.</p>"
                        "<p>2 years of experience.</p>"
                    ),
                }
            }),
        ]

        with redirect_stdout(io.StringIO()):
            jobs = main.fetch_workday(
                "Example Bank",
                "example.wd3.myworkdayjobs.com",
                "example",
                "Careers",
            )

        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["source"], "workday:example")
        self.assertEqual(jobs[0]["source_job_id"], "Data-Engineer_R123")
        self.assertEqual(jobs[0]["url"], "https://example.com/R123")
        self.assertEqual(
            jobs[0]["experience_text"],
            "2 years of experience",
        )
        self.assertEqual(jobs[1]["description"], "")
        self.assertEqual(urlopen.call_count, 2)


if __name__ == "__main__":
    unittest.main()
