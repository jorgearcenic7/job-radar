import logging
import os
from collections.abc import Collection
from typing import Any

from job_radar.domain import PreparedJob


LOGGER = logging.getLogger(__name__)

# Una oferta activa que lleva este tiempo sin verse respecto a la última
# ingesta pertenece a una fuente que falla en varias ejecuciones seguidas
# (cron cada 2 días): su enlace ya no está verificado. Sigue activa en el
# lifecycle, pero no se recomienda. Medirlo contra la última ingesta, y no
# contra el reloj, evita vaciar el listado si se detiene todo el pipeline.
# Mantener sincronizado con STALE_AFTER_DAYS en web/src/app/page.tsx.
STALE_AFTER_DAYS = 7


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
                    match_status,
                    first_seen_at,
                    last_seen_at >= (
                        SELECT MAX(last_seen_at) FROM jobs WHERE active
                    ) - make_interval(days => %s) AS fresh
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
                """,
                (STALE_AFTER_DAYS,),
            )

            fields = [column.name for column in cursor.description]
            rows = [dict(zip(fields, row)) for row in cursor.fetchall()]

    matches = [row for row in rows if row.pop("fresh")]
    hidden = len(rows) - len(matches)

    if hidden:
        LOGGER.warning(
            "stale_matches_hidden count=%s stale_after_days=%s",
            hidden,
            STALE_AFTER_DAYS,
        )

    return matches


def save_jobs(
    prepared_jobs: list[PreparedJob],
    company: str,
    *,
    close_missing: bool = True,
    keep_ids: Collection[str] = (),
) -> tuple[int, int]:
    import psycopg

    if not prepared_jobs:
        LOGGER.warning(
            "empty_snapshot_protected company=%r jobs_closed=0",
            company,
        )
        return 0, 0

    sources = {prepared.job.source for prepared in prepared_jobs}

    if len(sources) != 1:
        raise ValueError(
            f"{company}: el snapshot contiene varias fuentes"
        )

    source = next(iter(sources))
    current_ids = {
        prepared.job.source_job_id
        for prepared in prepared_jobs
    }

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
            match_rules_version,
            match_reason_codes,
            active,
            closed_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s,
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
            match_rules_version = EXCLUDED.match_rules_version,
            match_reason_codes = EXCLUDED.match_reason_codes,
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

            cursor.executemany(
                sql,
                [
                    (
                        prepared.job.source,
                        prepared.job.source_job_id,
                        prepared.job.company,
                        prepared.job.title,
                        prepared.job.location,
                        list(prepared.countries),
                        prepared.job.url,
                        prepared.job.description,
                        prepared.job.salary_text,
                        prepared.job.experience_text,
                        prepared.selected,
                        prepared.match_status,
                        prepared.match_reason,
                        prepared.match_rules_version,
                        (
                            list(prepared.match_reason_codes)
                            if prepared.match_reason_codes is not None
                            else None
                        ),
                    )
                    for prepared in prepared_jobs
                ],
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
                        list(current_ids | set(keep_ids)),
                    ),
                )

                closed_count = cursor.rowcount

    return new_count, closed_count
