import unittest

from job_radar.domain import Job
from job_radar.matching import (
    MATCH_RULES_VERSION,
    MatchReasonCode,
    MatchResult,
    classify,
    extract_experience,
    extract_salary,
    matches_target_location,
    required_experience_years,
)


COMPANY_AND_CANDIDATE_EXPERIENCE = (
    "We are a payments leader with over 20 years of experience. "
    "You have 2+ years of experience building data pipelines "
    "with Python and SQL."
)


def make_job(
    location,
    description,
    *,
    title="Data Engineer",
    experience_text=None,
):
    return Job(
        source="test:matching",
        source_job_id="job-1",
        company="Example",
        title=title,
        location=location,
        url="https://example.com/jobs/job-1",
        description=description,
        salary_text=None,
        experience_text=experience_text,
    )


class ExtractSalaryTests(unittest.TestCase):
    def test_auctane_salary_with_currency_after_amounts(self):
        description = "Salary range: 43000 € to 52000€/year"

        self.assertEqual(
            extract_salary(description),
            "43000 € to 52000€/year",
        )

    def test_cabify_salary_with_k_and_trailing_currency(self):
        description = "Excellent Salary conditions: 38K - 50K€"

        self.assertEqual(extract_salary(description), "38K - 50K€")

    def test_extract_salary_formats(self):
        cases = [
            ("Salary: 43.000 € - 52.000 €", "43.000 € - 52.000 €"),
            ("Salary: 43,000€ - 52,000€", "43,000€ - 52,000€"),
            ("Salary: €43,000 - €52,000", "€43,000 - €52,000"),
            ("Salary: 43000 - 52000 EUR", "43000 - 52000 EUR"),
            ("Salary: 43k - 52k EUR", "43k - 52k EUR"),
            ("Salary: $100,000 - $120,000", "$100,000 - $120,000"),
            (
                "Salary: 100000 USD - 120000 USD per year",
                "100000 USD - 120000 USD per year",
            ),
            ("Salary: £50k–£60k", "£50k–£60k"),
            ("Salary: €50,000 — €60,000 annually", "€50,000 — €60,000 annually"),
            ("Compensation: EUR 45 000 to 55 000 EUR", "EUR 45 000 to 55 000 EUR"),
            ("Budget: $80k per annum", "$80k per annum"),
        ]

        for description, expected in cases:
            with self.subTest(description=description):
                self.assertEqual(extract_salary(description), expected)

    def test_ignores_numeric_ranges_without_currency(self):
        cases = [
            "Candidates need 3-5 years of experience.",
            "The service processed 43000 to 52000 requests.",
            "Versions 100000 - 120000 are not supported.",
            "",
            None,
        ]

        for description in cases:
            with self.subTest(description=description):
                self.assertIsNone(extract_salary(description))


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
        self.assertEqual(result.status, "Buena coincidencia")
        self.assertIn("2+ years of experience", result.reason)

    def test_remote_restricted_to_another_country_is_rejected(self):
        description = (
            "You have 2+ years of experience building data pipelines "
            "with Python and SQL."
        )

        # Control: la misma oferta sin restricción de país encaja.
        self.assertEqual(
            classify(make_job("Remote", description)).status,
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


class MatchMetadataTests(unittest.TestCase):
    def assert_match(self, job, status, reason_codes):
        result = classify(job)

        self.assertIsNotNone(result)
        self.assertIsInstance(result, MatchResult)
        self.assertEqual(result.status, status)
        self.assertEqual(MATCH_RULES_VERSION, 1)
        self.assertEqual(result.rules_version, MATCH_RULES_VERSION)
        self.assertEqual(result.reason_codes, reason_codes)

    def test_core_data_engineer_with_compatible_experience(self):
        self.assert_match(
            make_job(
                "Madrid, Spain",
                "Python, SQL and data pipelines",
                experience_text="2 years of experience",
            ),
            "Buena coincidencia",
            (
                MatchReasonCode.CORE_DATA_ROLE,
                MatchReasonCode.EXPERIENCE_COMPATIBLE,
                MatchReasonCode.STRONG_DATA_SIGNALS,
            ),
        )

    def test_technical_data_analyst_is_stretch(self):
        self.assert_match(
            make_job(
                "Madrid, Spain",
                "Python and SQL",
                title="Data Analyst",
            ),
            "Stretch",
            (MatchReasonCode.TECHNICAL_DATA_ANALYST,),
        )

    def test_technical_ml_role_is_stretch(self):
        self.assert_match(
            make_job(
                "Madrid, Spain",
                "Python and model deployment",
                title="Machine Learning Engineer",
            ),
            "Stretch",
            (MatchReasonCode.TECHNICAL_ML_ROLE,),
        )

    def test_data_heavy_adjacent_role_is_stretch(self):
        self.assert_match(
            make_job(
                "Madrid, Spain",
                "Build data pipelines, ETL systems and a data warehouse",
                title="Software Engineer",
            ),
            "Stretch",
            (
                MatchReasonCode.DATA_HEAVY_ADJACENT_ROLE,
                MatchReasonCode.STRONG_DATA_SIGNALS,
            ),
        )

    def test_core_role_with_three_years_is_stretch(self):
        self.assert_match(
            make_job(
                "Madrid, Spain",
                "Python, SQL and data pipelines",
                experience_text="3 years of experience",
            ),
            "Stretch",
            (
                MatchReasonCode.CORE_DATA_ROLE,
                MatchReasonCode.THREE_YEARS_STRETCH,
                MatchReasonCode.STRONG_DATA_SIGNALS,
            ),
        )

    def test_junior_and_level_i_ii_are_good_matches(self):
        cases = [
            (
                "Junior Data Engineer",
                MatchReasonCode.JUNIOR_LEVEL,
            ),
            ("Data Engineer I", MatchReasonCode.LEVEL_I_II),
            ("Data Engineer II", MatchReasonCode.LEVEL_I_II),
        ]

        for title, level_code in cases:
            with self.subTest(title=title):
                self.assert_match(
                    make_job(
                        "Madrid, Spain",
                        "",
                        title=title,
                    ),
                    "Buena coincidencia",
                    (
                        MatchReasonCode.CORE_DATA_ROLE,
                        level_code,
                    ),
                )

    def test_mid_and_level_iii_use_specific_codes(self):
        cases = [
            ("Mid Data Engineer", MatchReasonCode.MID_LEVEL_STRETCH),
            ("Data Engineer III", MatchReasonCode.LEVEL_III_STRETCH),
        ]

        for title, level_code in cases:
            with self.subTest(title=title):
                self.assert_match(
                    make_job(
                        "Madrid, Spain",
                        "",
                        title=title,
                    ),
                    "Stretch",
                    (
                        MatchReasonCode.CORE_DATA_ROLE,
                        level_code,
                    ),
                )

    def test_unspecified_core_level_is_stretch(self):
        self.assert_match(
            make_job("Madrid, Spain", ""),
            "Stretch",
            (
                MatchReasonCode.CORE_DATA_ROLE,
                MatchReasonCode.LEVEL_UNSPECIFIED,
            ),
        )

    def test_excluded_jobs_still_return_none(self):
        cases = [
            make_job(
                "Madrid, Spain",
                "Python, SQL and data pipelines",
                title="Senior Data Engineer",
            ),
            make_job(
                "Madrid, Spain",
                "Python, SQL and data pipelines",
                experience_text="4 years of experience",
            ),
            make_job(
                "Madrid, Spain",
                "Business reporting",
                title="Data Analyst",
            ),
        ]

        for job in cases:
            with self.subTest(title=job.title):
                self.assertIsNone(classify(job))


if __name__ == "__main__":
    unittest.main()
