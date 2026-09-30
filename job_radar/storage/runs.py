import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    jobs_seen: int
    snapshot_status: str
    closure_suppressed: bool


class RunRepository:
    """Persists pipeline- and source-level execution metrics."""

    def __init__(self, database_url: str | None = None):
        self.database_url = (
            database_url
            if database_url is not None
            else os.getenv("DATABASE_URL", "")
        )

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url, connect_timeout=10)

    def create_ingestion_run(self, sources_total: int) -> int:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ingestion_runs (sources_total)
                    VALUES (%s)
                    RETURNING id
                    """,
                    (sources_total,),
                )
                return cursor.fetchone()[0]

    def finish_ingestion_run(
        self,
        run_id: int,
        *,
        status: str,
        sources_succeeded: int,
        sources_failed: int,
        jobs_seen: int,
        jobs_new: int,
        jobs_closed: int,
        matches: int,
    ) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE ingestion_runs
                    SET finished_at = CURRENT_TIMESTAMP,
                        status = %s,
                        sources_succeeded = %s,
                        sources_failed = %s,
                        jobs_seen = %s,
                        jobs_new = %s,
                        jobs_closed = %s,
                        matches = %s
                    WHERE id = %s
                    """,
                    (
                        status,
                        sources_succeeded,
                        sources_failed,
                        jobs_seen,
                        jobs_new,
                        jobs_closed,
                        matches,
                        run_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError(
                        f"ingestion_run inexistente: {run_id}"
                    )

    def create_source_run(
        self,
        ingestion_run_id: int,
        *,
        company: str,
        source: str,
    ) -> int:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO source_runs (
                        ingestion_run_id,
                        company,
                        source
                    )
                    VALUES (%s, %s, %s)
                    RETURNING id
                    """,
                    (ingestion_run_id, company, source),
                )
                return cursor.fetchone()[0]

    def finish_source_run(
        self,
        source_run_id: int,
        *,
        status: str,
        jobs_seen: int,
        jobs_new: int,
        jobs_closed: int,
        matches: int,
        duration_ms: int,
        snapshot_status: str = "unknown",
        closure_suppressed: bool = False,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE source_runs
                    SET finished_at = CURRENT_TIMESTAMP,
                        status = %s,
                        jobs_seen = %s,
                        jobs_new = %s,
                        jobs_closed = %s,
                        matches = %s,
                        duration_ms = %s,
                        snapshot_status = %s,
                        closure_suppressed = %s,
                        error_type = %s,
                        error_message = %s
                    WHERE id = %s
                    """,
                    (
                        status,
                        jobs_seen,
                        jobs_new,
                        jobs_closed,
                        matches,
                        duration_ms,
                        snapshot_status,
                        closure_suppressed,
                        error_type,
                        error_message,
                        source_run_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError(
                        f"source_run inexistente: {source_run_id}"
                    )

    def get_source_snapshot_history(
        self,
        *,
        company: str,
        source: str,
        limit: int,
    ) -> list[SourceSnapshot]:
        if limit <= 0:
            return []

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        jobs_seen,
                        snapshot_status,
                        closure_suppressed
                    FROM source_runs
                    WHERE company = %s
                      AND source = %s
                      AND status = 'success'
                      AND finished_at IS NOT NULL
                    ORDER BY started_at DESC, id DESC
                    LIMIT %s
                    """,
                    (company, source, limit),
                )
                return [
                    SourceSnapshot(
                        jobs_seen=row[0],
                        snapshot_status=row[1],
                        closure_suppressed=row[2],
                    )
                    for row in cursor.fetchall()
                ]
