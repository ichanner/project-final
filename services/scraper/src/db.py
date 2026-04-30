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
