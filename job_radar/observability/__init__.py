"""Operational health models derived from persisted run history."""

from .health import (
    HEALTH_WINDOW,
    PERSISTENT_EMPTY_SNAPSHOT_RUNS,
    SourceHealth,
    build_source_health,
    calculate_source_health,
    has_persistent_empty_snapshot,
    required_slo_breaches,
    violates_required_slo,
)

__all__ = [
    "HEALTH_WINDOW",
    "PERSISTENT_EMPTY_SNAPSHOT_RUNS",
    "SourceHealth",
    "build_source_health",
    "calculate_source_health",
    "has_persistent_empty_snapshot",
    "required_slo_breaches",
    "violates_required_slo",
]
