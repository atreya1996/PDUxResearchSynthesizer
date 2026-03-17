import json
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.json"
DB_PATH = Path(__file__).parent / "research.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    schema = json.loads(SCHEMA_PATH.read_text())
    field_defs = ", ".join(
        f"{f['key']} {f['sql']}" for f in schema["fields"]
    )
    with get_connection() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS interviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT,
                created_at TEXT
            )
        """)
        conn.commit()
