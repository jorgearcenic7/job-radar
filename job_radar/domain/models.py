from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(slots=True)
class Job:
    """Normalized job posting produced by connectors."""

    source: str
    source_job_id: str
    company: str
    title: str
    location: str | None
    url: str
    description: str
    salary_text: str | None
    experience_text: str | None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Job":
        return cls(
            source=value["source"],
            source_job_id=value["source_job_id"],
            company=value["company"],
            title=value["title"],
            location=value["location"],
            url=value["url"],
            description=value["description"],
            salary_text=value["salary_text"],
            experience_text=value["experience_text"],
        )


@dataclass(frozen=True, slots=True)
class PreparedJob:
    """Normalized job plus values derived before persistence."""

    job: Job
    countries: tuple[str, ...]
    selected: bool
    match_status: str | None
    match_reason: str | None
    match_rules_version: int | None
    match_reason_codes: tuple[str, ...] | None
