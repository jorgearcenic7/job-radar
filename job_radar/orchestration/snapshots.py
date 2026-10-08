from dataclasses import dataclass
from statistics import median
from typing import Literal, Sequence

from job_radar.storage.runs import SourceSnapshot


SnapshotStatus = Literal["healthy", "suspicious", "empty"]

SNAPSHOT_HISTORY_LIMIT = 10
BASELINE_SAMPLE_SIZE = 5
MIN_BASELINE_JOBS = 20
MIN_ABSOLUTE_DROP = 10
MIN_HEALTHY_RATIO = 0.50
STABILIZATION_SNAPSHOT_COUNT = 3
STABILIZATION_RELATIVE_TOLERANCE = 0.10
STABILIZATION_ABSOLUTE_TOLERANCE = 2


@dataclass(frozen=True, slots=True)
class SnapshotAssessment:
    status: SnapshotStatus
    closure_suppressed: bool
    baseline_jobs: float | None = None
    current_ratio: float | None = None
    stabilized: bool = False


def _counts_are_similar(first: int, second: int) -> bool:
    tolerance = max(
        STABILIZATION_ABSOLUTE_TOLERANCE,
        round(max(first, second) * STABILIZATION_RELATIVE_TOLERANCE),
    )
    return abs(first - second) <= tolerance


def _is_stable_reduced_snapshot(
    jobs_seen: int,
    history: Sequence[SourceSnapshot],
) -> bool:
    required_previous = STABILIZATION_SNAPSHOT_COUNT - 1
    previous = history[:required_previous]

    if len(previous) < required_previous:
        return False

    return all(
        snapshot.snapshot_status == "suspicious"
        and snapshot.closure_suppressed
        and _counts_are_similar(snapshot.jobs_seen, jobs_seen)
        for snapshot in previous
    )


def evaluate_snapshot(
    jobs_seen: int,
    history: Sequence[SourceSnapshot],
) -> SnapshotAssessment:
    if jobs_seen < 0:
        raise ValueError("jobs_seen no puede ser negativo")

    if jobs_seen == 0:
        return SnapshotAssessment(
            status="empty",
            closure_suppressed=True,
        )

    trusted_counts = [
        snapshot.jobs_seen
        for snapshot in history
        if snapshot.jobs_seen > 0
        and snapshot.snapshot_status in {"healthy", "unknown"}
    ][:BASELINE_SAMPLE_SIZE]

    if not trusted_counts:
        return SnapshotAssessment(
            status="healthy",
            closure_suppressed=False,
        )

    baseline_jobs = float(median(trusted_counts))
    current_ratio = jobs_seen / baseline_jobs
    absolute_drop = baseline_jobs - jobs_seen
    suspicious = (
        baseline_jobs >= MIN_BASELINE_JOBS
        and absolute_drop >= MIN_ABSOLUTE_DROP
        and current_ratio < MIN_HEALTHY_RATIO
    )

    if not suspicious:
        return SnapshotAssessment(
            status="healthy",
            closure_suppressed=False,
            baseline_jobs=baseline_jobs,
            current_ratio=current_ratio,
        )

    if (
        history
        and history[0].snapshot_status == "healthy"
        and _counts_are_similar(history[0].jobs_seen, jobs_seen)
    ) or _is_stable_reduced_snapshot(jobs_seen, history):
        return SnapshotAssessment(
            status="healthy",
            closure_suppressed=False,
            baseline_jobs=baseline_jobs,
            current_ratio=current_ratio,
            stabilized=True,
        )

    return SnapshotAssessment(
        status="suspicious",
        closure_suppressed=True,
        baseline_jobs=baseline_jobs,
        current_ratio=current_ratio,
    )
