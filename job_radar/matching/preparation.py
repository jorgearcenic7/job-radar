from job_radar.domain import Job, PreparedJob

from .rules import classify, infer_countries


def prepare_job(job: Job) -> PreparedJob:
    """Classify a normalized job and derive its persistence metadata."""

    result = classify(job)
    countries = tuple(infer_countries(job.location))

    if result is None:
        return PreparedJob(
            job=job,
            countries=countries,
            selected=False,
            match_status=None,
            match_reason=None,
            match_rules_version=None,
            match_reason_codes=None,
        )

    return PreparedJob(
        job=job,
        countries=countries,
        selected=True,
        match_status=result.status,
        match_reason=f"{result.level}: {result.reason}",
        match_rules_version=result.rules_version,
        match_reason_codes=tuple(code.value for code in result.reason_codes),
    )
