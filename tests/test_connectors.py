import io
import json
import os
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.error import HTTPError

from job_radar import matching, notifications
from job_radar.connectors import ats, common, custom
from job_radar.connectors import registry
from job_radar.domain import Job
from job_radar.observability import SourceHealth


class JsonResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self, _size=-1):
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

        self.assertEqual(result.status, "Buena coincidencia")
        infer_countries.assert_called_once_with("Barcelona")

    def test_remote_europe_location_is_accepted(self):
        result = matching.classify(
            self.make_job(
                "Junior Data Engineer",
                location="Remote - Europe",
            )
        )

        self.assertEqual(result.status, "Buena coincidencia")

    def test_spanish_remote_word_is_accepted(self):
        result = matching.classify(
            self.make_job(
                "Junior Data Engineer",
                location="Remoto",
            )
        )

        self.assertEqual(result.status, "Buena coincidencia")

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
        self.assertEqual(result.status, "Buena coincidencia")

    def test_unspecified_data_engineer_is_stretch(self):
        result = matching.classify(
            self.make_job("Data Engineer")
        )
        self.assertEqual(result.status, "Stretch")

    def test_data_focused_software_engineer_is_stretch(self):
        result = matching.classify(
            self.make_job(
                "Software Engineer",
                "Build data pipelines, ETL systems and a data warehouse",
            )
        )
        self.assertEqual(result.status, "Stretch")

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

    def test_email_alerts_only_for_required_unhealthy_sources(self):
        failed_at = datetime(2026, 10, 8, tzinfo=timezone.utc)
        required = SourceHealth(
            company="Required & Co",
            source="greenhouse",
            required=True,
            health_status="unhealthy",
            recent_runs=10,
            successful_runs=6,
            success_rate=0.6,
            consecutive_failures=2,
            last_success_at=None,
            last_failure_at=failed_at,
            last_error_type="TimeoutError",
            last_error_message="upstream <down>",
        )
        optional = SourceHealth(
            company="Optional Co",
            source="portal propio",
            required=False,
            health_status="unhealthy",
            recent_runs=10,
            successful_runs=0,
            success_rate=0.0,
            consecutive_failures=10,
            last_success_at=None,
            last_failure_at=failed_at,
            last_error_type="ValueError",
            last_error_message="broken",
        )

        _subject, html, text = notifications.build_match_email(
            [self.make_match()],
            source_health=(required, optional),
        )

        self.assertIn("Fuentes required con problemas", html)
        self.assertIn("Required &amp; Co", html)
        self.assertIn("upstream &lt;down&gt;", html)
        self.assertIn("Required & Co", text)
        self.assertNotIn("Optional Co", html)
        self.assertNotIn("Optional Co", text)

    def test_email_without_required_unhealthy_source_is_unchanged(self):
        optional = SourceHealth(
            company="Optional Co",
            source="portal propio",
            required=False,
            health_status="unhealthy",
            recent_runs=2,
            successful_runs=0,
            success_rate=0.0,
            consecutive_failures=2,
            last_success_at=None,
            last_failure_at=None,
            last_error_type="ValueError",
            last_error_message="broken",
        )

        _subject, html, text = notifications.build_match_email(
            [self.make_match()],
            source_health=(optional,),
        )

        self.assertNotIn("Fuentes required con problemas", html)
        self.assertNotIn("ALERTA DE SALUD", text)

    def test_email_alerts_required_source_with_persistent_empty_snapshot(self):
        def healthy(company, *, required, empty_runs):
            return SourceHealth(
                company=company,
                source="greenhouse",
                required=required,
                health_status="healthy",
                recent_runs=10,
                successful_runs=10,
                success_rate=1.0,
                consecutive_failures=0,
                last_success_at=None,
                last_failure_at=None,
                last_error_type=None,
                last_error_message=None,
                consecutive_empty_snapshots=empty_runs,
            )

        _subject, html, text = notifications.build_match_email(
            [self.make_match()],
            source_health=(
                healthy("Moved & Co", required=True, empty_runs=3),
                healthy("Blip Co", required=True, empty_runs=2),
                healthy("Optional Co", required=False, empty_runs=10),
            ),
        )

        self.assertIn("Fuentes required con problemas", html)
        self.assertIn("Moved &amp; Co", html)
        self.assertIn("snapshot vacío en 3 ejecuciones seguidas", text)
        self.assertNotIn("Blip Co", text)
        self.assertNotIn("Optional Co", text)

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

    def test_email_separates_new_matches(self):
        run_started_at = datetime(2026, 10, 17, 8, tzinfo=timezone.utc)
        new = self.make_match()
        new["title"] = "Fresh Role"
        new["first_seen_at"] = datetime(
            2026, 10, 17, 8, 5, tzinfo=timezone.utc
        )
        old = self.make_match()
        old["title"] = "Known Role"
        old["first_seen_at"] = datetime(2026, 10, 1, tzinfo=timezone.utc)

        subject, html, text = notifications.build_match_email(
            [old, new],
            new_since=run_started_at,
        )

        self.assertIn("2 coincidencias · 1 nueva", subject)
        self.assertIn("Nuevas en esta ejecución (1)", html)
        self.assertIn("Resto de coincidencias activas (1)", html)
        self.assertLess(text.index("Fresh Role"), text.index("Known Role"))

    @patch("job_radar.notifications.email.get_active_matches")
    @patch("job_radar.notifications.email.urlopen")
    def test_send_notification_retries_transient_resend_errors(
        self,
        urlopen,
        get_active_matches,
    ):
        get_active_matches.return_value = [self.make_match()]
        unavailable = HTTPError(
            notifications.RESEND_EMAILS_URL, 503, "unavailable", {}, None
        )
        self.addCleanup(unavailable.close)
        urlopen.side_effect = [unavailable, JsonResponse({"id": "email-1"})]
        environment = {
            "RESEND_API_KEY": "re_test",
            "NOTIFICATION_EMAIL": "recipient@example.com",
            "NOTIFICATION_RUN_ID": "",
        }

        with patch.dict(os.environ, environment, clear=False):
            with patch("job_radar.connectors.common.time.sleep"):
                with self.assertLogs(common.LOGGER, level="WARNING"):
                    result = notifications.send_match_notification()

        self.assertEqual(result, "email-1")
        keys = [
            call.args[0].get_header("Idempotency-key")
            for call in urlopen.call_args_list
        ]
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0], keys[1])
        self.assertTrue(keys[0].startswith("job-radar/"))

    @patch("job_radar.notifications.email.get_active_matches")
    @patch("job_radar.notifications.email.urlopen")
    def test_send_notification_does_not_retry_validation_errors(
        self,
        urlopen,
        get_active_matches,
    ):
        get_active_matches.return_value = [self.make_match()]
        invalid = HTTPError(
            notifications.RESEND_EMAILS_URL, 422, "invalid", {}, None
        )
        self.addCleanup(invalid.close)
        urlopen.side_effect = invalid
        environment = {
            "RESEND_API_KEY": "re_test",
            "NOTIFICATION_EMAIL": "recipient@example.com",
        }

        with patch.dict(os.environ, environment, clear=False):
            with self.assertRaises(HTTPError):
                notifications.send_match_notification()

        urlopen.assert_called_once()

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
        self.assertEqual(jobs[0].url, "https://example.com/jobs/123")

    @patch("job_radar.connectors.ats.fetch_json")
    def test_greenhouse_upgrades_http_url_to_https(self, fetch_json):
        fetch_json.return_value = {
            "jobs": [
                {
                    "id": 5108007008,
                    "title": "Data Engineer",
                    "location": {"name": "Madrid, Spain"},
                    "absolute_url": (
                        "http://block.xyz/careers/jobs/5108007008"
                        "?gh_jid=5108007008"
                    ),
                    "content": "",
                }
            ]
        }

        jobs = ats.fetch_greenhouse("Block", "block")

        self.assertEqual(
            jobs[0].url,
            "https://block.xyz/careers/jobs/5108007008?gh_jid=5108007008",
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

        with self.assertLogs(common.LOGGER, level="INFO"):
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

    @patch("job_radar.connectors.custom.fetch_deel_job")
    @patch("job_radar.connectors.custom.fetch_text")
    def test_failed_details_mark_snapshot_incomplete(
        self,
        fetch_text,
        fetch_deel_job,
    ):
        ids = [
            "161d3133-1185-405b-a037-0f5aa14ee60b",
            "261d3133-1185-405b-a037-0f5aa14ee60b",
        ]
        fetch_text.return_value = "".join(
            f'<a href="/klarna/job-details/{job_id}/overview">Role</a>'
            for job_id in ids
        )
        job = Job(
            source="deel:klarna",
            source_job_id=ids[0],
            company="Klarna",
            title="Data Engineer",
            location="Stockholm, Sweden",
            url=f"https://jobs.deel.com/klarna/job-details/{ids[0]}/overview",
            description="",
            salary_text=None,
            experience_text=None,
        )

        def fetch_detail(job_id, *_args):
            if job_id != ids[0]:
                raise TimeoutError("detail timed out")
            return job

        fetch_deel_job.side_effect = fetch_detail

        with self.assertLogs(custom.LOGGER, level="WARNING") as logs:
            jobs = custom.fetch_deel_company("Klarna", "klarna")

        self.assertIsInstance(jobs, common.IncompleteSnapshot)
        self.assertEqual(jobs.failed_ids, {ids[1]})
        self.assertEqual([item.source_job_id for item in jobs], [ids[0]])
        self.assertIn("error_types=TimeoutError", "\n".join(logs.output))
        self.assertIn(f"failed_ids={ids[1]}", "\n".join(logs.output))

    @patch("job_radar.connectors.custom.fetch_text")
    def test_detail_without_jsonld_is_kept_as_listed(self, fetch_text):
        ids = [
            "161d3133-1185-405b-a037-0f5aa14ee60b",
            "261d3133-1185-405b-a037-0f5aa14ee60b",
        ]
        listing = "".join(
            f'<a href="/klarna/job-details/{job_id}/overview">Role</a>'
            for job_id in ids
        )
        detail = """
            <script type="application/ld+json">
            {"@type": "JobPosting", "title": "Data Engineer",
             "description": "Python."}
            </script>
        """
        fetch_text.side_effect = lambda url, **_kwargs: (
            listing if url.endswith("/klarna")
            else detail if ids[0] in url
            else "<html>challenge</html>"
        )

        with self.assertLogs(custom.LOGGER, level="WARNING") as logs:
            jobs = custom.fetch_deel_company("Klarna", "klarna")

        self.assertEqual(jobs.failed_ids, {ids[1]})
        self.assertIn("missing_jsonld", "\n".join(logs.output))


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
            common.is_relevant_title("Data Platform Engineer")
        )
        self.assertFalse(
            common.is_relevant_title("Commercial Account Manager")
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

        with self.assertLogs(common.LOGGER, level="INFO"):
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

    @patch("job_radar.connectors.common.urlopen")
    def test_workday_keeps_additional_locations(self, urlopen):
        urlopen.side_effect = [
            JsonResponse({
                "jobPostings": [{
                    "title": "Data Engineer",
                    "locationsText": "3 Locations",
                    "externalPath": "/job/London/Data-Engineer_R789",
                }]
            }),
            JsonResponse({
                "jobPostingInfo": {
                    "location": "London",
                    "additionalLocations": ["Madrid", "London", "Lisbon"],
                    "jobDescription": "<p>Build data pipelines.</p>",
                }
            }),
        ]

        with self.assertLogs(common.LOGGER, level="INFO"):
            jobs = ats.fetch_workday(
                "Example Bank",
                "example.wd3.myworkdayjobs.com",
                "example",
                "Careers",
            )

        self.assertEqual(jobs[0].location, "London; Madrid; Lisbon")

    @patch("job_radar.connectors.common.time.sleep")
    @patch("job_radar.connectors.common.urlopen")
    def test_workday_retries_empty_listing_body(self, urlopen, _sleep):
        empty = JsonResponse(None)
        empty.read = lambda _size=-1: b""
        urlopen.side_effect = [
            empty,
            JsonResponse({
                "jobPostings": [{
                    "title": "Sales Executive",
                    "locationsText": "Madrid, Spain",
                    "externalPath": "/job/Madrid/Sales_R456",
                }]
            }),
        ]

        with self.assertLogs(common.LOGGER, level="WARNING"):
            jobs = ats.fetch_workday(
                "Example Bank",
                "example.wd3.myworkdayjobs.com",
                "example",
                "Careers",
            )

        self.assertEqual([job.source_job_id for job in jobs], ["Sales_R456"])
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
            92 + len(greenhouse_companies) + len(ashby_companies),
        )

    def test_spain_product_and_fintech_sources_are_configured(self):
        expected = [
            (registry.GREENHOUSE_COMPANIES, {
                "Wallapop": "wallapop",
                "Databricks": "databricks",
                "Fivetran": "fivetran",
                "SumUp": "sumup",
                "Bitpanda": "bitpanda",
                "Elastic": "elastic",
                "GitLab": "gitlab",
                "Grafana Labs": "grafanalabs",
                "TheFork": "thefork",
                "Payhawk": "payhawkio",
            }),
            (registry.ASHBY_COMPANIES, {
                "Preply": "preply",
                "Perk (TravelPerk)": "perk",
                "Docplanner (Doctoralia)": "docplanner",
                "Pennylane": "pennylane",
                "Kraken": "kraken.com",
                "Alan": "alan",
                "Satispay": "satispay",
                "ElevenLabs": "elevenlabs",
                "Oyster HR": "oyster",
                "Amenitiz": "amenitiz",
                "Pliant": "pliant",
                "Mistral AI": "mistral.ai",
                "Prefect": "prefect",
                "PostHog": "posthog",
                "Primer": "primer",
            }),
            (registry.LEVER_COMPANIES, {
                "Jobandtalent": "jobandtalent",
                "Metabase": "metabase",
                "Tinybird": "tinybird",
            }),
            (registry.RECRUITEE_COMPANIES, {
                "SeQura": "sequra",
                "Holded": "holded",
            }),
            (registry.PERSONIO_COMPANIES, {
                "Indexa Capital": "talento-indexa-capital.jobs.personio.com",
            }),
            (registry.PINPOINT_COMPANIES, {"CARTO": "carto"}),
            (registry.WORKABLE_COMPANIES, {
                "Kantox": "kantox",
                "Yapily": "yapily",
            }),
            (registry.TEAMTAILOR_COMPANIES, {
                "Freepik": (
                    "https://careers.freepik.com",
                    "teamtailor:freepik",
                ),
                "PayFit": (
                    "https://careers.payfit.com",
                    "teamtailor:payfit",
                ),
            }),
        ]

        for companies, slugs in expected:
            for company, slug in slugs.items():
                with self.subTest(company=company):
                    self.assertEqual(companies[company], slug)

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
        # Nubank dejó de publicar en Greenhouse (board vacía) y usa Ashby.
        self.assertNotIn("Nubank", registry.GREENHOUSE_COMPANIES)
        self.assertEqual(registry.ASHBY_COMPANIES["Nubank"], "nubank")
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


class RecruiteeTests(unittest.TestCase):
    @patch("job_radar.connectors.ats.fetch_json")
    def test_published_offers_mapping(self, fetch_json):
        fetch_json.return_value = {
            "offers": [
                {
                    "id": 101,
                    "status": "published",
                    "title": "Data Engineer",
                    "location": "Barcelona, Spain",
                    "locations": [{"city": "Madrid", "country": "Spain"}],
                    "remote": True,
                    "careers_url": "https://example.recruitee.com/o/data",
                    "description": "<p>Python and SQL.</p>",
                    "requirements": "<p>2 years of experience.</p>",
                    "salary": {
                        "min": 40000,
                        "max": 50000,
                        "currency": "EUR",
                        "period": "year",
                    },
                },
                {"id": 102, "status": "draft", "title": "Hidden"},
            ]
        }

        jobs = ats.fetch_recruitee("Example", "example")

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source, "recruitee:example")
        self.assertEqual(jobs[0].source_job_id, "101")
        self.assertEqual(
            jobs[0].location,
            "Barcelona, Spain; Madrid, Spain; Remote",
        )
        self.assertEqual(jobs[0].salary_text, "40000 – 50000 EUR / year")
        self.assertEqual(jobs[0].experience_text, "2 years of experience")


class PersonioTests(unittest.TestCase):
    @patch("job_radar.connectors.ats.fetch_text")
    def test_xml_mapping(self, fetch_text):
        fetch_text.return_value = """<?xml version="1.0" encoding="UTF-8"?>
            <workzag-jobs><position>
              <id>2707763</id>
              <office>Madrid</office>
              <additionalOffices><office>Remoto</office></additionalOffices>
              <name>Data Analyst</name>
              <jobDescriptions><jobDescription>
                <name>Tareas</name><value><![CDATA[<p>SQL</p>]]></value>
              </jobDescription></jobDescriptions>
              <yearsOfExperience>1-2</yearsOfExperience>
              <salaryInformation>
                <min>24000.00</min><max>30000.00</max>
                <currencyCode>EUR</currencyCode><type>yearly</type>
              </salaryInformation>
            </position></workzag-jobs>"""

        jobs = ats.fetch_personio("Indexa", "talento.jobs.personio.com")

        self.assertEqual(jobs[0].source, "personio:talento")
        self.assertEqual(jobs[0].source_job_id, "2707763")
        self.assertEqual(jobs[0].location, "Madrid; Remoto")
        self.assertEqual(
            jobs[0].url,
            "https://talento.jobs.personio.com/job/2707763",
        )
        self.assertEqual(jobs[0].description, "SQL")
        self.assertEqual(
            jobs[0].salary_text,
            "24000.00 – 30000.00 EUR / yearly",
        )
        self.assertEqual(jobs[0].experience_text, "1-2 years of experience")

    @patch("job_radar.connectors.ats.fetch_text", return_value="<html>")
    def test_invalid_xml_is_a_recoverable_source_error(self, _fetch_text):
        with self.assertRaises(ValueError):
            ats.fetch_personio("Indexa", "talento.jobs.personio.com")


class PinpointTests(unittest.TestCase):
    @patch("job_radar.connectors.ats.fetch_json")
    def test_postings_mapping(self, fetch_json):
        fetch_json.return_value = {
            "data": [{
                "id": "575804",
                "title": "Data Engineer",
                "url": "https://carto.pinpointhq.com/en/postings/abc",
                "location": {"name": "Spain or Argentina"},
                "workplace_type": "remote",
                "description": "<p>Pipelines.</p>",
                "key_responsibilities": None,
                "skills_knowledge_expertise": "<p>3 years of experience</p>",
                "compensation_visible": False,
                "compensation_minimum": 1,
            }]
        }

        jobs = ats.fetch_pinpoint("CARTO", "carto")

        self.assertEqual(jobs[0].source, "pinpoint:carto")
        self.assertEqual(jobs[0].location, "Spain or Argentina; Remote")
        self.assertIsNone(jobs[0].salary_text)
        self.assertEqual(jobs[0].experience_text, "3 years of experience")


class WorkableTests(unittest.TestCase):
    @patch("job_radar.connectors.ats.fetch_json")
    def test_listing_and_relevant_job_enrichment(self, fetch_json):
        fetch_json.side_effect = [
            {
                "jobs": [
                    {
                        "shortcode": "AAA",
                        "title": "Data Platform Engineer",
                        "url": "https://apply.workable.com/j/AAA",
                        "telecommuting": True,
                        "locations": [
                            {"city": "Madrid", "country": "Spain"},
                            {"city": "Hidden", "hidden": True},
                        ],
                    },
                    {
                        "shortcode": "BBB",
                        "title": "Account Executive",
                        "url": "https://apply.workable.com/j/BBB",
                        "locations": [],
                    },
                ]
            },
            {
                "description": "<p>Python.</p>",
                "requirements": "<p>2 years of experience.</p>",
            },
        ]

        with self.assertLogs(common.LOGGER, level="INFO"):
            jobs = ats.fetch_workable("Yapily", "yapily")

        self.assertEqual(jobs[0].source, "workable:yapily")
        self.assertEqual(jobs[0].source_job_id, "AAA")
        self.assertEqual(jobs[0].location, "Madrid, Spain; Remote")
        self.assertEqual(jobs[0].experience_text, "2 years of experience")
        self.assertEqual(jobs[1].description, "")
        self.assertEqual(fetch_json.call_count, 2)


class FactorialTests(unittest.TestCase):
    PAGE = """
        <select id="location_filter">
          <option value="">All locations</option>
          <option value="229308">Madrid</option>
        </select>
        <ul>
          <li class="job-offer-item w-full" data-is-remote="true"
              data-job-postings-url="https://embat.factorial.com/job_posting/backend-engineer-280456"
              data-location-id="229308">
            <div class="text-sm font-bold text-gray-500">Backend Engineer
            </div>
            <div class="text-sm font-normal">Technology</div>
          </li>
        </ul>
    """

    @patch("job_radar.connectors.ats.fetch_text")
    def test_job_cards_mapping(self, fetch_text):
        fetch_text.return_value = self.PAGE

        jobs = ats.fetch_factorial("Embat", "embat.factorialhr.com")

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source, "factorial:embat")
        self.assertEqual(jobs[0].source_job_id, "280456")
        self.assertEqual(jobs[0].title, "Backend Engineer")
        self.assertEqual(jobs[0].location, "Madrid; Remote")

    @patch("job_radar.connectors.ats.fetch_text")
    def test_unparseable_cards_fail_visibly(self, fetch_text):
        fetch_text.return_value = (
            '<li class="job-offer-item" data-job-postings-url="/x"></li>'
        )

        with self.assertRaises(ValueError):
            ats.fetch_factorial("Embat", "embat.factorialhr.com")


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

        with self.assertLogs(common.LOGGER, level="INFO"):
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


    @patch("job_radar.connectors.custom.fetch_text")
    @patch("job_radar.connectors.custom.read_url")
    def test_pagination_and_relevant_job_enrichment(
        self,
        read_url,
        fetch_text,
    ):
        def listing(rows, next_link):
            body = (
                '<form id="form1" action="/careers/fRecruit__ApplyJobList">'
                '<input type="hidden" name="ViewState" value="VS1">'
            )

            for vacancy, title, country in rows:
                body += (
                    f'<tr class="dataRow"><td>{vacancy}</td><td>'
                    '<a href="/careers/fRecruit__ApplyJob?'
                    f'vacancyNo={vacancy}&amp;portal=English">{title}</a>'
                    f"</td><td>Eng</td><td>{country}</td><td>Office</td></tr>"
                )

            if next_link:
                body += (
                    '<a href="#" onclick="jsfcljs(document.forms[0],'
                    "'form1:next,form1:next','');\" "
                    'class="link-pagination">Next</a>'
                )

            return (body + "</form>").encode("utf-8")

        read_url.side_effect = [
            listing(
                [("VN1", "Data Engineer", "UK"), ("VN2", "Sales", "UK")],
                next_link=True,
            ),
            listing([("VN3", "Data Analyst", "Spain")], next_link=False),
        ]
        fetch_text.side_effect = lambda url, **_kwargs: (
            '<script type="application/ld+json">{"@type": "JobPosting", '
            '"description": "<p>2 years of experience</p>", '
            '"jobLocation": {"address": {"addressLocality": "Newcastle"}}}'
            "</script>"
            if "VN1" in url
            else "<html>no JSON-LD</html>"
        )

        with self.assertLogs(common.LOGGER, level="INFO") as logs:
            jobs = custom.fetch_sage()

        def url(vacancy):
            return (
                "https://sagehr.my.salesforce-sites.com/careers/"
                f"fRecruit__ApplyJob?vacancyNo={vacancy}&portal=English"
            )

        common_fields = {"source": "sagepeople:sage", "company": "Sage"}
        self.assertEqual(jobs, [
            make_job(
                **common_fields,
                source_job_id="VN1",
                title="Data Engineer",
                location="Newcastle",
                url=url("VN1"),
                description="2 years of experience",
                experience_text="2 years of experience",
            ),
            make_job(
                **common_fields,
                source_job_id="VN2",
                title="Sales",
                location="Office, UK",
                url=url("VN2"),
            ),
            make_job(
                **common_fields,
                source_job_id="VN3",
                title="Data Analyst",
                location="Office, Spain",
                url=url("VN3"),
            ),
        ])
        postback = read_url.call_args_list[1].args[0]
        self.assertEqual(
            postback.data.decode("utf-8"),
            "ViewState=VS1&form1=form1&form1%3Anext=form1%3Anext",
        )
        self.assertIn(
            "enrichment_finished company='Sage' source=sagepeople "
            "enriched=1 failures=1",
            "\n".join(logs.output),
        )


def make_job(**fields):
    defaults = {
        "location": None,
        "description": "",
        "salary_text": None,
        "experience_text": None,
    }
    return Job(**{**defaults, **fields})


class ConnectorHelperTests(unittest.TestCase):
    def test_join_unique_skips_empty_values_and_duplicates(self):
        self.assertEqual(
            common.join_unique(["Madrid", None, "", "Remote", "Madrid"]),
            "Madrid; Remote",
        )
        self.assertEqual(
            common.join_unique(["Lekki", "Lagos"], separator=", "),
            "Lekki, Lagos",
        )
        self.assertIsNone(common.join_unique([None, ""]))

    def test_text_fields_prefers_structured_salary_and_extracted_experience(
        self,
    ):
        self.assertEqual(
            common.text_fields(
                "Salary €40,000 - €50,000. 2 years of experience.",
                salary_text="40k EUR",
                experience_fallback="5 years of experience",
            ),
            {
                "description": (
                    "Salary €40,000 - €50,000. 2 years of experience."
                ),
                "salary_text": "40k EUR",
                "experience_text": "2 years of experience",
            },
        )
        self.assertEqual(
            common.text_fields("", experience_fallback="1-2 years"),
            {
                "description": "",
                "salary_text": None,
                "experience_text": "1-2 years",
            },
        )

    def test_format_salary(self):
        self.assertEqual(
            ats.format_salary(1, 2, "EUR", "per-year-salary"),
            "1 – 2 EUR / per year salary",
        )
        self.assertEqual(ats.format_salary(None, 9), "9")
        self.assertIsNone(ats.format_salary(None, None, "EUR", "year"))

    def test_offset_pages_stop_at_total_or_empty_page(self):
        pages = {
            0: {"items": [1, 2], "total": 3},
            2: {"items": [3], "total": 3},
        }
        offsets = []

        def fetch_page(offset):
            offsets.append(offset)
            return pages.get(offset, {})

        self.assertEqual(
            common.fetch_offset_pages(
                fetch_page,
                items_key="items",
                total_key="total",
            ),
            [1, 2, 3],
        )
        self.assertEqual(offsets, [0, 2])


class TeamtailorTests(unittest.TestCase):
    @patch("job_radar.connectors.custom.fetch_json")
    def test_jsonld_location_and_salary_mapping(self, fetch_json):
        fetch_json.return_value = {"items": [
            {
                "id": 1,
                "title": "Data Engineer",
                "url": "https://jobs.example.com/1",
                "content_html": "<p>2 years of experience</p>",
                "_jobposting": {
                    "jobLocation": [
                        {"address": {
                            "addressLocality": "Madrid",
                            "addressRegion": {"name": "Madrid"},
                            "addressCountry": "ES",
                        }},
                        {"address": {
                            "addressLocality": "Madrid",
                            "addressCountry": "ES",
                        }},
                        "invalid",
                        {"address": {}},
                    ],
                    "jobLocationType": "TELECOMMUTE",
                    "applicantLocationRequirements": [
                        {"name": "Spain"},
                        {},
                        {"name": "Portugal"},
                    ],
                    "baseSalary": {
                        "currency": "EUR",
                        "value": {
                            "minValue": 40000,
                            "maxValue": 50000,
                            "unitText": "YEAR",
                        },
                    },
                },
            },
            {
                "id": 2,
                "title": "Analyst",
                "url": "https://jobs.example.com/2",
                "_jobposting": {
                    "description": "<p>Python</p>",
                    "jobLocation": {
                        "address": {"addressCountry": {"name": "PT"}},
                    },
                    "jobLocationType": "TELECOMMUTE",
                    "applicantLocationRequirements": {"name": "EU"},
                    "baseSalary": {"value": {"value": 7}},
                },
            },
            {
                "id": 3,
                "title": "Bare",
                "url": "https://jobs.example.com/3",
                "_jobposting": None,
            },
        ]}

        jobs = custom.fetch_teamtailor_company(
            "Example",
            "https://jobs.example.com/",
            "teamtailor:example",
        )

        common_fields = {"source": "teamtailor:example", "company": "Example"}
        self.assertEqual(jobs, [
            make_job(
                **common_fields,
                source_job_id="1",
                title="Data Engineer",
                location="Madrid, ES; Remote - Spain, Portugal",
                url="https://jobs.example.com/1",
                description="2 years of experience",
                salary_text="40000 – 50000 EUR / year",
                experience_text="2 years of experience",
            ),
            make_job(
                **common_fields,
                source_job_id="2",
                title="Analyst",
                location="PT; Remote - EU",
                url="https://jobs.example.com/2",
                description="Python",
                salary_text="7",
            ),
            make_job(
                **common_fields,
                source_job_id="3",
                title="Bare",
                url="https://jobs.example.com/3",
            ),
        ])
        fetch_json.assert_called_once_with(
            "https://jobs.example.com/jobs.json",
            context="company='Example' source=teamtailor:example",
        )

    def test_catch_all_teamtailor_sources_are_configured(self):
        connectors = {
            connector.company: connector for connector in registry.CONNECTORS
        }

        for company, base_url, source in (
            ("Spendesk", "https://career.spendesk.com", "teamtailor:spendesk"),
            ("Seedtag", "https://jobs.seedtag.com", "teamtailor:seedtag"),
            (
                "Lingokids",
                "https://jobs.lingokids.com",
                "teamtailor:lingokids",
            ),
        ):
            with self.subTest(company=company):
                connector = connectors[company]
                self.assertEqual(connector.source, "teamtailor")
                self.assertTrue(connector.catch_all)
                self.assertEqual(
                    connector.fetcher.args,
                    (company, base_url, source),
                )


class EightfoldPaginationTests(unittest.TestCase):
    @patch("job_radar.connectors.ats.fetch_json")
    def test_pages_deduplicate_ids_and_failed_details_keep_listing(
        self,
        fetch_json,
    ):
        def respond(url, **_kwargs):
            if "start=0" in url:
                return {"data": {"count": 3, "positions": [
                    {"id": 1, "name": "Data Engineer", "locations": ["A"]},
                    {"id": 1, "name": "Duplicate"},
                ]}}
            if "start=2" in url:
                return {"data": {"count": 3, "positions": [
                    {"id": 2, "name": "Data Analyst", "locations": ["B"]},
                ]}}
            if "position_id=1" in url:
                raise TimeoutError("detail timed out")
            return {"data": {
                "locations": ["Madrid"],
                "jobDescription": "<p>2 years of experience</p>",
            }}

        fetch_json.side_effect = respond

        with self.assertLogs(common.LOGGER, level="INFO") as logs:
            jobs = ats.fetch_eightfold("PayPal", "paypal.example", "x.com")

        self.assertEqual(jobs, [
            make_job(
                source="eightfold:x.com",
                source_job_id="1",
                company="PayPal",
                title="Data Engineer",
                location="A",
                url="https://paypal.example/careers/job/1?domain=x.com",
            ),
            make_job(
                source="eightfold:x.com",
                source_job_id="2",
                company="PayPal",
                title="Data Analyst",
                location="Madrid",
                url="https://paypal.example/careers/job/2?domain=x.com",
                description="2 years of experience",
                experience_text="2 years of experience",
            ),
        ])
        output = "\n".join(logs.output)
        self.assertIn(
            "enrichment_started company='PayPal' source=eightfold "
            "candidates=2",
            output,
        )
        self.assertIn("error_type=TimeoutError", output)
        self.assertIn(
            "enrichment_finished company='PayPal' source=eightfold "
            "enriched=1 failures=1",
            output,
        )


class WorkableErrorTests(unittest.TestCase):
    LISTING = {"jobs": [{
        "shortcode": "AAA",
        "title": "Data Engineer",
        "url": "https://apply.workable.com/j/AAA",
    }]}

    @patch("job_radar.connectors.ats.fetch_json")
    def test_recoverable_detail_error_keeps_listing_job(self, fetch_json):
        fetch_json.side_effect = [self.LISTING, ValueError("bad json")]

        with self.assertLogs(common.LOGGER, level="INFO") as logs:
            jobs = ats.fetch_workable("Yapily", "yapily")

        self.assertEqual(jobs[0].description, "")
        self.assertIn(
            "enrichment_finished company='Yapily' source=workable "
            "enriched=0 failures=1",
            "\n".join(logs.output),
        )

    @patch("job_radar.connectors.ats.fetch_json")
    def test_unexpected_detail_error_propagates(self, fetch_json):
        fetch_json.side_effect = [self.LISTING, KeyError("contract")]

        with self.assertLogs(common.LOGGER, level="INFO"):
            with self.assertRaises(KeyError):
                ats.fetch_workable("Yapily", "yapily")


class DassaultPaginationTests(unittest.TestCase):
    @patch("job_radar.connectors.custom.fetch_json")
    def test_offset_pagination_and_required_fields(self, fetch_json):
        def hit(job_id, url_key="content_cta_1_url"):
            return {"metas": [
                {"name": "card_id", "value": job_id},
                {"name": "content_title", "value": f"Job {job_id}"},
                {"name": url_key, "value": f"https://3ds.example/{job_id}"},
            ]}

        fetch_json.side_effect = [
            {"nhits": 3, "hits": [hit("1"), {"metas": [{"value": "x"}]}]},
            {"nhits": 3, "hits": [hit("3", "content_cta_1_url_id")]},
        ]

        jobs = custom.fetch_dassault_systemes()

        self.assertEqual(
            [(job.source_job_id, job.url) for job in jobs],
            [("1", "https://3ds.example/1"), ("3", "https://3ds.example/3")],
        )
        offsets = [
            call.args[0].split("&b=")[1].split("&")[0]
            for call in fetch_json.call_args_list
        ]
        self.assertEqual(offsets, ["0", "2"])


class ThetaRayTests(unittest.TestCase):
    @patch("job_radar.connectors.custom.fetch_json")
    def test_comeet_mapping(self, fetch_json):
        fetch_json.return_value = [
            {
                "uid": "U1",
                "name": "Data Engineer",
                "details": [
                    {"value": "<p>2 years of experience</p>"},
                    {"value": None},
                ],
                "location": {"name": "Tel Aviv"},
                "url_comeet_hosted_page": "https://comeet.example/1",
            },
            {
                "uid": "U2",
                "name": "Internal",
                "is_internal": True,
                "url_active_page": "https://comeet.example/2",
            },
            {"uid": "U3", "name": "Without URL"},
            {
                "uid": 4,
                "name": "Plain location",
                "location": "Remote",
                "url_recruit_hosted_page": "https://comeet.example/4",
            },
        ]

        jobs = custom.fetch_thetaray()

        self.assertEqual(jobs, [
            make_job(
                source="comeet:thetaray",
                source_job_id="U1",
                company="ThetaRay",
                title="Data Engineer",
                location="Tel Aviv",
                url="https://comeet.example/1",
                description="2 years of experience",
                experience_text="2 years of experience",
            ),
            make_job(
                source="comeet:thetaray",
                source_job_id="4",
                company="ThetaRay",
                title="Plain location",
                url="https://comeet.example/4",
            ),
        ])


class MambuTests(unittest.TestCase):
    @patch("job_radar.connectors.custom.fetch_text")
    def test_listing_and_detail_pages(self, fetch_text):
        pages = {
            "search": (
                '<a href="/jobs/12/data-engineer/job">x</a>'
                '<a href="/jobs/12/data-engineer/job?x">x</a>'
                "<a href='/jobs/7/ops/job'>y</a>"
            ),
            "/jobs/12/": (
                "<h1>Data <b>Engineer</b></h1>"
                "<div>Job Locations ES-Madrid Posted Date 1 day ago</div>"
            ),
            "/jobs/7/": "<html>no title</html>",
        }
        fetch_text.side_effect = lambda url, **_kwargs: next(
            body for key, body in pages.items() if key in url
        )

        jobs = custom.fetch_mambu()

        url = "https://careers-mambu.icims.com/jobs/12/job?in_iframe=1"
        self.assertEqual(jobs, [make_job(
            source="icims:mambu",
            source_job_id="12",
            company="Mambu",
            title="Data Engineer",
            location="ES-Madrid",
            url=url,
            description=(
                "Data Engineer Job Locations ES-Madrid Posted Date 1 day ago"
            ),
        )])


if __name__ == "__main__":
    unittest.main()
