import json
import os
from pathlib import Path

import psycopg2
import psycopg2.extras
from sqlalchemy import create_engine

SCHEMA_PATH = Path(__file__).parent / "schema.json"

# ---------------------------------------------------------------------------
# Singleton engine — reuse the connection pool across all calls.
# Creating a new engine on every get_engine() call means a new pool is spun
# up and immediately torn down, negating all pooling benefits and hammering
# the DB with connect/disconnect churn on every Streamlit widget interaction.
# ---------------------------------------------------------------------------
_engine = None


def _dsn() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set.")
    return url


def get_engine():
    """Return the module-level singleton SQLAlchemy engine (with connection pool)."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            _dsn(),
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,   # discard stale connections silently
        )
    return _engine


class _PgConn:
    """Thin wrapper so callers use conn.execute() / conn.commit() like sqlite3."""

    def __init__(self, raw):
        self._raw = raw
        self._cur = raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def execute(self, sql: str, params=None):
        sql = sql.replace("?", "%s")
        self._cur.execute(sql, params)
        return self._cur

    def commit(self):
        self._raw.commit()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        if exc_type:
            self._raw.rollback()
        self._raw.close()


def get_connection() -> _PgConn:
    return _PgConn(psycopg2.connect(_dsn()))


def init_db() -> None:
    schema = json.loads(SCHEMA_PATH.read_text())
    field_defs = ", ".join(f"{f['key']} {f['sql']}" for f in schema["fields"])
    with get_connection() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS interviews (
                id SERIAL PRIMARY KEY,
                source_file TEXT,
                full_transcript TEXT,
                needs_reprocessing INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT,
                {field_defs}
            )
        """)
        # Idempotency guard: one row per source file.
        # If two watcher runs overlap (e.g. GHA scheduled + manual dispatch
        # before the concurrency group queuing takes effect), the second
        # INSERT will silently skip rather than creating a duplicate row.
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS interviews_source_file_uidx
            ON interviews (source_file)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS syntheses (
                id SERIAL PRIMARY KEY,
                content TEXT,
                created_at TEXT
            )
        """)
        conn.commit()
