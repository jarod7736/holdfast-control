"""SQLite storage for the Holdfast control plane."""

import sqlite3
from pathlib import Path

_ADAPTER_SEEDS: list[tuple[str, str]] = [
    ("opencode", "capability"),
    ("providers", "capability"),
    ("mcp_servers", "capability"),
    ("skills", "capability"),
    ("observatory", "integration"),
    ("network_monitor", "integration"),
    ("litellm", "integration"),
    ("amd-halo", "integration"),
    ("lan-orangutan", "integration"),
    ("documentation", "integration"),
]

_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS enrollment_codes (
    code TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    expires_at REAL NOT NULL,
    used_at REAL
);
CREATE TABLE IF NOT EXISTS tokens (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    revoked INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS device_reports (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    report_data TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    desired_commit TEXT NOT NULL,
    current_hash TEXT NOT NULL,
    expiry_timestamp REAL NOT NULL,
    approval_status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_approvals (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    current_hash TEXT NOT NULL,
    desired_commit TEXT NOT NULL,
    approved_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS adapter_health (
    adapter TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unknown',
    detail TEXT,
    last_checked_at REAL
);
CREATE TABLE IF NOT EXISTS credential_metadata (
    id TEXT PRIMARY KEY,
    owner TEXT,
    purpose TEXT,
    environment TEXT,
    device TEXT,
    service TEXT,
    created_at REAL,
    rotated_at REAL,
    rotation_days INTEGER,
    managed_by TEXT,
    credential_type TEXT,
    recovery_required INTEGER NOT NULL DEFAULT 0
);
"""


def connection(database_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def init_database(database_path: str) -> None:
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as connection:
        connection.executescript(_SCHEMA)
        connection.executemany(
            "INSERT OR IGNORE INTO adapter_health(adapter, kind) VALUES (?, ?)",
            _ADAPTER_SEEDS,
        )
