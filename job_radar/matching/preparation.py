from job_radar.domain import Job, PreparedJob

from .rules import classify, infer_countries


def prepare_job(job: Job) -> PreparedJob:
    """Classify a normalized job and derive its persistence metadata."""

    result = classify(job)

    return PreparedJob(
        job=job,
        countries=tuple(infer_countries(job.location)),
        selected=result is not None,
        match_status=result.status if result else None,
        match_reason=(
            f"{result.level}: {result.reason}"
            if result
            else None
        ),
        match_rules_version=result.rules_version if result else None,
        match_reason_codes=(
            tuple(code.value for code in result.reason_codes)
            if result
            else None
        ),
    )
