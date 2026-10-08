import io
import os
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

try:
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
except ModuleNotFoundError:
    psycopg = None

from job_radar.storage import migrations


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
PROJECT_MIGRATIONS = Path("sql")


@unittest.skipUnless(
    TEST_DATABASE_URL and psycopg is not None,
    "PostgreSQL de pruebas no configurado",
)
class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.schema = f"migration_test_{uuid.uuid4().hex}"

        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("CREATE SCHEMA {}").format(
                        sql.Identifier(self.schema)
                    )
                )

        self.database_url = make_conninfo(
            TEST_DATABASE_URL,
            options=f"-c search_path={self.schema}",
        )
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.migrations_directory = Path(self.temporary_directory.name)

        for source in sorted(PROJECT_MIGRATIONS.glob("*.sql")):
            destination = self.migrations_directory / source.name
            destination.write_bytes(source.read_bytes())

    def tearDown(self):
        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(self.schema)
                    )
                )

    def fetchall(self, query, parameters=None):
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, parameters)
                return cursor.fetchall()

    def relation_exists(self, name):
        return self.fetchall(
            "SELECT to_regclass(%s) IS NOT NULL",
            (name,),
        )[0][0]

    def test_empty_database_applies_all_migrations(self):
        expected = migrations.discover_migrations(self.migrations_directory)
        applied = migrations.apply_pending(
            self.database_url,
            self.migrations_directory,
        )

        self.assertEqual(
            [migration.filename for migration in applied],
            [migration.filename for migration in expected],
        )
        self.assertTrue(self.relation_exists("jobs"))
        self.assertTrue(self.relation_exists("ingestion_runs"))
        self.assertTrue(self.relation_exists("source_runs"))
        self.assertEqual(
            self.fetchall(
                "SELECT version, filename, length(checksum) "
                "FROM schema_migrations ORDER BY version"
            ),
            [
                (migration.version, migration.filename, 64)
                for migration in expected
            ],
        )

    def test_second_execution_does_nothing(self):
        migrations.apply_pending(
            self.database_url,
            self.migrations_directory,
        )
        before = self.fetchall(
            "SELECT version, applied_at FROM schema_migrations "
            "ORDER BY version"
        )

        applied = migrations.apply_pending(
            self.database_url,
            self.migrations_directory,
        )

        self.assertEqual(applied, ())
        self.assertEqual(
            self.fetchall(
                "SELECT version, applied_at FROM schema_migrations "
                "ORDER BY version"
            ),
            before,
        )

    def test_only_new_migration_is_applied(self):
        migrations.apply_pending(
            self.database_url,
            self.migrations_directory,
        )
        next_version = max(
            migration.version
            for migration in migrations.discover_migrations(
                self.migrations_directory
            )
        ) + 1
        filename = f"{next_version:03d}_create_fixture.sql"
        fixture = self.migrations_directory / filename
        fixture.write_text(
            "CREATE TABLE migration_fixture (id INTEGER PRIMARY KEY);\n",
            encoding="utf-8",
        )

        applied = migrations.apply_pending(
            self.database_url,
            self.migrations_directory,
        )

        self.assertEqual(
            [migration.filename for migration in applied],
            [filename],
        )
        self.assertTrue(self.relation_exists("migration_fixture"))
        self.assertEqual(
            self.fetchall(
                "SELECT filename FROM schema_migrations ORDER BY version"
            )[-1][0],
            filename,
        )

    def test_failed_migration_rolls_back_and_is_not_registered(self):
        migrations.apply_pending(
            self.database_url,
            self.migrations_directory,
        )
        next_version = max(
            migration.version
            for migration in migrations.discover_migrations(
                self.migrations_directory
            )
        ) + 1
        fixture = (
            self.migrations_directory / f"{next_version:03d}_broken.sql"
        )
        fixture.write_text(
            "CREATE TABLE migration_will_rollback (id INTEGER);\n"
            "SELECT 1 / 0;\n",
            encoding="utf-8",
        )

        with self.assertRaises(psycopg.Error):
            migrations.apply_pending(
                self.database_url,
                self.migrations_directory,
            )

        self.assertFalse(self.relation_exists("migration_will_rollback"))
        self.assertEqual(
            self.fetchall(
                "SELECT COUNT(*) FROM schema_migrations WHERE version = %s",
                (next_version,),
            )[0][0],
            0,
        )

    def test_modified_applied_migration_is_drift(self):
        migrations.apply_pending(
            self.database_url,
            self.migrations_directory,
        )
        migration = self.migrations_directory / "004_add_snapshot_health.sql"
        migration.write_text(
            migration.read_text(encoding="utf-8") + "\n-- modified\n",
            encoding="utf-8",
        )

        with self.assertRaises(migrations.MigrationDriftError):
            migrations.inspect_database(
                self.database_url,
                self.migrations_directory,
            )

        with redirect_stderr(io.StringIO()):
            result = migrations.main(
                ["--check"],
                database_url=self.database_url,
                migrations_directory=self.migrations_directory,
            )

        self.assertEqual(result, 1)

    def test_status_and_check_are_read_only(self):
        migration_count = len(
            migrations.discover_migrations(self.migrations_directory)
        )
        status_output = io.StringIO()

        with redirect_stdout(status_output):
            status_result = migrations.main(
                ["--status"],
                database_url=self.database_url,
                migrations_directory=self.migrations_directory,
            )

        with redirect_stderr(io.StringIO()):
            check_result = migrations.main(
                ["--check"],
                database_url=self.database_url,
                migrations_directory=self.migrations_directory,
            )

        self.assertEqual(status_result, 0)
        self.assertEqual(
            status_output.getvalue().count("PENDING "),
            migration_count,
        )
        self.assertEqual(check_result, 1)
        self.assertFalse(self.relation_exists("schema_migrations"))

        migrations.apply_pending(
            self.database_url,
            self.migrations_directory,
        )
        status_output = io.StringIO()

        with redirect_stdout(status_output):
            status_result = migrations.main(
                ["--status"],
                database_url=self.database_url,
                migrations_directory=self.migrations_directory,
            )
            check_result = migrations.main(
                ["--check"],
                database_url=self.database_url,
                migrations_directory=self.migrations_directory,
            )

        self.assertEqual(status_result, 0)
        self.assertEqual(
            status_output.getvalue().count("APPLIED "),
            migration_count,
        )
        self.assertEqual(check_result, 0)

    def test_explicit_baseline_records_existing_database(self):
        with self.assertRaises(migrations.UntrackedDatabaseError):
            migrations.baseline(
                self.database_url,
                "004",
                confirmed=True,
                migrations_directory=self.migrations_directory,
            )

        discovered = migrations.discover_migrations(
            self.migrations_directory
        )
        baseline_migrations = tuple(
            migration
            for migration in discovered
            if migration.version <= 4
        )

        for migration in baseline_migrations:
            with psycopg.connect(self.database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(migration.sql)

        with self.assertRaises(migrations.UntrackedDatabaseError):
            migrations.apply_pending(
                self.database_url,
                self.migrations_directory,
            )

        output = io.StringIO()

        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            result = migrations.main(
                ["--baseline", "004"],
                database_url=self.database_url,
                migrations_directory=self.migrations_directory,
            )

        self.assertEqual(result, 2)
        self.assertEqual(
            output.getvalue().count("BASELINE "),
            len(baseline_migrations),
        )
        self.assertFalse(self.relation_exists("schema_migrations"))

        with redirect_stdout(io.StringIO()):
            result = migrations.main(
                ["--baseline", "004", "--confirm-baseline"],
                database_url=self.database_url,
                migrations_directory=self.migrations_directory,
            )

        self.assertEqual(result, 0)
        self.assertEqual(
            self.fetchall("SELECT COUNT(*) FROM schema_migrations")[0][0],
            len(baseline_migrations),
        )
        migrations.apply_pending(
            self.database_url,
            self.migrations_directory,
        )
        with redirect_stdout(io.StringIO()):
            result = migrations.main(
                ["--check"],
                database_url=self.database_url,
                migrations_directory=self.migrations_directory,
            )

        self.assertEqual(result, 0)

    def test_advisory_lock_prevents_concurrent_migrator(self):
        with psycopg.connect(
            self.database_url,
            autocommit=True,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_lock(%s, %s)",
                    migrations.ADVISORY_LOCK_KEY,
                )

            with self.assertRaises(migrations.MigrationLockError):
                migrations.apply_pending(
                    self.database_url,
                    self.migrations_directory,
                )
