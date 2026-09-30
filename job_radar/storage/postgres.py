import logging
import os
from typing import Any

from job_radar.domain import Job
from job_radar.matching import classify, infer_countries


LOGGER = logging.getLogger(__name__)


def get_active_matches() -> list[dict[str, Any]]:
    import psycopg

    with psycopg.connect(
        os.getenv("DATABASE_URL", ""),
        connect_timeout=10,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    company,
                    title,
                    location,
                    url,
                    salary_text,
                    experience_text,
                    match_status
                FROM jobs
                WHERE active = TRUE
                  AND selected = TRUE
                ORDER BY
                    CASE
                        WHEN match_status = 'Buena coincidencia' THEN 0
                        ELSE 1
                    END,
                    company,
                    title,
                    source_job_id
                """
            )

            fields = [column.name for column in cursor.description]

            return [dict(zip(fields, row)) for row in cursor.fetchall()]


def save_jobs(
    jobs: list[Job],
    company: str,
    *,
    close_missing: bool = True,
) -> tuple[int, int]:
    import psycopg

    if not jobs:
        LOGGER.warning(
            "empty_snapshot_protected company=%r jobs_closed=0",
            company,
        )
        return 0, 0

    sources = {job.source for job in jobs}

    if len(sources) != 1:
        raise ValueError(
            f"{company}: el snapshot contiene varias fuentes"
        )

    source = next(iter(sources))
    current_ids = {job.source_job_id for job in jobs}

    sql = """
        INSERT INTO jobs (
            source,
            source_job_id,
            company,
            title,
            location,
            countries,
            url,
            description,
            salary_text,
            experience_text,
            selected,
            match_status,
            match_reason,
            active,
            closed_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s,
            TRUE, NULL
        )
        ON CONFLICT (source, source_job_id) DO UPDATE SET
            company = EXCLUDED.company,
            title = EXCLUDED.title,
            location = EXCLUDED.location,
            countries = EXCLUDED.countries,
            url = EXCLUDED.url,
            description = EXCLUDED.description,
            salary_text = EXCLUDED.salary_text,
            experience_text = EXCLUDED.experience_text,
            selected = EXCLUDED.selected,
            match_status = EXCLUDED.match_status,
            match_reason = EXCLUDED.match_reason,
            active = TRUE,
            closed_at = NULL,
            last_seen_at = CURRENT_TIMESTAMP
    """

    with psycopg.connect(
        os.getenv("DATABASE_URL", ""),
        connect_timeout=10,
    ) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT source_job_id
                FROM jobs
                WHERE company = %s
                  AND source = %s
                """,
                (company, source),
            )

            existing_ids = {
                row[0]
                for row in cursor.fetchall()
            }

            new_count = len(current_ids - existing_ids)

            for job in jobs:
                result = classify(job)
                status, level, reason = result or (None, None, None)

                cursor.execute(
                    sql,
                    (
                        job.source,
                        job.source_job_id,
                        job.company,
                        job.title,
                        job.location,
                        infer_countries(job.location),
                        job.url,
                        job.description,
                        job.salary_text,
                        job.experience_text,
                        result is not None,
                        status,
                        f"{level}: {reason}" if result else None,
                    ),
                )

            closed_count = 0

            if close_missing:
                cursor.execute(
                    """
                    UPDATE jobs
                    SET active = FALSE,
                        closed_at = CURRENT_TIMESTAMP
                    WHERE company = %s
                      AND source = %s
                      AND active = TRUE
                      AND NOT (source_job_id = ANY(%s))
                    """,
                    (
                        company,
                        source,
                        list(current_ids),
                    ),
                )

                closed_count = cursor.rowcount

    return new_count, closed_count
