"""Connector health derived from recent source run history."""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import takewhile
from typing import Literal, Protocol

from job_radar.storage.runs import SourceRunRecord

HEALTH_WINDOW = 10
HEALTHY_SUCCESS_RATE = 0.90
UNHEALTHY_SUCCESS_RATE = 0.70
# Un snapshot vacío termina con status=success: no es un fallo de ejecución
# y no altera health_status. Repetido, suele indicar que la empresa cambió de
# ATS o de slug, así que se avisa como señal aparte (3 ejecuciones ≈ 6 días).
PERSISTENT_EMPTY_SNAPSHOT_RUNS = 3

HealthStatus = Literal["healthy", "degraded", "unhealthy", "unknown"]
SourceKey = tuple[str, str]


class ConnectorDefinition(Protocol):
    company: str
    source: str
    required: bool


@dataclass(frozen=True, slots=True)
class SourceHealth:
    company: str
    source: str
    required: bool
    health_status: HealthStatus
    recent_runs: int
    successful_runs: int
    success_rate: float | None
    consecutive_failures: int
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_error_type: str | None
    last_error_message: str | None
    consecutive_empty_snapshots: int = 0


def _leading_count(
    runs: Iterable[SourceRunRecord],
    predicate: Callable[[SourceRunRecord], bool],
) -> int:
    return sum(1 for _run in takewhile(predicate, runs))


def calculate_source_health(
    *,
    company: str,
    source: str,
    required: bool,
    runs: Sequence[SourceRunRecord],
) -> SourceHealth:
    """Calculate health from completed runs ordered newest first."""
    recent = tuple(runs[:HEALTH_WINDOW])
    recent_runs = len(recent)
    successful_runs = sum(run.status == "success" for run in recent)
    success_rate = successful_runs / recent_runs if recent_runs else None

    consecutive_failures = _leading_count(
        recent, lambda run: run.status == "failed"
    )
    consecutive_empty_snapshots = _leading_count(
        recent,
        lambda run: run.status == "success" and run.snapshot_status == "empty",
    )

    last_success = next((run for run in runs if run.status == "success"), None)
    last_failure = next((run for run in runs if run.status == "failed"), None)

    if not recent:
        health_status: HealthStatus = "unknown"
    elif consecutive_failures >= 2:
        health_status = "unhealthy"
    elif recent_runs == HEALTH_WINDOW and success_rate < UNHEALTHY_SUCCESS_RATE:
        health_status = "unhealthy"
    elif recent[0].status == "success" and success_rate >= HEALTHY_SUCCESS_RATE:
        health_status = "healthy"
    else:
        health_status = "degraded"

    return SourceHealth(
        company=company,
        source=source,
        required=required,
        health_status=health_status,
        recent_runs=recent_runs,
        successful_runs=successful_runs,
        success_rate=success_rate,
        consecutive_failures=consecutive_failures,
        last_success_at=last_success.finished_at if last_success else None,
        last_failure_at=last_failure.finished_at if last_failure else None,
        last_error_type=last_failure.error_type if last_failure else None,
        last_error_message=last_failure.error_message if last_failure else None,
        consecutive_empty_snapshots=consecutive_empty_snapshots,
    )


def build_source_health(
    connectors: Iterable[ConnectorDefinition],
    histories: Mapping[SourceKey, Sequence[SourceRunRecord]],
) -> tuple[SourceHealth, ...]:
    """Calculate health for every configured connector in registry order."""
    return tuple(
        calculate_source_health(
            company=connector.company,
            source=connector.source,
            required=connector.required,
            runs=histories.get((connector.company, connector.source), ()),
        )
        for connector in connectors
    )


def violates_required_slo(health: SourceHealth) -> bool:
    """Return whether a required source violates the operational SLO."""
    if not health.required:
        return False
    if health.health_status == "unhealthy":
        return True
    return (
        health.recent_runs == HEALTH_WINDOW
        and health.success_rate is not None
        and health.success_rate < HEALTHY_SUCCESS_RATE
    )


def has_persistent_empty_snapshot(health: SourceHealth) -> bool:
    """Return whether the latest runs all succeeded with an empty snapshot."""
    return health.consecutive_empty_snapshots >= PERSISTENT_EMPTY_SNAPSHOT_RUNS


def required_slo_breaches(
    health: Iterable[SourceHealth],
) -> tuple[SourceHealth, ...]:
    return tuple(source for source in health if violates_required_slo(source))
