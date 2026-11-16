"""Postgres connection pool and schema setup."""
import time
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

_pool = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


def wait_for_db(timeout: float = 60) -> None:
    deadline = time.time() + timeout
    while True:
        try:
            with psycopg.connect(settings.database_url, connect_timeout=3):
                return
        except psycopg.OperationalError:
            if time.time() > deadline:
                raise
            time.sleep(1)


def init_schema() -> None:
    """Idempotent. The advisory lock stops the API and worker racing at startup."""
    sql = (Path(__file__).parent / "schema.sql").read_text()
    with pool().connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(727274)")
        conn.execute(sql)


def bump_generation(conn) -> None:
    conn.execute("UPDATE index_state SET generation = generation + 1 WHERE id = 1")
