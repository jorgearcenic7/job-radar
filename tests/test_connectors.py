import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from job_radar import matching, notifications
from job_radar.connectors import ats, common, custom
from job_radar.connectors import registry
from job_radar.domain import Job


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
        result = common.plain_text(
            "<p>Build &amp; maintain</p><p>data pipelines</p>"
        )
        self.assertEqual(result, "Build & maintain data pipelines")

    def test_salary_extraction(self):
        description = "Salary: €50,000 - €60,000 per year"
        self.assertEqual(
            matching.extract_salary(description),
            "€50,000 - €60,000 per year",
        )

    def test_experience_extraction(self):
        description = "Minimum 2 years of relevant experience required"
        self.assertEqual(
            matching.extract_experience(description),
            "Minimum 2 years of relevant experience",
        )


class MatchingTests(unittest.TestCase):
    def make_job(
        self,
        title,
        description="",
        experience_text=None,
        location="Madrid, Spain",
    ):
        return Job(
            source="test:matching",
            source_job_id="job-1",
            company="Example",
            title=title,
            description=description,
            experience_text=experience_text,
            location=location,
            url="https://example.com/jobs/job-1",
            salary_text=None,
        )

    @patch("job_radar.matching.rules.infer_countries", return_value=["Spain"])
    def test_spanish_city_is_accepted(self, infer_countries):
        result = matching.classify(
            self.make_job(
                "Junior Data Engineer",
                location="Barcelona",
            )
        )

        self.assertEqual(result[0], "Buena coincidencia")
        infer_countries.assert_called_once_with("Barcelona")

    def test_remote_europe_location_is_accepted(self):
        result = matching.classify(
            self.make_job(
                "Junior Data Engineer",
                location="Remote - Europe",
            )
        )

        self.assertEqual(result[0], "Buena coincidencia")

    def test_spanish_remote_word_is_accepted(self):
        result = matching.classify(
            self.make_job(
                "Junior Data Engineer",
                location="Remoto",
            )
        )

        self.assertEqual(result[0], "Buena coincidencia")

    @patch("job_radar.matching.rules.infer_countries", return_value=["United Kingdom"])
    def test_foreign_non_remote_location_is_rejected(
        self,
        infer_countries,
    ):
        result = matching.classify(
            self.make_job(
                "Junior Data Engineer",
                location="London, United Kingdom",
            )
        )

        self.assertIsNone(result)
        infer_countries.assert_called_once_with(
            "London, United Kingdom"
        )

    def test_missing_location_is_rejected(self):
        result = matching.classify(
            self.make_job(
                "Junior Data Engineer",
                location=None,
            )
        )

        self.assertIsNone(result)

    def test_junior_data_engineer_is_good_match(self):
        result = matching.classify(
            self.make_job(
                "Junior Data Engineer",
                "Python, SQL and data pipelines",
                "2 years of experience",
            )
        )
        self.assertEqual(result[0], "Buena coincidencia")

    def test_unspecified_data_engineer_is_stretch(self):
        result = matching.classify(
            self.make_job("Data Engineer")
        )
        self.assertEqual(result[0], "Stretch")

    def test_data_focused_software_engineer_is_stretch(self):
        result = matching.classify(
            self.make_job(
                "Software Engineer",
                "Build data pipelines, ETL systems and a data warehouse",
            )
        )
        self.assertEqual(result[0], "Stretch")

    def test_senior_role_is_rejected(self):
        result = matching.classify(
            self.make_job("Senior Data Engineer")
        )
        self.assertIsNone(result)

    def test_four_year_requirement_is_rejected(self):
        result = matching.classify(
            self.make_job(
                "Data Engineer",
                experience_text="4 years of experience",
            )
        )
        self.assertIsNone(result)


class EmailNotificationTests(unittest.TestCase):
    def make_match(self):
        return {
            "company": "Example & Co",
            "title": "Data Engineer <Junior>",
            "location": "Madrid, Spain",
            "url": "https://example.com/jobs/123?from=radar&level=1",
            "salary_text": "€40k - €50k",
            "experience_text": "2 years of experience",
            "match_status": "Buena coincidencia",
        }

    def test_email_content_escapes_data_and_includes_plain_text(self):
        subject, html, text = notifications.build_match_email([self.make_match()])

        self.assertIn("1 coincidencia", subject)
        self.assertIn("Example &amp; Co", html)
        self.assertIn("Data Engineer &lt;Junior&gt;", html)
        self.assertNotIn("Data Engineer <Junior>", html)
        self.assertIn("Example & Co", text)
        self.assertIn("https://example.com/jobs/123", text)

    def test_email_rejects_non_https_job_links(self):
        match = self.make_match()
        match["url"] = "javascript:alert(1)"

        _subject, html, text = notifications.build_match_email([match])

        self.assertNotIn("javascript:", html)
        self.assertNotIn("javascript:", text)
        self.assertIn("Enlace no disponible", html)

    @patch("job_radar.notifications.email.get_active_matches")
    @patch("job_radar.notifications.email.urlopen")
    def test_send_notification_uses_resend_and_idempotency(
        self,
        urlopen,
        get_active_matches,
    ):
        get_active_matches.return_value = [self.make_match()]
        urlopen.return_value = JsonResponse({"id": "email-123"})
        environment = {
            "RESEND_API_KEY": "re_test",
            "NOTIFICATION_EMAIL": "recipient@example.com",
            "NOTIFICATION_FROM": "Job Radar <jobs@example.com>",
            "NOTIFICATION_RUN_ID": "run-123",
        }

        with patch.dict(os.environ, environment, clear=False):
            with redirect_stdout(io.StringIO()):
                result = notifications.send_match_notification()

        self.assertEqual(result, "email-123")
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        headers = {
            name.casefold(): value
            for name, value in request.header_items()
        }

        self.assertEqual(request.full_url, notifications.RESEND_EMAILS_URL)
        self.assertEqual(payload["to"], ["recipient@example.com"])
        self.assertEqual(
            payload["from"],
            "Job Radar <jobs@example.com>",
        )
        self.assertEqual(headers["authorization"], "Bearer re_test")
        self.assertEqual(headers["idempotency-key"], "job-radar/run-123")

    @patch("job_radar.notifications.email.get_active_matches")
    def test_notification_is_disabled_without_api_key(
        self,
        get_active_matches,
    ):
        with patch.dict(os.environ, {"RESEND_API_KEY": ""}, clear=False):
            with redirect_stdout(io.StringIO()):
                result = notifications.send_match_notification()

        self.assertIsNone(result)
        get_active_matches.assert_not_called()

class GreenhouseTests(unittest.TestCase):
    @patch("job_radar.connectors.ats.fetch_json")
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

        jobs = ats.fetch_greenhouse("Example", "example")

        self.assertEqual(len(jobs), 1)
        self.assertIsInstance(jobs[0], Job)
        self.assertEqual(jobs[0].source, "greenhouse")
        self.assertEqual(jobs[0].source_job_id, "123")
        self.assertEqual(jobs[0].company, "Example")
        self.assertEqual(jobs[0].location, "Madrid, Spain")
        self.assertEqual(
            jobs[0].experience_text,
            "2 years of experience",
        )


class AshbyTests(unittest.TestCase):
    @patch("job_radar.connectors.ats.fetch_json")
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

        jobs = ats.fetch_ashby("Example", "example")

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source, "ashby")
        self.assertEqual(jobs[0].source_job_id, "abc")
        self.assertEqual(
            jobs[0].location,
            "Madrid; Remote - Spain",
        )
        self.assertEqual(jobs[0].salary_text, "€45k - €55k")


class LeverTests(unittest.TestCase):
    @patch("job_radar.connectors.ats.fetch_json")
    def test_lever_mapping(self, fetch_json):
        fetch_json.return_value = [{
            "id": "paytm-123",
            "text": "Data Engineer",
            "hostedUrl": "https://jobs.lever.co/paytm/paytm-123",
            "descriptionPlain": "Build data pipelines.",
            "additionalPlain": "2 years of experience.",
            "lists": [{"content": "<p>Python and SQL</p>"}],
            "categories": {
                "location": "Noida",
                "allLocations": ["Noida", "Bengaluru"],
            },
            "salaryRange": {
                "min": 100000,
                "max": 150000,
                "currency": "INR",
                "interval": "per-year-salary",
            },
        }]

        jobs = ats.fetch_lever("Paytm", "paytm")

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source, "lever:paytm")
        self.assertEqual(jobs[0].location, "Noida; Bengaluru")
        self.assertEqual(
            jobs[0].salary_text,
            "100000 – 150000 INR / per year salary",
        )
        self.assertEqual(
            jobs[0].experience_text,
            "2 years of experience",
        )


class BambooHRTests(unittest.TestCase):
    @patch("job_radar.connectors.ats.fetch_json")
    def test_bamboohr_mapping(self, fetch_json):
        fetch_json.return_value = {
            "result": [{
                "id": "1383",
                "jobOpeningName": "Analytics Engineer",
                "departmentLabel": "Data",
                "employmentStatusLabel": "Full-Time",
                "location": {
                    "city": "Lekki",
                    "state": "Lagos",
                    "country": "Nigeria",
                },
            }]
        }

        jobs = ats.fetch_bamboohr("Flutterwave", "flutterwavego")

        self.assertEqual(len(jobs), 1)
        self.assertEqual(
            jobs[0].source,
            "bamboohr:flutterwavego",
        )
        self.assertEqual(jobs[0].location, "Lekki, Lagos, Nigeria")
        self.assertEqual(
            jobs[0].url,
            "https://flutterwavego.bamboohr.com/careers/1383",
        )


class EightfoldTests(unittest.TestCase):
    @patch("job_radar.connectors.ats.fetch_json")
    def test_listing_and_relevant_job_enrichment(self, fetch_json):
        fetch_json.side_effect = [
            {
                "data": {
                    "count": 2,
                    "positions": [
                        {
                            "id": 123,
                            "name": "Data Engineer",
                            "locations": ["Madrid, Spain"],
                            "positionUrl": "/careers/job/123",
                        },
                        {
                            "id": 456,
                            "name": "Account Executive",
                            "locations": ["London, UK"],
                            "positionUrl": "/careers/job/456",
                        },
                    ],
                }
            },
            {
                "data": {
                    "locations": ["Madrid, Spain"],
                    "jobDescription": (
                        "<p>Build data pipelines.</p>"
                        "<p>2 years of experience.</p>"
                    ),
                }
            },
        ]

        with redirect_stdout(io.StringIO()):
            jobs = ats.fetch_eightfold(
                "PayPal",
                "paypal.eightfold.ai",
                "paypal.com",
            )

        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].source, "eightfold:paypal.com")
        self.assertEqual(
            jobs[0].experience_text,
            "2 years of experience",
        )
        self.assertEqual(jobs[1].description, "")


class DeelBoardTests(unittest.TestCase):
    @patch("job_radar.connectors.custom.fetch_text")
    def test_company_board_mapping(self, fetch_text):
        job_id = "161d3133-1185-405b-a037-0f5aa14ee60b"
        fetch_text.side_effect = [
            f'<a href="/klarna/job-details/{job_id}/overview">Role</a>',
            """
                <script type="application/ld+json">
                {
                  "@type": "JobPosting",
                  "title": "Data Engineer",
                  "description": "Build pipelines with Python.",
                  "jobLocation": {
                    "address": {
                      "addressLocality": "Stockholm",
                      "addressCountry": "Sweden"
                    }
                  }
                }
                </script>
            """,
        ]

        jobs = custom.fetch_deel_company("Klarna", "klarna")

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source, "deel:klarna")
        self.assertEqual(jobs[0].company, "Klarna")
        self.assertEqual(jobs[0].location, "Stockholm, Sweden")


class AntGroupTests(unittest.TestCase):
    @patch("job_radar.connectors.common.urlopen")
    def test_ant_group_mapping(self, urlopen):
        urlopen.return_value = JsonResponse({
            "success": True,
            "content": [{
                "id": 123,
                "name": "Applied AI Engineer",
                "workLocations": ["Kuala Lumpur"],
                "description": "Build production data pipelines.",
                "requirement": "At least 3 years of experience.",
                "experience": {"from": 3, "to": None},
            }],
            "totalCount": 1,
            "pageSize": 10,
            "currentPage": 1,
        })

        jobs = custom.fetch_ant_group()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source, "ant:careers")
        self.assertEqual(
            jobs[0].company,
            "Ant Group / Ant International",
        )
        self.assertEqual(jobs[0].location, "Kuala Lumpur")
        self.assertEqual(
            jobs[0].experience_text,
            "At least 3 years of experience",
        )
        request = urlopen.call_args.args[0]
        self.assertEqual(json.loads(request.data)["bgCode"], "M7892")


class WorkdayTests(unittest.TestCase):
    def test_relevant_title_filter(self):
        self.assertTrue(
            ats.workday_relevant_title("Data Platform Engineer")
        )
        self.assertFalse(
            ats.workday_relevant_title("Commercial Account Manager")
        )

    @patch("job_radar.connectors.common.urlopen")
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
            jobs = ats.fetch_workday(
                "Example Bank",
                "example.wd3.myworkdayjobs.com",
                "example",
                "Careers",
            )

        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].source, "workday:example")
        self.assertEqual(jobs[0].source_job_id, "Data-Engineer_R123")
        self.assertEqual(jobs[0].url, "https://example.com/R123")
        self.assertEqual(
            jobs[0].experience_text,
            "2 years of experience",
        )
        self.assertEqual(jobs[1].description, "")
        self.assertEqual(urlopen.call_count, 2)


class NewCompanyConfigurationTests(unittest.TestCase):
    def test_product_software_and_fintech_sources_are_configured(self):
        greenhouse_companies = {
            "Make": "make",
            "Awin": "awin",
            "Blip Global": "blip-global",
            "OneTrust": "onetrust",
            "nCino": "ncinoinc",
            "Affirm": "affirm",
            "Raisin": "raisin",
        }
        ashby_companies = {
            "n8n": "n8n",
            "ClickHouse": "clickhouse",
            "Ashby": "ashby",
            "StackAI": "stack-ai",
            "Camunda": "camunda",
            "Supabase": "supabase",
        }

        for company, slug in greenhouse_companies.items():
            with self.subTest(company=company):
                self.assertEqual(
                    registry.GREENHOUSE_COMPANIES[company],
                    slug,
                )

        for company, slug in ashby_companies.items():
            with self.subTest(company=company):
                self.assertEqual(registry.ASHBY_COMPANIES[company], slug)

        self.assertEqual(
            len(registry.CONNECTORS),
            57 + len(greenhouse_companies) + len(ashby_companies),
        )

    def test_requested_companies_are_configured(self):
        self.assertIn("Celonis", registry.GREENHOUSE_COMPANIES)
        self.assertIn("Lovable", registry.ASHBY_COMPANIES)
        self.assertIn("Amadeus", registry.WORKDAY_COMPANIES)
        self.assertIn("AVEVA", registry.WORKDAY_COMPANIES)
        self.assertIn("IFS", registry.SMARTRECRUITERS_COMPANIES)
        self.assertIn("SAP", registry.SUCCESSFACTORS_COMPANIES)
        self.assertIn("Hexagon", registry.SUCCESSFACTORS_COMPANIES)
        self.assertEqual(registry.GREENHOUSE_COMPANIES["Stripe"], "stripe")
        self.assertEqual(registry.GREENHOUSE_COMPANIES["Adyen"], "adyen")
        self.assertEqual(
            registry.GREENHOUSE_COMPANIES["Block (incl. Afterpay)"],
            "block",
        )
        self.assertIn("Chime", registry.GREENHOUSE_COMPANIES)
        self.assertIn("Nubank", registry.GREENHOUSE_COMPANIES)
        self.assertIn("Robinhood", registry.GREENHOUSE_COMPANIES)
        self.assertIn("SoFi", registry.GREENHOUSE_COMPANIES)
        self.assertIn("Coinbase", registry.GREENHOUSE_COMPANIES)
        self.assertIn("Plaid", registry.ASHBY_COMPANIES)
        self.assertIn("Qonto", registry.ASHBY_COMPANIES)
        self.assertIn("Mollie", registry.ASHBY_COMPANIES)
        self.assertIn("Wise", registry.SMARTRECRUITERS_COMPANIES)
        self.assertIn(
            "Grab / Grab Financial Group",
            registry.SMARTRECRUITERS_COMPANIES,
        )
        self.assertIn("Paytm", registry.LEVER_COMPANIES)
        self.assertIn("Klarna", registry.DEEL_COMPANIES)
        self.assertIn("Flutterwave", registry.BAMBOOHR_COMPANIES)
        self.assertIn("PayPal", registry.EIGHTFOLD_COMPANIES)


class SmartRecruitersTests(unittest.TestCase):
    @patch("job_radar.connectors.ats.fetch_json")
    def test_listing_and_relevant_job_enrichment(self, fetch_json):
        fetch_json.side_effect = [
            {
                "totalFound": 2,
                "content": [
                    {
                        "id": "123",
                        "name": "Data Engineer",
                        "location": {"fullLocation": "Madrid, Spain"},
                    },
                    {
                        "id": "456",
                        "name": "Account Executive",
                        "location": {"fullLocation": "London, UK"},
                    },
                ],
            },
            {
                "postingUrl": "https://jobs.example.com/123",
                "jobAd": {
                    "sections": {
                        "jobDescription": {
                            "text": "<p>Build data pipelines.</p>"
                        },
                        "qualifications": {
                            "text": "<p>2 years of experience.</p>"
                        },
                    }
                },
            },
        ]

        with redirect_stdout(io.StringIO()):
            jobs = ats.fetch_smartrecruiters("IFS", "IFS1")

        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].source, "smartrecruiters:ifs1")
        self.assertEqual(jobs[0].url, "https://jobs.example.com/123")
        self.assertEqual(
            jobs[0].experience_text,
            "2 years of experience",
        )
        self.assertEqual(jobs[1].description, "")


class SuccessFactorsTests(unittest.TestCase):
    @patch("job_radar.connectors.ats.fetch_text")
    def test_successfactors_mapping(self, fetch_text):
        fetch_text.return_value = """
            <Job-Listing>
              <Job>
                <JobTitle><![CDATA[Data Engineer II]]></JobTitle>
                <Job-Description><![CDATA[
                  <p>Build pipelines.</p><p>2 years of experience.</p>
                ]]></Job-Description>
                <ReqId>123</ReqId>
                <filter7><label>Country</label><value>Spain</value></filter7>
                <filter8>
                  <label>Internal Posting Location</label>
                  <value>Madrid</value>
                </filter8>
              </Job>
            </Job-Listing>
        """

        jobs = ats.fetch_successfactors(
            "SAP",
            "career.example.com",
            "SAP",
            "https://jobs.example.com/search?q={job_id}&slug={slug}",
        )

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source, "successfactors:sap")
        self.assertEqual(jobs[0].source_job_id, "123")
        self.assertEqual(jobs[0].location, "Madrid, Spain")
        self.assertIn("slug=Data-Engineer-II", jobs[0].url)
        self.assertEqual(
            jobs[0].experience_text,
            "2 years of experience",
        )


class DassaultTests(unittest.TestCase):
    @patch("job_radar.connectors.custom.fetch_json")
    def test_dassault_mapping(self, fetch_json):
        fetch_json.return_value = {
            "nhits": 1,
            "hits": [{
                "metas": [
                    {"name": "card_id", "value": "549001"},
                    {"name": "content_title", "value": "Data Engineer"},
                    {
                        "name": "content_info_2_value",
                        "value": "Spain, Barcelona",
                    },
                    {
                        "name": "content_cta_1_url",
                        "value": "https://www.3ds.com/careers/jobs/549001",
                    },
                    {
                        "name": "content_summary",
                        "value": "<p>Build Python data pipelines.</p>",
                    },
                ]
            }],
        }

        jobs = custom.fetch_dassault_systemes()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source, "dassault:careers")
        self.assertEqual(jobs[0].company, "Dassault Systèmes")
        self.assertEqual(jobs[0].description, "Build Python data pipelines.")


class VismaTests(unittest.TestCase):
    @patch("job_radar.connectors.custom.fetch_text")
    def test_visma_mapping(self, fetch_text):
        fetch_text.return_value = """
          <div role="listitem" class="openposition-list-item w-dyn-item">
            <div data-job-title="Data Engineer">
              <a href="https://jobs.example.com/123">Data Engineer</a>
              <div fs-cmssort-field="countries">Spain</div>
              <div class="text-size-small text-wrap line-break no-gap w-embed">
                &nbsp;|&nbsp;Madrid
              </div>
              <div fs-cmssort-field="areasofwork">Data Science</div>
              <div fs-cmssort-field="tags">Python, SQL</div>
            </div>
          </div>
        """

        jobs = custom.fetch_visma()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source, "visma:careers")
        self.assertEqual(jobs[0].location, "Spain, Madrid")
        self.assertEqual(jobs[0].description, "Data Science Python, SQL")


class SageTests(unittest.TestCase):
    def test_sage_listing_rows(self):
        page = """
          <tr class="dataRow even">
            <td><span>VN123</span></td>
            <td>
              <a href="/careers/fRecruit__ApplyJob?vacancyNo=VN123&amp;portal=English">
                Data Engineer
              </a>
            </td>
            <td>Engineering</td>
            <td><span>Spain</span></td>
            <td><span>Barcelona</span></td>
          </tr>
        """

        rows = custom.sage_listing_rows(page)

        self.assertEqual(rows, [{
            "id": "VN123",
            "title": "Data Engineer",
            "country": "Spain",
            "office": "Barcelona",
        }])


if __name__ == "__main__":
    unittest.main()
