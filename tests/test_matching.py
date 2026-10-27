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


class CaseTableTestCase(unittest.TestCase):
    def assert_cases(self, function, cases, assertion=None):
        assertion = assertion or self.assertEqual

        for value, expected in cases:
            with self.subTest(value=value):
                assertion(function(value), expected)


class ExtractSalaryTests(CaseTableTestCase):
    def assert_salaries(self, cases):
        self.assert_cases(extract_salary, cases)

    def assert_no_salary(self, descriptions):
        self.assert_salaries(
            [(description, None) for description in descriptions]
        )

    def test_auctane_salary_with_currency_after_amounts(self):
        self.assert_salaries([
            ("Salary range: 43000 € to 52000€/year", "43000 € to 52000€/year"),
        ])

    def test_cabify_salary_with_k_and_trailing_currency(self):
        self.assert_salaries([
            ("Excellent Salary conditions: 38K - 50K€", "38K - 50K€"),
        ])

    def test_extract_salary_formats(self):
        self.assert_salaries([
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
        ])

    def test_ignores_numeric_ranges_without_currency(self):
        self.assert_no_salary([
            "Candidates need 3-5 years of experience.",
            "The service processed 43000 to 52000 requests.",
            "Versions 100000 - 120000 are not supported.",
            "",
            None,
        ])

    def test_ignores_perks_and_business_figures(self):
        # Textos reales de ClickHouse, Seedtag, Fever y Databricks.
        self.assert_no_salary([
            "Time off. - A USD$500 Home office setup if you're remote.",
            "Additional Perks Home office setup budget up to €1,000 Paid trips",
            "Swile Meal Card: R$900 monthly for food and dining.",
            "both capped at a $6,800 monthly wage ceiling.",
            "selling to Emerging Enterprise ($250 - $1B revenue) accounts",
            "We offer a $2,000 yearly learning stipend.",
            "Perks include a $300 gift.",
        ])

    def test_keeps_salary_next_to_perks(self):
        self.assert_salaries([
            (
                "Base Salary: $70,000 – $75,000 USD annually. "
                "Home office setup budget up to $1,000.",
                "$70,000 – $75,000 USD annually",
            ),
        ])

    def test_salary_label_wins_over_nearby_perk_words(self):
        self.assert_salaries([
            (
                "Salary: £42,180 + Car Allowance £6,000 (OTE £70,360)",
                "£42,180",
            ),
            (
                "Commission + 5-Year Recurring Revenue | "
                "OTE £7,000-£10,000+/month",
                "£7,000-£10,000",
            ),
        ])

    def test_keeps_small_amounts_with_pay_interval(self):
        self.assert_salaries([
            ("Pay: £16 per hour", "£16 per hour"),
            ("PAY RATE $500/day ABOUT THE ROLE", "$500/day"),
            ("Hourly Rate $54 — $60 USD", "$54 — $60 USD"),
        ])

    def test_keeps_country_prefix_of_dollar_currency(self):
        self.assert_salaries([
            ("Salary: R$8.000 monthly", "R$8.000 monthly"),
            ("Salary: CA$120,000 - CA$140,000", "CA$120,000 - CA$140,000"),
        ])


class ExtractExperienceTests(CaseTableTestCase):
    def test_extract_experience(self):
        self.assert_cases(extract_experience, [
            (COMPANY_AND_CANDIDATE_EXPERIENCE, "2+ years of experience"),
            (
                "Minimum 2 years of relevant experience required",
                "Minimum 2 years of relevant experience",
            ),
            ("3-5 years of experience with Spark", "3-5 years of experience"),
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
            ("Serving banks for over 12 years of experience.", None),
            ("Nuestra empresa acumula 10 años de experiencia.", None),
            ("A team with 25 years of experience in payments.", None),
            ("Build data pipelines with Python", None),
            ("", None),
            (None, None),
        ])


class RequiredExperienceYearsTests(CaseTableTestCase):
    def test_required_experience_years(self):
        self.assert_cases(required_experience_years, [
            ("5 years' experience", 5),
            ("5 years’ experience", 5),
            ("2 years of experience", 2),
        ])


class MatchesTargetLocationTests(CaseTableTestCase):
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
            ("Vitória, ES, Brazil", False),
            ("Remote - Atlanta, GA", False),
            ("", False),
            (None, False),
        ]

        self.assert_cases(matches_target_location, cases, self.assertIs)


class ClassifyEndToEndTests(unittest.TestCase):
    def test_company_years_do_not_hide_candidate_requirement(self):
        result = classify(
            make_job("Madrid, Spain", COMPANY_AND_CANDIDATE_EXPERIENCE)
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
                self.assertIsNone(classify(make_job(location, description)))


class MatchMetadataTests(unittest.TestCase):
    def assert_match(self, job, status, reason_codes):
        result = classify(job)

        self.assertIsNotNone(result)
        self.assertIsInstance(result, MatchResult)
        self.assertEqual(result.status, status)
        self.assertEqual(MATCH_RULES_VERSION, 2)
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
            ("Junior Data Engineer", MatchReasonCode.JUNIOR_LEVEL),
            ("Data Engineer I", MatchReasonCode.LEVEL_I_II),
            ("Data Engineer II", MatchReasonCode.LEVEL_I_II),
        ]

        for title, level_code in cases:
            with self.subTest(title=title):
                self.assert_match(
                    make_job("Madrid, Spain", "", title=title),
                    "Buena coincidencia",
                    (MatchReasonCode.CORE_DATA_ROLE, level_code),
                )

    def test_mid_and_level_iii_use_specific_codes(self):
        cases = [
            ("Mid Data Engineer", MatchReasonCode.MID_LEVEL_STRETCH),
            ("Data Engineer III", MatchReasonCode.LEVEL_III_STRETCH),
        ]

        for title, level_code in cases:
            with self.subTest(title=title):
                self.assert_match(
                    make_job("Madrid, Spain", "", title=title),
                    "Stretch",
                    (MatchReasonCode.CORE_DATA_ROLE, level_code),
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
