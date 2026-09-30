import logging
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError

from job_radar.connectors import CONNECTORS, Connector
from job_radar.matching import classify
from job_radar.notifications import send_match_notification
from job_radar.storage import RunRepository, save_jobs

from .snapshots import SNAPSHOT_HISTORY_LIMIT, evaluate_snapshot


LOGGER = logging.getLogger(__name__)

EXPECTED_CONNECTOR_ERRORS = (
    HTTPError,
    URLError,
    TimeoutError,
    KeyError,
    ValueError,
    TypeError,
)


@dataclass
class SourceMetrics:
    jobs_seen: int = 0
    jobs_new: int = 0
    jobs_closed: int = 0
    matches: int = 0
    snapshot_status: str = "unknown"
    closure_suppressed: bool = False


@dataclass
class RunMetrics:
    sources_succeeded: int = 0
    sources_failed: int = 0
    jobs_seen: int = 0
    jobs_new: int = 0
    jobs_closed: int = 0
    matches: int = 0

    def add(self, source: SourceMetrics) -> None:
        self.jobs_seen += source.jobs_seen
        self.jobs_new += source.jobs_new
        self.jobs_closed += source.jobs_closed
        self.matches += source.matches


def process_connector(
    connector: Connector,
    metrics: SourceMetrics,
    repository: RunRepository,
) -> None:
    jobs = connector.fetch()
    metrics.jobs_seen = len(jobs)
    metrics.matches = sum(classify(job) is not None for job in jobs)
    history = repository.get_source_snapshot_history(
        company=connector.company,
        source=connector.source,
        limit=SNAPSHOT_HISTORY_LIMIT,
    )
    assessment = evaluate_snapshot(metrics.jobs_seen, history)
    metrics.snapshot_status = assessment.status
    metrics.closure_suppressed = assessment.closure_suppressed

    if assessment.closure_suppressed:
        LOGGER.warning(
            "snapshot_closure_suppressed company=%r source=%s "
            "snapshot_status=%s jobs_seen=%s baseline_jobs=%s "
            "current_ratio=%s",
            connector.company,
            connector.source,
            assessment.status,
            metrics.jobs_seen,
            assessment.baseline_jobs,
            assessment.current_ratio,
        )

    metrics.jobs_new, metrics.jobs_closed = save_jobs(
        jobs,
        connector.company,
        close_missing=not assessment.closure_suppressed,
    )


def _duration_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))


def _can_continue(connector: Connector, error: BaseException) -> bool:
    return bool(
        isinstance(error, Exception)
        and (
            getattr(connector, "catch_all", False)
            or isinstance(error, EXPECTED_CONNECTOR_ERRORS)
        )
    )


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def run(run_repository: RunRepository | None = None) -> int:
    _configure_logging()
    repository = run_repository or RunRepository()
    run_metrics = RunMetrics()
    run_id = repository.create_ingestion_run(len(CONNECTORS))
    blocking_failure = False
    notification_failed = False
    final_status = "running"
    fatal_error: BaseException | None = None
    finalized = False

    LOGGER.info(
        "ingestion_run_started run_id=%s sources_total=%s status=running",
        run_id,
        len(CONNECTORS),
    )

    try:
        for connector in CONNECTORS:
            source_run_id = repository.create_source_run(
                run_id,
                company=connector.company,
                source=connector.source,
            )
            started_at = time.perf_counter()
            source_metrics = SourceMetrics()

            try:
                process_connector(connector, source_metrics, repository)
            except Exception as error:
                duration_ms = _duration_ms(started_at)
                run_metrics.sources_failed += 1
                run_metrics.add(source_metrics)
                repository.finish_source_run(
                    source_run_id,
                    status="failed",
                    jobs_seen=source_metrics.jobs_seen,
                    jobs_new=source_metrics.jobs_new,
                    jobs_closed=source_metrics.jobs_closed,
                    matches=source_metrics.matches,
                    duration_ms=duration_ms,
                    snapshot_status=source_metrics.snapshot_status,
                    closure_suppressed=(
                        source_metrics.closure_suppressed
                    ),
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                LOGGER.error(
                    "source_run_finished run_id=%s source_run_id=%s "
                    "company=%r source=%s jobs_seen=%s jobs_new=%s "
                    "jobs_closed=%s matches=%s duration_ms=%s "
                    "status=failed snapshot_status=%s "
                    "closure_suppressed=%s error_type=%s error_message=%r",
                    run_id,
                    source_run_id,
                    connector.company,
                    connector.source,
                    source_metrics.jobs_seen,
                    source_metrics.jobs_new,
                    source_metrics.jobs_closed,
                    source_metrics.matches,
                    duration_ms,
                    source_metrics.snapshot_status,
                    source_metrics.closure_suppressed,
                    type(error).__name__,
                    str(error),
                )

                if not _can_continue(connector, error):
                    raise

                if getattr(connector, "required", True):
                    blocking_failure = True
            else:
                duration_ms = _duration_ms(started_at)
                run_metrics.sources_succeeded += 1
                run_metrics.add(source_metrics)
                repository.finish_source_run(
                    source_run_id,
                    status="success",
                    jobs_seen=source_metrics.jobs_seen,
                    jobs_new=source_metrics.jobs_new,
                    jobs_closed=source_metrics.jobs_closed,
                    matches=source_metrics.matches,
                    duration_ms=duration_ms,
                    snapshot_status=source_metrics.snapshot_status,
                    closure_suppressed=(
                        source_metrics.closure_suppressed
                    ),
                )
                LOGGER.info(
                    "source_run_finished run_id=%s source_run_id=%s "
                    "company=%r source=%s jobs_seen=%s jobs_new=%s "
                    "jobs_closed=%s matches=%s duration_ms=%s "
                    "status=success snapshot_status=%s "
                    "closure_suppressed=%s",
                    run_id,
                    source_run_id,
                    connector.company,
                    connector.source,
                    source_metrics.jobs_seen,
                    source_metrics.jobs_new,
                    source_metrics.jobs_closed,
                    source_metrics.matches,
                    duration_ms,
                    source_metrics.snapshot_status,
                    source_metrics.closure_suppressed,
                )

        try:
            send_match_notification(partial=blocking_failure)
        except Exception as error:
            blocking_failure = True
            notification_failed = True
            LOGGER.error(
                "notification_failed run_id=%s error_type=%s "
                "error_message=%r",
                run_id,
                type(error).__name__,
                str(error),
            )

        if notification_failed:
            final_status = "failed"
        elif run_metrics.sources_failed:
            final_status = "partial"
        else:
            final_status = "success"
    except BaseException as error:
        fatal_error = error
        final_status = "failed"
    finally:
        try:
            repository.finish_ingestion_run(
                run_id,
                status=final_status,
                sources_succeeded=run_metrics.sources_succeeded,
                sources_failed=run_metrics.sources_failed,
                jobs_seen=run_metrics.jobs_seen,
                jobs_new=run_metrics.jobs_new,
                jobs_closed=run_metrics.jobs_closed,
                matches=run_metrics.matches,
            )
            finalized = True
        except BaseException as error:
            LOGGER.exception(
                "ingestion_run_finalize_failed run_id=%s status=failed",
                run_id,
            )
            if fatal_error is None:
                fatal_error = error

    if finalized:
        LOGGER.info(
            "ingestion_run_finished run_id=%s status=%s "
            "sources_total=%s sources_succeeded=%s sources_failed=%s "
            "jobs_seen=%s jobs_new=%s jobs_closed=%s matches=%s",
            run_id,
            final_status,
            len(CONNECTORS),
            run_metrics.sources_succeeded,
            run_metrics.sources_failed,
            run_metrics.jobs_seen,
            run_metrics.jobs_new,
            run_metrics.jobs_closed,
            run_metrics.matches,
        )

    if fatal_error is not None:
        raise fatal_error

    return 1 if blocking_failure else 0
