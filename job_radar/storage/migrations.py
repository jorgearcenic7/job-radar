import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .connection import connect, resolve_database_url


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MIGRATIONS_DIRECTORY = REPOSITORY_ROOT / "sql"
MIGRATION_PATTERN = re.compile(r"^(?P<version>[0-9]+)_[a-z0-9_]+\.sql$")
ADVISORY_LOCK_KEY = (1_245_610_066, 1_297_303_890)

CREATE_TRACKING_TABLE = """
    CREATE TABLE schema_migrations (
        version BIGINT PRIMARY KEY,
        filename TEXT NOT NULL UNIQUE,
        checksum TEXT NOT NULL
            CHECK (checksum ~ '^[0-9a-f]{64}$'),
        applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
"""
INSERT_APPLIED_MIGRATION = """
    INSERT INTO schema_migrations (
        version,
        filename,
        checksum
    )
    VALUES (%s, %s, %s)
"""
UNTRACKED_SCHEMA_MESSAGE = (
    "schema con tablas y sin historial; verifica si necesita baseline."
)


class MigrationError(Exception):
    """Base error for migration validation and execution."""


class MigrationDefinitionError(MigrationError):
    """Raised when local migration files do not form a valid sequence."""


class MigrationDriftError(MigrationError):
    """Raised when applied migration metadata differs from local files."""


class MigrationLockError(MigrationError):
    """Raised when another migration command holds the advisory lock."""


class UntrackedDatabaseError(MigrationError):
    """Raised when an existing schema has no migration history."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    version_label: str
    filename: str
    path: Path
    checksum: str
    sql: str


@dataclass(frozen=True, slots=True)
class AppliedMigration:
    version: int
    filename: str
    checksum: str
    applied_at: datetime


@dataclass(frozen=True, slots=True)
class MigrationStatus:
    migration: Migration
    applied: AppliedMigration | None


@dataclass(frozen=True, slots=True)
class DatabaseMigrationState:
    statuses: tuple[MigrationStatus, ...]
    tracking_exists: bool
    has_untracked_tables: bool


def discover_migrations(directory: Path) -> tuple[Migration, ...]:
    paths = sorted(directory.glob("*.sql"))

    if not paths:
        raise MigrationDefinitionError(
            f"no se encontraron migraciones SQL en {directory}"
        )

    migrations = []
    versions = set()

    for path in paths:
        match = MIGRATION_PATTERN.fullmatch(path.name)

        if match is None:
            raise MigrationDefinitionError(
                f"nombre de migración inválido: {path.name}"
            )

        version_label = match.group("version")
        version = int(version_label)

        if version in versions:
            raise MigrationDefinitionError(
                f"versión de migración duplicada: {version_label}"
            )

        versions.add(version)
        contents = path.read_bytes()

        try:
            sql = contents.decode("utf-8")
        except UnicodeDecodeError as error:
            raise MigrationDefinitionError(
                f"la migración no es UTF-8: {path.name}"
            ) from error

        migrations.append(
            Migration(
                version=version,
                version_label=version_label,
                filename=path.name,
                path=path,
                checksum=hashlib.sha256(contents).hexdigest(),
                sql=sql,
            )
        )

    return tuple(sorted(migrations, key=lambda migration: migration.version))


def _acquire_lock(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_try_advisory_lock(%s, %s)",
            ADVISORY_LOCK_KEY,
        )
        acquired = cursor.fetchone()[0]

    if not acquired:
        raise MigrationLockError(
            "otro proceso de migración mantiene el advisory lock"
        )


def _tracking_table_exists(connection) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT to_regclass('schema_migrations') IS NOT NULL"
        )
        return cursor.fetchone()[0]


def _has_user_tables(connection) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_class AS relation
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND relation.relkind IN ('r', 'p')
                  AND relation.relname <> 'schema_migrations'
            )
            """
        )
        return cursor.fetchone()[0]


def _create_tracking_table(connection) -> None:
    if _tracking_table_exists(connection):
        return

    with connection.cursor() as cursor:
        cursor.execute(CREATE_TRACKING_TABLE)


def _record_applied(cursor, migration: Migration) -> None:
    cursor.execute(
        INSERT_APPLIED_MIGRATION,
        (migration.version, migration.filename, migration.checksum),
    )


def _load_applied(connection) -> dict[int, AppliedMigration]:
    if not _tracking_table_exists(connection):
        return {}

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT version, filename, checksum, applied_at
            FROM schema_migrations
            ORDER BY version
            """
        )
        return {
            row[0]: AppliedMigration(
                version=row[0],
                filename=row[1],
                checksum=row[2],
                applied_at=row[3],
            )
            for row in cursor.fetchall()
        }


def _validate_history(
    migrations: tuple[Migration, ...],
    applied: dict[int, AppliedMigration],
) -> None:
    local_by_version = {
        migration.version: migration for migration in migrations
    }
    problems = []

    for version, record in applied.items():
        migration = local_by_version.get(version)

        if migration is None:
            problems.append(
                f"{record.filename}: registrada pero ausente en sql/"
            )
            continue

        if record.filename != migration.filename:
            problems.append(
                f"versión {migration.version_label}: filename registrado "
                f"{record.filename!r}, local {migration.filename!r}"
            )

        if record.checksum != migration.checksum:
            problems.append(
                f"{migration.filename}: checksum distinto al registrado"
            )

    if applied:
        highest_applied = max(applied)
        gaps = [
            migration.filename
            for migration in migrations
            if migration.version < highest_applied
            and migration.version not in applied
        ]

        if gaps:
            problems.append(
                "historial no secuencial; faltan: " + ", ".join(gaps)
            )

    if problems:
        raise MigrationDriftError("; ".join(problems))


def _connect(database_url: str):
    # Cada migración gestiona su propia transacción explícita.
    return connect(database_url, autocommit=True)


def inspect_database(
    database_url: str,
    migrations_directory: Path = DEFAULT_MIGRATIONS_DIRECTORY,
) -> DatabaseMigrationState:
    migrations = discover_migrations(migrations_directory)

    with _connect(database_url) as connection:
        _acquire_lock(connection)
        tracking_exists = _tracking_table_exists(connection)
        applied = _load_applied(connection)
        _validate_history(migrations, applied)
        statuses = tuple(
            MigrationStatus(
                migration=migration,
                applied=applied.get(migration.version),
            )
            for migration in migrations
        )
        return DatabaseMigrationState(
            statuses=statuses,
            tracking_exists=tracking_exists,
            has_untracked_tables=(
                not tracking_exists and _has_user_tables(connection)
            ),
        )


def apply_pending(
    database_url: str,
    migrations_directory: Path = DEFAULT_MIGRATIONS_DIRECTORY,
) -> tuple[Migration, ...]:
    migrations = discover_migrations(migrations_directory)

    with _connect(database_url) as connection:
        _acquire_lock(connection)
        tracking_exists = _tracking_table_exists(connection)

        if not tracking_exists and _has_user_tables(connection):
            raise UntrackedDatabaseError(
                "el schema contiene tablas pero no historial; "
                "usa --baseline solo tras verificar las migraciones aplicadas"
            )

        with connection.transaction():
            _create_tracking_table(connection)

        applied = _load_applied(connection)
        _validate_history(migrations, applied)
        pending = tuple(
            migration
            for migration in migrations
            if migration.version not in applied
        )

        for migration in pending:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(migration.sql)
                    _record_applied(cursor, migration)

        return pending


def baseline(
    database_url: str,
    target: str,
    *,
    confirmed: bool,
    migrations_directory: Path = DEFAULT_MIGRATIONS_DIRECTORY,
) -> tuple[Migration, ...]:
    selected = _baseline_plan(target, migrations_directory)

    if not confirmed:
        raise MigrationError(
            "baseline no confirmado; revisa la lista y añade "
            "--confirm-baseline"
        )

    with _connect(database_url) as connection:
        _acquire_lock(connection)
        applied = _load_applied(connection)

        if applied:
            raise MigrationError(
                "schema_migrations ya contiene registros; baseline rechazado"
            )

        if not _has_user_tables(connection):
            raise UntrackedDatabaseError(
                "el schema no contiene tablas existentes; "
                "una base nueva debe ejecutar las migraciones"
            )

        with connection.transaction():
            _create_tracking_table(connection)

            with connection.cursor() as cursor:
                for migration in selected:
                    _record_applied(cursor, migration)

        return selected


def _baseline_plan(
    target: str,
    migrations_directory: Path,
) -> tuple[Migration, ...]:
    migrations = discover_migrations(migrations_directory)
    target_migration = next(
        (
            migration
            for migration in migrations
            if migration.version_label == target
        ),
        None,
    )

    if target_migration is None:
        raise MigrationDefinitionError(
            f"el baseline {target!r} no corresponde a una migración local"
        )

    return tuple(
        migration
        for migration in migrations
        if migration.version <= target_migration.version
    )


def _run_baseline(
    database_url: str,
    target: str,
    *,
    confirmed: bool,
    migrations_directory: Path,
) -> int:
    planned = _baseline_plan(target, migrations_directory)
    print("El baseline registrará sin ejecutar SQL:")

    for migration in planned:
        print(f"BASELINE {migration.filename}")

    if not confirmed:
        print(
            "ERROR: revisa el schema y repite con --confirm-baseline",
            file=sys.stderr,
        )
        return 2

    recorded = baseline(
        database_url,
        target,
        confirmed=True,
        migrations_directory=migrations_directory,
    )
    print(f"Baseline registrado: {len(recorded)} migraciones.")
    return 0


def _print_status(state: DatabaseMigrationState) -> int:
    for status in state.statuses:
        if status.applied is None:
            print(f"PENDING {status.migration.filename}")
        else:
            applied_at = status.applied.applied_at.isoformat()
            print(
                f"APPLIED {status.migration.filename} "
                f"applied_at={applied_at}"
            )

    if state.has_untracked_tables:
        print(f"WARNING: {UNTRACKED_SCHEMA_MESSAGE}", file=sys.stderr)

    return 0


def _check_state(state: DatabaseMigrationState) -> int:
    if state.has_untracked_tables:
        print(f"ERROR: {UNTRACKED_SCHEMA_MESSAGE}", file=sys.stderr)
        return 1

    pending = [
        status.migration.filename
        for status in state.statuses
        if status.applied is None
    ]

    if pending:
        print(
            "ERROR: migraciones pendientes: " + ", ".join(pending),
            file=sys.stderr,
        )
        return 1

    print(f"OK: {len(state.statuses)} migraciones aplicadas.")
    return 0


def _apply(database_url: str, migrations_directory: Path) -> int:
    applied = apply_pending(database_url, migrations_directory)

    if not applied:
        print("No hay migraciones pendientes.")

    for migration in applied:
        print(f"APPLIED {migration.filename}")

    return 0


def main(
    argv: list[str] | None = None,
    *,
    database_url: str | None = None,
    migrations_directory: Path = DEFAULT_MIGRATIONS_DIRECTORY,
) -> int:
    parser = argparse.ArgumentParser(
        description="Aplica y verifica las migraciones PostgreSQL de Job Radar."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--status",
        action="store_true",
        help="muestra migraciones aplicadas y pendientes",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="falla si hay migraciones pendientes o drift",
    )
    mode.add_argument(
        "--baseline",
        metavar="VERSION",
        help="registra hasta VERSION sin ejecutar las migraciones",
    )
    parser.add_argument(
        "--confirm-baseline",
        action="store_true",
        help="confirma que el schema ya contiene el baseline indicado",
    )
    args = parser.parse_args(argv)

    if args.confirm_baseline and args.baseline is None:
        parser.error("--confirm-baseline requiere --baseline")

    resolved_database_url = resolve_database_url(database_url)

    if not resolved_database_url:
        print("ERROR: DATABASE_URL no está configurada", file=sys.stderr)
        return 2

    try:
        import psycopg
    except ModuleNotFoundError:
        print("ERROR: psycopg no está instalado", file=sys.stderr)
        return 2

    try:
        if args.baseline is not None:
            return _run_baseline(
                resolved_database_url,
                args.baseline,
                confirmed=args.confirm_baseline,
                migrations_directory=migrations_directory,
            )

        if args.status or args.check:
            state = inspect_database(
                resolved_database_url,
                migrations_directory,
            )
            return _print_status(state) if args.status else _check_state(state)

        return _apply(resolved_database_url, migrations_directory)
    except MigrationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(
            f"ERROR: no se pudieron leer las migraciones: {error}",
            file=sys.stderr,
        )
        return 1
    except psycopg.Error as error:
        print(f"ERROR PostgreSQL: {error}", file=sys.stderr)
        return 1
