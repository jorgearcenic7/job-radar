import os

from job_radar.domain import (
    FeedbackReport,
    JobFeedback,
    PendingFeedback,
    RecordedFeedback,
    Relevance,
    RelevanceStats,
)


class FeedbackError(Exception):
    """Base error for feedback that cannot be recorded."""


class FeedbackJobNotFoundError(FeedbackError):
    """Raised when the exact (source, source_job_id) does not exist."""


class FeedbackJobNotSelectedError(FeedbackError):
    """Raised when the job was not recommended by the matching rules."""


class MissingMatchMetadataError(FeedbackError):
    """Raised when a selected job lacks versioned matching metadata."""


_RELEVANT_COUNT = "COUNT(*) FILTER (WHERE relevance = 'relevant')"
_VERSION_FILTER = """
    WHERE (
        %(rules_version)s::INTEGER IS NULL
        OR match_rules_version = %(rules_version)s::INTEGER
    )
"""


class FeedbackRepository:
    """Stores manual relevance labels for recommended jobs."""

    def __init__(self, database_url: str | None = None):
        self.database_url = (
            database_url
            if database_url is not None
            else os.getenv("DATABASE_URL", "")
        )

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url, connect_timeout=10)

    def record(
        self,
        *,
        source: str,
        source_job_id: str,
        relevance: Relevance,
    ) -> RecordedFeedback:
        """Upsert a label for the job's current matching rules version."""
        relevance = Relevance(relevance)
        identity = f"source={source!r} job_id={source_job_id!r}"

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        company,
                        title,
                        selected,
                        match_status,
                        match_rules_version,
                        match_reason_codes
                    FROM jobs
                    WHERE source = %s
                      AND source_job_id = %s
                    """,
                    (source, source_job_id),
                )
                row = cursor.fetchone()

                if row is None:
                    raise FeedbackJobNotFoundError(
                        f"oferta inexistente: {identity}"
                    )

                company, title, selected, status, version, codes = row

                if not selected:
                    raise FeedbackJobNotSelectedError(
                        f"la oferta no es una recomendación: {identity}"
                    )

                if version is None or status is None or codes is None:
                    raise MissingMatchMetadataError(
                        "la oferta no tiene metadata de matching "
                        f"versionada; espera a que se reingeste: {identity}"
                    )

                cursor.execute(
                    """
                    SELECT relevance
                    FROM job_feedback
                    WHERE source = %s
                      AND source_job_id = %s
                      AND match_rules_version = %s
                    """,
                    (source, source_job_id, version),
                )
                previous = cursor.fetchone()

                cursor.execute(
                    """
                    INSERT INTO job_feedback (
                        source,
                        source_job_id,
                        match_rules_version,
                        relevance,
                        match_status,
                        match_reason_codes
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (
                        source,
                        source_job_id,
                        match_rules_version
                    ) DO UPDATE SET
                        relevance = EXCLUDED.relevance,
                        match_status = EXCLUDED.match_status,
                        match_reason_codes = EXCLUDED.match_reason_codes,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING created_at, updated_at
                    """,
                    (
                        source,
                        source_job_id,
                        version,
                        relevance.value,
                        status,
                        list(codes),
                    ),
                )
                created_at, updated_at = cursor.fetchone()

        return RecordedFeedback(
            feedback=JobFeedback(
                source=source,
                source_job_id=source_job_id,
                match_rules_version=version,
                relevance=relevance,
                match_status=status,
                match_reason_codes=tuple(codes),
                created_at=created_at,
                updated_at=updated_at,
            ),
            company=company,
            title=title,
            previous_relevance=(
                Relevance(previous[0]) if previous is not None else None
            ),
        )

    def get_pending(self, *, limit: int = 20) -> list[PendingFeedback]:
        """Return active recommendations not yet labeled for their version."""
        if limit <= 0:
            return []

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        job.source,
                        job.source_job_id,
                        job.company,
                        job.title,
                        job.url,
                        job.match_status,
                        job.match_rules_version
                    FROM jobs AS job
                    WHERE job.active = TRUE
                      AND job.selected = TRUE
                      AND job.match_status IS NOT NULL
                      AND job.match_rules_version IS NOT NULL
                      AND job.match_reason_codes IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1
                          FROM job_feedback AS feedback
                          WHERE feedback.source = job.source
                            AND feedback.source_job_id = job.source_job_id
                            AND feedback.match_rules_version
                                = job.match_rules_version
                      )
                    ORDER BY
                        job.first_seen_at DESC,
                        job.source,
                        job.source_job_id
                    LIMIT %s
                    """,
                    (limit,),
                )
                return [
                    PendingFeedback(
                        source=row[0],
                        source_job_id=row[1],
                        company=row[2],
                        title=row[3],
                        url=row[4],
                        match_status=row[5],
                        match_rules_version=row[6],
                    )
                    for row in cursor.fetchall()
                ]

    def get_report(
        self,
        *,
        rules_version: int | None = None,
    ) -> FeedbackReport:
        """Aggregate labels overall and by version, status and reason code."""
        parameters = {"rules_version": rules_version}

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT 'overall', COUNT(*), {_RELEVANT_COUNT}
                    FROM job_feedback
                    {_VERSION_FILTER}
                    """,
                    parameters,
                )
                overall = _stats(cursor.fetchall())[0]

                cursor.execute(
                    f"""
                    SELECT
                        match_rules_version::TEXT,
                        COUNT(*),
                        {_RELEVANT_COUNT}
                    FROM job_feedback
                    {_VERSION_FILTER}
                    GROUP BY match_rules_version
                    ORDER BY match_rules_version
                    """,
                    parameters,
                )
                by_rules_version = _stats(cursor.fetchall())

                cursor.execute(
                    f"""
                    SELECT match_status, COUNT(*), {_RELEVANT_COUNT}
                    FROM job_feedback
                    {_VERSION_FILTER}
                    GROUP BY match_status
                    ORDER BY match_status
                    """,
                    parameters,
                )
                by_match_status = _stats(cursor.fetchall())

                cursor.execute(
                    f"""
                    SELECT reason_code, COUNT(*), {_RELEVANT_COUNT}
                    FROM job_feedback
                    CROSS JOIN LATERAL unnest(match_reason_codes)
                        AS reason_code
                    {_VERSION_FILTER}
                    GROUP BY reason_code
                    ORDER BY
                        COUNT(*) - {_RELEVANT_COUNT} DESC,
                        COUNT(*) DESC,
                        reason_code
                    """,
                    parameters,
                )
                by_reason_code = _stats(cursor.fetchall())

        return FeedbackReport(
            overall=overall,
            by_rules_version=by_rules_version,
            by_match_status=by_match_status,
            by_reason_code=by_reason_code,
        )


def _stats(rows) -> tuple[RelevanceStats, ...]:
    return tuple(
        RelevanceStats(key=row[0], labeled=row[1], relevant=row[2])
        for row in rows
    )
