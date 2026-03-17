import json
import os
from pathlib import Path

import psycopg2
import psycopg2.extras
from sqlalchemy import create_engine

SCHEMA_PATH = Path(__file__).parent / "schema.json"


def _dsn() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set.")
    return url


def get_engine():
    """SQLAlchemy engine — used by pandas.read_sql."""
    return create_engine(_dsn())


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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS syntheses (
                id SERIAL PRIMARY KEY,
                content TEXT,
                created_at TEXT
            )
        """)
        conn.commit()
