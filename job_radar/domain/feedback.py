from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Relevance(StrEnum):
    """Manual judgement of a recommendation shown by Job Radar."""

    RELEVANT = "relevant"
    NOT_RELEVANT = "not_relevant"


@dataclass(frozen=True, slots=True)
class JobFeedback:
    """Relevance label plus the matching context it was given under."""

    source: str
    source_job_id: str
    match_rules_version: int
    relevance: Relevance
    match_status: str
    match_reason_codes: tuple[str, ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RecordedFeedback:
    feedback: JobFeedback
    company: str
    title: str
    previous_relevance: Relevance | None


@dataclass(frozen=True, slots=True)
class PendingFeedback:
    """Active recommendation without a label for its current rules version."""

    source: str
    source_job_id: str
    company: str
    title: str
    url: str
    match_status: str
    match_rules_version: int


@dataclass(frozen=True, slots=True)
class RelevanceStats:
    key: str
    labeled: int
    relevant: int

    @property
    def not_relevant(self) -> int:
        return self.labeled - self.relevant

    @property
    def relevance_rate(self) -> float | None:
        if self.labeled == 0:
            return None
        return self.relevant / self.labeled


@dataclass(frozen=True, slots=True)
class FeedbackReport:
    """Precision of shown recommendations; recall is not measured."""

    overall: RelevanceStats
    by_rules_version: tuple[RelevanceStats, ...]
    by_match_status: tuple[RelevanceStats, ...]
    by_reason_code: tuple[RelevanceStats, ...]
