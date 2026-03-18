import json
import sqlite3
from unittest.mock import patch

import database


def test_init_db_creates_tables(tmp_path):
    db_file = tmp_path / "test_research.db"
    with patch.object(database, "DB_PATH", db_file):
        database.init_db()
        conn = sqlite3.connect(db_file)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        conn.close()
    assert "interviews" in tables
    assert "syntheses" in tables


def test_get_connection_wal_mode(tmp_path):
    db_file = tmp_path / "wal_test.db"
    with patch.object(database, "DB_PATH", db_file):
        conn = database.get_connection()
        row = conn.execute("PRAGMA journal_mode;").fetchone()
        conn.close()
    assert row[0] == "wal"


def test_interviews_schema_has_expected_columns(tmp_path):
    db_file = tmp_path / "schema_test.db"
    with patch.object(database, "DB_PATH", db_file):
        database.init_db()
        conn = sqlite3.connect(db_file)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(interviews)").fetchall()}
        conn.close()
    schema = json.loads(database.SCHEMA_PATH.read_text())
    for field in schema["fields"]:
        assert field["key"] in cols, f"Column {field['key']} missing from interviews table"
