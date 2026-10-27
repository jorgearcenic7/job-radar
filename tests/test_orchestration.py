import unittest
from dataclasses import dataclass
from unittest.mock import ANY, patch

from job_radar.connectors import IncompleteSnapshot
from job_radar.domain import Job, PreparedJob
from job_radar.matching import classify
from job_radar.orchestration import runner
from job_radar.observability import HEALTH_WINDOW
from job_radar.storage import SourceSnapshot


@dataclass
class StubConnector:
    company: str = "Example"
    source: str = "stub"
    required: bool = True
    catch_all: bool = False
    error: Exception | None = None
    jobs_count: int = 1

    def fetch(self) -> list[Job]:
        if self.error:
            raise self.error

        return [
            Job(
                source="stub",
                source_job_id=f"job-{self.company}-{index}",
                company=self.company,
                title="Junior Data Engineer",
                location="Madrid, Spain",
                url="https://example.com/jobs/job-1",
                description="Python, SQL and data pipelines",
                salary_text=None,
                experience_text="2 years of experience",
            )
            for index in range(self.jobs_count)
        ]


class RecordingRunRepository:
    def __init__(self, snapshot_history=None):
        self.sources_total = None
        self.source_starts = []
        self.source_finishes = []
        self.ingestion_finish = None
        self.snapshot_history = snapshot_history or []
        self.health_history = {}
        self.health_limit = None

    def create_ingestion_run(self, sources_total):
        self.sources_total = sources_total
        return 100

    def create_source_run(self, ingestion_run_id, *, company, source):
        source_run_id = 200 + len(self.source_starts)
        self.source_starts.append({
            "id": source_run_id,
            "ingestion_run_id": ingestion_run_id,
            "company": company,
            "source": source,
        })
        return source_run_id

    def finish_source_run(self, source_run_id, **values):
        self.source_finishes.append({"id": source_run_id, **values})

    def get_source_snapshot_history(self, *, company, source, limit):
        return self.snapshot_history[:limit]

    def get_recent_source_runs(self, *, limit):
        self.health_limit = limit
        return self.health_history

    def finish_ingestion_run(self, run_id, **values):
        self.ingestion_finish = {"id": run_id, **values}


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(
            runner,
            "close_retired_companies",
            return_value=0,
        )
        self.close_retired_companies = patcher.start()
        self.addCleanup(patcher.stop)

    def test_each_job_is_classified_once_before_persistence(self):
        connector = StubConnector(jobs_count=3)
        metrics = runner.SourceMetrics()
        repository = RecordingRunRepository()

        with patch(
            "job_radar.matching.preparation.classify",
            wraps=classify,
        ) as classify_spy:
            with patch.object(
                runner,
                "save_jobs",
                return_value=(3, 0),
            ) as save_jobs:
                runner.process_connector(connector, metrics, repository)

        self.assertEqual(classify_spy.call_count, 3)
        self.assertEqual(metrics.jobs_seen, 3)
        self.assertEqual(metrics.matches, 3)
        prepared_jobs = save_jobs.call_args.args[0]
        self.assertEqual(len(prepared_jobs), 3)
        self.assertTrue(
            all(
                isinstance(prepared, PreparedJob)
                for prepared in prepared_jobs
            )
        )

    @patch("job_radar.orchestration.runner.send_match_notification")
    @patch("job_radar.orchestration.runner.save_jobs", return_value=(2, 1))
    @patch(
        "job_radar.orchestration.runner.time.perf_counter",
        side_effect=[10.0, 10.125],
    )
    def test_success_records_source_and_aggregated_metrics(
        self,
        _perf_counter,
        save_jobs,
        send_match_notification,
    ):
        repository = RecordingRunRepository()
        connector = StubConnector()

        with patch.object(runner, "CONNECTORS", (connector,)):
            with self.assertLogs(runner.LOGGER, level="INFO") as logs:
                exit_code = runner.run(repository)

        self.assertEqual(exit_code, 0)
        self.assertEqual(repository.sources_total, 1)
        self.assertEqual(len(repository.source_starts), 1)
        self.assertEqual(
            repository.source_finishes,
            [{
                "id": 200,
                "status": "success",
                "jobs_seen": 1,
                "jobs_new": 2,
                "jobs_closed": 1,
                "matches": 1,
                "duration_ms": 125,
                "snapshot_status": "healthy",
                "closure_suppressed": False,
            }],
        )
        self.assertEqual(
            repository.ingestion_finish,
            {
                "id": 100,
                "status": "success",
                "sources_succeeded": 1,
                "sources_failed": 0,
                "jobs_seen": 1,
                "jobs_new": 2,
                "jobs_closed": 1,
                "matches": 1,
            },
        )
        save_jobs.assert_called_once_with(
            ANY,
            "Example",
            close_missing=True,
            keep_ids=frozenset(),
        )
        send_match_notification.assert_called_once()
        notification = send_match_notification.call_args.kwargs
        self.assertFalse(notification["partial"])
        self.assertEqual(repository.health_limit, HEALTH_WINDOW)
        self.assertEqual(len(notification["source_health"]), 1)
        self.assertEqual(
            notification["source_health"][0].health_status,
            "unknown",
        )
        output = "\n".join(logs.output)
        self.assertIn("run_id=100", output)
        self.assertIn("company='Example'", output)
        self.assertIn("source=stub", output)
        self.assertIn("jobs_seen=1", output)
        self.assertIn("matches=1", output)
        self.assertIn("duration_ms=125", output)
        self.assertIn("status=success", output)

    @patch("job_radar.orchestration.runner.send_match_notification")
    @patch("job_radar.orchestration.runner.save_jobs", return_value=(1, 0))
    @patch(
        "job_radar.orchestration.runner.time.perf_counter",
        side_effect=[1.0, 1.05, 2.0, 2.2],
    )
    def test_failed_source_is_recorded_and_next_source_runs(
        self,
        _perf_counter,
        save_jobs,
        send_match_notification,
    ):
        repository = RecordingRunRepository()
        failed = StubConnector(error=ValueError("broken snapshot"))
        successful = StubConnector(company="Second source")

        with patch.object(runner, "CONNECTORS", (failed, successful)):
            with self.assertLogs(runner.LOGGER, level="INFO"):
                exit_code = runner.run(repository)

        self.assertEqual(exit_code, 1)
        self.assertEqual(len(repository.source_starts), 2)
        self.assertEqual(
            [item["status"] for item in repository.source_finishes],
            ["failed", "success"],
        )
        self.assertEqual(repository.source_finishes[0]["duration_ms"], 50)
        self.assertEqual(
            repository.source_finishes[0]["error_type"],
            "ValueError",
        )
        self.assertEqual(
            repository.source_finishes[0]["error_message"],
            "broken snapshot",
        )
        self.assertEqual(
            repository.source_finishes[0]["snapshot_status"],
            "unknown",
        )
        self.assertEqual(repository.source_finishes[1]["duration_ms"], 200)
        self.assertEqual(
            repository.ingestion_finish,
            {
                "id": 100,
                "status": "partial",
                "sources_succeeded": 1,
                "sources_failed": 1,
                "jobs_seen": 1,
                "jobs_new": 1,
                "jobs_closed": 0,
                "matches": 1,
            },
        )
        save_jobs.assert_called_once()
        send_match_notification.assert_called_once_with(
            partial=True,
            source_health=ANY,
            new_since=ANY,
        )

    @patch("job_radar.orchestration.runner.send_match_notification")
    def test_optional_source_failure_is_partial_but_exit_remains_zero(
        self,
        send_match_notification,
    ):
        repository = RecordingRunRepository()
        connector = StubConnector(
            required=False,
            catch_all=True,
            error=RuntimeError("optional source failed"),
        )

        with patch.object(runner, "CONNECTORS", (connector,)):
            with self.assertLogs(runner.LOGGER, level="INFO"):
                exit_code = runner.run(repository)

        self.assertEqual(exit_code, 0)
        self.assertEqual(repository.ingestion_finish["status"], "partial")
        send_match_notification.assert_called_once_with(
            partial=False,
            source_health=ANY,
            new_since=ANY,
        )

    @patch("job_radar.orchestration.runner.send_match_notification")
    def test_fatal_failure_finalizes_run_before_propagating(
        self,
        send_match_notification,
    ):
        repository = RecordingRunRepository()
        connector = StubConnector(error=RuntimeError("unexpected"))

        with patch.object(runner, "CONNECTORS", (connector,)):
            with self.assertLogs(runner.LOGGER, level="INFO"):
                with self.assertRaisesRegex(RuntimeError, "unexpected"):
                    runner.run(repository)

        self.assertEqual(repository.ingestion_finish["status"], "failed")
        self.assertEqual(repository.source_finishes[0]["status"], "failed")
        send_match_notification.assert_not_called()
        # Una ingesta abortada no retira empresas.
        self.close_retired_companies.assert_not_called()

    @patch("job_radar.orchestration.runner.send_match_notification")
    def test_interruption_finalizes_run_before_propagating(
        self,
        send_match_notification,
    ):
        for interruption in (KeyboardInterrupt(), SystemExit(3)):
            with self.subTest(interruption=type(interruption).__name__):
                repository = RecordingRunRepository()
                connector = StubConnector(error=interruption)

                with patch.object(runner, "CONNECTORS", (connector,)):
                    with self.assertLogs(runner.LOGGER, level="INFO"):
                        with self.assertRaises(type(interruption)):
                            runner.run(repository)

                self.assertEqual(
                    repository.ingestion_finish["status"],
                    "failed",
                )
                self.assertEqual(repository.source_finishes, [])

        send_match_notification.assert_not_called()

    @patch("job_radar.orchestration.runner.send_match_notification")
    @patch("job_radar.orchestration.runner.save_jobs", return_value=(1, 0))
    def test_suspicious_snapshot_updates_without_closing_missing_jobs(
        self,
        save_jobs,
        _send_match_notification,
    ):
        repository = RecordingRunRepository([
            SourceSnapshot(100, "healthy", False),
        ])

        with patch.object(runner, "CONNECTORS", (StubConnector(),)):
            with self.assertLogs(runner.LOGGER, level="WARNING") as logs:
                exit_code = runner.run(repository)

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            repository.source_finishes[0]["snapshot_status"],
            "suspicious",
        )
        self.assertTrue(
            repository.source_finishes[0]["closure_suppressed"]
        )
        save_jobs.assert_called_once_with(
            ANY,
            "Example",
            close_missing=False,
            keep_ids=frozenset(),
        )
        self.assertIn("snapshot_closure_suppressed", "\n".join(logs.output))

    @patch("job_radar.orchestration.runner.send_match_notification")
    @patch("job_radar.orchestration.runner.save_jobs", return_value=(0, 0))
    def test_empty_snapshot_does_not_close_missing_jobs(
        self,
        save_jobs,
        _send_match_notification,
    ):
        repository = RecordingRunRepository([
            SourceSnapshot(100, "healthy", False),
        ])
        connector = StubConnector(jobs_count=0)

        with patch.object(runner, "CONNECTORS", (connector,)):
            with self.assertLogs(runner.LOGGER, level="WARNING"):
                exit_code = runner.run(repository)

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            repository.source_finishes[0]["snapshot_status"],
            "empty",
        )
        save_jobs.assert_called_once_with(
            [],
            "Example",
            close_missing=False,
            keep_ids=frozenset(),
        )

    @patch("job_radar.orchestration.runner.send_match_notification")
    @patch("job_radar.orchestration.runner.save_jobs", return_value=(0, 0))
    def test_incomplete_snapshot_keeps_failed_ids_open(
        self,
        save_jobs,
        _send_match_notification,
    ):
        connector = StubConnector()
        complete_fetch = connector.fetch
        failed_ids = {"listed-1", "listed-2"}
        connector.fetch = lambda: IncompleteSnapshot(
            complete_fetch(),
            failed_ids=failed_ids,
        )
        repository = RecordingRunRepository()

        with patch.object(runner, "CONNECTORS", (connector,)):
            with self.assertLogs(runner.LOGGER, level="WARNING") as logs:
                exit_code = runner.run(repository)

        self.assertEqual(exit_code, 0)
        save_jobs.assert_called_once_with(
            ANY,
            "Example",
            close_missing=True,
            keep_ids=frozenset(failed_ids),
        )
        finish = repository.source_finishes[0]
        self.assertEqual(finish["jobs_seen"], 3)
        self.assertEqual(finish["snapshot_status"], "healthy")
        self.assertIn("snapshot_incomplete", "\n".join(logs.output))

    @patch(
        "job_radar.orchestration.runner.send_match_notification",
        side_effect=RuntimeError("resend unavailable"),
    )
    @patch("job_radar.orchestration.runner.save_jobs", return_value=(0, 0))
    def test_notification_failure_marks_run_failed(
        self,
        _save_jobs,
        _send_match_notification,
    ):
        repository = RecordingRunRepository()

        with patch.object(runner, "CONNECTORS", (StubConnector(),)):
            with self.assertLogs(runner.LOGGER, level="INFO"):
                exit_code = runner.run(repository)

        self.assertEqual(exit_code, 1)
        self.assertEqual(repository.ingestion_finish["status"], "failed")

    @patch("job_radar.orchestration.runner.send_match_notification")
    @patch("job_radar.orchestration.runner.save_jobs", return_value=(1, 0))
    @patch(
        "job_radar.orchestration.runner.time.perf_counter",
        side_effect=[1.0, 1.05, 2.0, 2.2],
    )
    def test_source_run_finished_log_lines_are_stable(
        self,
        _perf_counter,
        _save_jobs,
        _send_match_notification,
    ):
        repository = RecordingRunRepository()
        failed = StubConnector(error=ValueError("broken snapshot"))
        successful = StubConnector(company="Second source")

        with patch.object(runner, "CONNECTORS", (failed, successful)):
            with self.assertLogs(runner.LOGGER, level="INFO") as logs:
                runner.run(repository)

        self.assertIn(
            "ERROR:job_radar.orchestration.runner:source_run_finished "
            "run_id=100 source_run_id=200 company='Example' source=stub "
            "jobs_seen=0 jobs_new=0 jobs_closed=0 matches=0 duration_ms=50 "
            "status=failed snapshot_status=unknown closure_suppressed=False "
            "error_type=ValueError error_message='broken snapshot'",
            logs.output,
        )
        self.assertIn(
            "INFO:job_radar.orchestration.runner:source_run_finished "
            "run_id=100 source_run_id=201 company='Second source' "
            "source=stub jobs_seen=1 jobs_new=1 jobs_closed=0 matches=1 "
            "duration_ms=200 status=success snapshot_status=healthy "
            "closure_suppressed=False",
            logs.output,
        )

    @patch("job_radar.orchestration.runner.send_match_notification")
    def test_required_catch_all_failure_continues_but_blocks_exit(
        self,
        send_match_notification,
    ):
        repository = RecordingRunRepository()
        connector = StubConnector(
            catch_all=True,
            error=RuntimeError("custom site changed"),
        )

        with patch.object(runner, "CONNECTORS", (connector,)):
            with self.assertLogs(runner.LOGGER, level="INFO"):
                exit_code = runner.run(repository)

        self.assertEqual(exit_code, 1)
        self.assertEqual(repository.ingestion_finish["status"], "partial")
        self.assertEqual(
            repository.source_finishes[0]["error_type"],
            "RuntimeError",
        )
        send_match_notification.assert_called_once_with(
            partial=True,
            source_health=ANY,
            new_since=ANY,
        )

    @patch(
        "job_radar.orchestration.runner.send_match_notification",
        side_effect=RuntimeError("resend unavailable"),
    )
    @patch("job_radar.orchestration.runner.save_jobs", return_value=(0, 0))
    def test_notification_failure_is_logged_with_error_details(
        self,
        _save_jobs,
        _send_match_notification,
    ):
        repository = RecordingRunRepository()

        with patch.object(runner, "CONNECTORS", (StubConnector(),)):
            with self.assertLogs(runner.LOGGER, level="INFO") as logs:
                runner.run(repository)

        self.assertIn(
            "ERROR:job_radar.orchestration.runner:notification_failed "
            "run_id=100 error_type=RuntimeError "
            "error_message='resend unavailable'",
            logs.output,
        )

    @patch("job_radar.orchestration.runner.send_match_notification")
    @patch("job_radar.orchestration.runner.save_jobs", return_value=(0, 0))
    def test_finalization_failure_is_logged_and_raised(
        self,
        _save_jobs,
        _send_match_notification,
    ):
        repository = RecordingRunRepository()

        def fail_finish(run_id, **values):
            raise ValueError(f"ingestion_run inexistente: {run_id}")

        repository.finish_ingestion_run = fail_finish

        with patch.object(runner, "CONNECTORS", (StubConnector(),)):
            with self.assertLogs(runner.LOGGER, level="INFO") as logs:
                with self.assertRaisesRegex(ValueError, "inexistente"):
                    runner.run(repository)

        output = "\n".join(logs.output)
        self.assertIn("ingestion_run_finalize_failed run_id=100", output)
        self.assertNotIn("ingestion_run_finished", output)

    @patch("job_radar.orchestration.runner.send_match_notification")
    def test_fatal_error_wins_over_finalization_failure(
        self,
        _send_match_notification,
    ):
        repository = RecordingRunRepository()

        def fail_finish(run_id, **values):
            raise ValueError("database unavailable")

        repository.finish_ingestion_run = fail_finish
        connector = StubConnector(error=RuntimeError("unexpected"))

        with patch.object(runner, "CONNECTORS", (connector,)):
            with self.assertLogs(runner.LOGGER, level="INFO"):
                with self.assertRaisesRegex(RuntimeError, "unexpected"):
                    runner.run(repository)



class RetiredCompaniesTests(unittest.TestCase):
    @patch("job_radar.orchestration.runner.send_match_notification")
    @patch("job_radar.orchestration.runner.save_jobs", return_value=(1, 0))
    def test_retires_companies_missing_from_registry_after_sources(
        self,
        save_jobs,
        send_match_notification,
    ):
        repository = RecordingRunRepository()
        connectors = (
            StubConnector(company="Alpha"),
            StubConnector(company="Beta", error=TimeoutError("down")),
        )
        calls = []
        save_jobs.side_effect = lambda *args, **kwargs: (
            calls.append("save_jobs") or (1, 0)
        )

        def close_retired(companies):
            calls.append(("close_retired", sorted(companies)))
            return 4

        with patch.object(runner, "CONNECTORS", connectors):
            with patch.object(
                runner,
                "close_retired_companies",
                side_effect=close_retired,
            ):
                with self.assertLogs(runner.LOGGER, level="INFO"):
                    exit_code = runner.run(repository)

        # Una fuente configurada que falla sigue en el registry: no se retira.
        self.assertEqual(
            calls,
            ["save_jobs", ("close_retired", ["Alpha", "Beta"])],
        )
        self.assertEqual(exit_code, 1)
        send_match_notification.assert_called_once()

    @patch("job_radar.orchestration.runner.send_match_notification")
    @patch("job_radar.orchestration.runner.save_jobs", return_value=(1, 0))
    def test_retirement_failure_fails_the_run(
        self,
        _save_jobs,
        send_match_notification,
    ):
        repository = RecordingRunRepository()

        with patch.object(runner, "CONNECTORS", (StubConnector(),)):
            with patch.object(
                runner,
                "close_retired_companies",
                side_effect=RuntimeError("database down"),
            ):
                with self.assertLogs(runner.LOGGER, level="INFO"):
                    with self.assertRaisesRegex(RuntimeError, "database"):
                        runner.run(repository)

        self.assertEqual(repository.ingestion_finish["status"], "failed")
        send_match_notification.assert_not_called()



if __name__ == "__main__":
    unittest.main()
