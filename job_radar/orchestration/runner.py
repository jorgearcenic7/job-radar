import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from urllib.error import HTTPError, URLError

from job_radar.connectors import CONNECTORS, Connector, IncompleteSnapshot
from job_radar.matching import prepare_job
from job_radar.notifications import send_match_notification
from job_radar.observability import HEALTH_WINDOW, build_source_health
from job_radar.storage import (
    RunRepository,
    close_retired_companies,
    save_jobs,
)

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
    # IDs listados cuyo detalle falló: siguen publicados, así que cuentan
    # para el snapshot y no se cierran.
    kept_ids = (
        jobs.failed_ids
        if isinstance(jobs, IncompleteSnapshot)
        else frozenset()
    )
    metrics.jobs_seen = len(jobs) + len(kept_ids)
    prepared_jobs = []

    for job in jobs:
        prepared = prepare_job(job)
        prepared_jobs.append(prepared)
        metrics.matches += int(prepared.selected)

    history = repository.get_source_snapshot_history(
        company=connector.company,
        source=connector.source,
        limit=SNAPSHOT_HISTORY_LIMIT,
    )
    assessment = evaluate_snapshot(metrics.jobs_seen, history)

    if kept_ids:
        LOGGER.warning(
            "snapshot_incomplete company=%r source=%s jobs_seen=%s "
            "detail_failures=%s",
            connector.company,
            connector.source,
            metrics.jobs_seen,
            len(kept_ids),
        )

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
        prepared_jobs,
        connector.company,
        close_missing=not assessment.closure_suppressed,
        keep_ids=kept_ids,
    )


def _duration_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))


def _can_continue(connector: Connector, error: Exception) -> bool:
    # El protocolo Connector no declara catch_all ni required: sin ellos, la
    # fuente es required y solo continúa ante EXPECTED_CONNECTOR_ERRORS.
    return bool(
        getattr(connector, "catch_all", False)
        or isinstance(error, EXPECTED_CONNECTOR_ERRORS)
    )


def _finish_source_run(
    repository: RunRepository,
    *,
    run_id: int,
    source_run_id: int,
    connector: Connector,
    metrics: SourceMetrics,
    started_at: float,
    error: Exception | None = None,
) -> None:
    duration_ms = _duration_ms(started_at)
    status = "success" if error is None else "failed"
    error_details = (
        {}
        if error is None
        else {"error_type": type(error).__name__, "error_message": str(error)}
    )
    repository.finish_source_run(
        source_run_id,
        status=status,
        jobs_seen=metrics.jobs_seen,
        jobs_new=metrics.jobs_new,
        jobs_closed=metrics.jobs_closed,
        matches=metrics.matches,
        duration_ms=duration_ms,
        snapshot_status=metrics.snapshot_status,
        closure_suppressed=metrics.closure_suppressed,
        **error_details,
    )

    message = (
        "source_run_finished run_id=%s source_run_id=%s "
        "company=%r source=%s jobs_seen=%s jobs_new=%s "
        "jobs_closed=%s matches=%s duration_ms=%s "
        "status=%s snapshot_status=%s closure_suppressed=%s"
    )
    arguments = [
        run_id,
        source_run_id,
        connector.company,
        connector.source,
        metrics.jobs_seen,
        metrics.jobs_new,
        metrics.jobs_closed,
        metrics.matches,
        duration_ms,
        status,
        metrics.snapshot_status,
        metrics.closure_suppressed,
    ]

    if error is None:
        LOGGER.info(message, *arguments)
    else:
        LOGGER.error(
            message + " error_type=%s error_message=%r",
            *arguments,
            error_details["error_type"],
            error_details["error_message"],
        )


def _notify_matches(
    repository: RunRepository,
    *,
    run_id: int,
    partial_run: bool,
    new_since: datetime,
) -> bool:
    """Envía el correo con la salud de las fuentes; False si falla."""
    try:
        histories = repository.get_recent_source_runs(limit=HEALTH_WINDOW)
        send_match_notification(
            partial=partial_run,
            source_health=build_source_health(CONNECTORS, histories),
            new_since=new_since,
        )
    except Exception as error:
        LOGGER.error(
            "notification_failed run_id=%s error_type=%s "
            "error_message=%r",
            run_id,
            type(error).__name__,
            str(error),
        )
        return False

    return True


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def run(run_repository: RunRepository | None = None) -> int:
    _configure_logging()
    repository = run_repository or RunRepository()
    run_metrics = RunMetrics()
    run_started_at = datetime.now(timezone.utc)
    run_id = repository.create_ingestion_run(len(CONNECTORS))
    blocking_failure = False
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
            finish_source_run = partial(
                _finish_source_run,
                repository,
                run_id=run_id,
                source_run_id=source_run_id,
                connector=connector,
                metrics=source_metrics,
                started_at=started_at,
            )

            try:
                process_connector(connector, source_metrics, repository)
            except Exception as error:
                run_metrics.sources_failed += 1
                run_metrics.add(source_metrics)
                finish_source_run(error=error)

                if not _can_continue(connector, error):
                    raise

                if getattr(connector, "required", True):
                    blocking_failure = True
            else:
                run_metrics.sources_succeeded += 1
                run_metrics.add(source_metrics)
                finish_source_run()

        close_retired_companies(
            connector.company for connector in CONNECTORS
        )

        notified = _notify_matches(
            repository,
            run_id=run_id,
            partial_run=blocking_failure,
            new_since=run_started_at,
        )

        if not notified:
            blocking_failure = True
            final_status = "failed"
        elif run_metrics.sources_failed:
            final_status = "partial"
        else:
            final_status = "success"
    except (KeyboardInterrupt, SystemExit, Exception) as error:
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
        except Exception as error:
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
