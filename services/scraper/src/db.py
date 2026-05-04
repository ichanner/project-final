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
    """Apply tiny additive migrations for long-lived local compose volumes.

    `db/init.sql` only runs when the Postgres volume is first created. The
    demo environment is often reused, so new nullable/defaulted columns need a
    lightweight compatibility pass at service startup.
    """
    statements = [
        "ALTER TABLE sources ADD COLUMN IF NOT EXISTS conditional_polling BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE sources ADD COLUMN IF NOT EXISTS etag TEXT",
        "ALTER TABLE sources ADD COLUMN IF NOT EXISTS last_modified TEXT",
        "ALTER TABLE sources ADD COLUMN IF NOT EXISTS last_content_bytes INT",
    ]
    with conn() as c, c.cursor() as cur:
        for stmt in statements:
            cur.execute(stmt)
