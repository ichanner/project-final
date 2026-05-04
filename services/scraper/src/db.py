import os
from contextlib import contextmanager

from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgres://webharvest:webharvest@postgres:5432/webharvest",
)

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=5, open=True)
    return _pool


@contextmanager
def conn():
    pool = get_pool()
    with pool.connection() as c:
        yield c


def ensure_runtime_schema() -> None:
    """Hook for additive migrations on long-lived local compose volumes.

    `db/init.sql` only runs when the Postgres volume is first created. The
    demo environment is often reused, so this is the place to apply tiny
    nullable/defaulted column additions at service startup. Currently a no-op
    — the schema in init.sql is the full picture.
    """
    return
