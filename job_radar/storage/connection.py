import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


CONNECT_TIMEOUT_SECONDS = 10


def resolve_database_url(database_url: str | None = None) -> str:
    if database_url is not None:
        return database_url
    return os.getenv("DATABASE_URL", "")


def connect(database_url: str, **options: Any) -> Any:
    # Import diferido: los módulos se pueden importar sin psycopg instalado.
    import psycopg

    return psycopg.connect(
        database_url,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        **options,
    )


@contextmanager
def open_cursor(database_url: str | None = None) -> Iterator[Any]:
    """Cursor en una transacción propia que se confirma al salir sin error."""
    with connect(resolve_database_url(database_url)) as connection:
        with connection.cursor() as cursor:
            yield cursor
