import unittest

from job_radar.domain import Job
from job_radar.matching import (
    classify,
    extract_experience,
    matches_target_location,
    required_experience_years,
)


COMPANY_AND_CANDIDATE_EXPERIENCE = (
    "We are a payments leader with over 20 years of experience. "
    "You have 2+ years of experience building data pipelines "
    "with Python and SQL."
)


def make_job(location, description):
    return Job(
        source="test:matching",
        source_job_id="job-1",
        company="Example",
        title="Data Engineer",
        location=location,
        url="https://example.com/jobs/job-1",
        description=description,
        salary_text=None,
        experience_text=None,
    )


class ExtractExperienceTests(unittest.TestCase):
    def test_extract_experience(self):
        cases = [
            (
                COMPANY_AND_CANDIDATE_EXPERIENCE,
                "2+ years of experience",
            ),
            (
                "Minimum 2 years of relevant experience required",
                "Minimum 2 years of relevant experience",
            ),
            (
                "3-5 years of experience with Spark",
                "3-5 years of experience",
            ),
            (
                "Mínimo 3 años de experiencia en SQL",
                "Mínimo 3 años de experiencia",
            ),
            (
                "Más de 5 años de experiencia en Python",
                "5 años de experiencia",
            ),
            (
                "More than 4 years of experience with Airflow",
                "4 years of experience",
            ),
            (
                "Our company has 10 years of experience in fintech. "
                "You bring 1+ years of experience with dbt.",
                "1+ years of experience",
            ),
            (
                "Serving banks for over 12 years of experience.",
                None,
            ),
            (
                "Nuestra empresa acumula 10 años de experiencia.",
                None,
            ),
            (
                "A team with 25 years of experience in payments.",
                None,
            ),
            ("Build data pipelines with Python", None),
            ("", None),
            (None, None),
        ]

        for description, expected in cases:
            with self.subTest(description=description):
                self.assertEqual(
                    extract_experience(description),
                    expected,
                )


class RequiredExperienceYearsTests(unittest.TestCase):
    def test_required_experience_years(self):
        cases = [
            ("5 years' experience", 5),
            ("5 years’ experience", 5),
            ("2 years of experience", 2),
        ]

        for experience_text, expected in cases:
            with self.subTest(experience_text=experience_text):
                self.assertEqual(
                    required_experience_years(experience_text),
                    expected,
                )


class MatchesTargetLocationTests(unittest.TestCase):
    def test_matches_target_location(self):
        cases = [
            ("Madrid, Spain", True),
            ("Barcelona", True),
            ("Remote", True),
            ("Remote - EMEA", True),
            ("Remote, Europe", True),
            ("Remote - Spain", True),
            ("Remoto - España", True),
            ("Remote (ES)", True),
            ("Madrid, Spain; Remote - US", True),
            ("Remote - US", False),
            ("Remote - USA", False),
            ("Remote (United States)", False),
            ("Remote, Canada", False),
            ("Remote - LATAM", False),
            ("Remote - Americas", False),
            ("Remote - North America", False),
            ("Remote - APAC", False),
            ("Remote - India", False),
            ("Remote - Germany", False),
            ("London, UK", False),
            ("", False),
            (None, False),
        ]

        for location, expected in cases:
            with self.subTest(location=location):
                self.assertIs(
                    matches_target_location(location),
                    expected,
                )


class ClassifyEndToEndTests(unittest.TestCase):
    def test_company_years_do_not_hide_candidate_requirement(self):
        result = classify(
            make_job(
                "Madrid, Spain",
                COMPANY_AND_CANDIDATE_EXPERIENCE,
            )
        )

        self.assertIsNotNone(result)
        self.assertEqual(result[0], "Buena coincidencia")
        self.assertIn("2+ years of experience", result[2])

    def test_remote_restricted_to_another_country_is_rejected(self):
        description = (
            "You have 2+ years of experience building data pipelines "
            "with Python and SQL."
        )

        # Control: la misma oferta sin restricción de país encaja.
        self.assertEqual(
            classify(make_job("Remote", description))[0],
            "Buena coincidencia",
        )

        for location in [
            "Remote - US",
            "Remote (United States)",
            "Remote, Canada",
        ]:
            with self.subTest(location=location):
                self.assertIsNone(
                    classify(make_job(location, description))
                )


if __name__ == "__main__":
    unittest.main()
