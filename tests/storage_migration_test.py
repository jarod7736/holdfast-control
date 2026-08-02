"""
Tests that init_database creates the gateway-key schema and upgrades old databases.
"""

import sqlite3

from server.storage import init_database


def columns(db_path: str, table: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_fresh_database_has_gateway_scope_columns(tmp_path):
    db = str(tmp_path / "fresh.sqlite")
    init_database(db)
    assert {"gateway_models", "gateway_mcp_servers"} <= columns(db, "enrollment_codes")


def test_fresh_database_has_gateway_keys_table(tmp_path):
    db = str(tmp_path / "fresh.sqlite")
    init_database(db)
    assert {"id", "device_id", "key_alias", "models", "mcp_servers", "minted_at"} <= columns(db, "gateway_keys")


def test_existing_database_is_upgraded(tmp_path):
    """A database created with the old enrollment_codes shape gains the new columns."""
    db = str(tmp_path / "old.sqlite")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE enrollment_codes (code TEXT PRIMARY KEY, device_id TEXT NOT NULL, "
            "expires_at REAL NOT NULL, used_at REAL)"
        )
    init_database(db)
    assert {"gateway_models", "gateway_mcp_servers"} <= columns(db, "enrollment_codes")
