"""
Tests for gateway virtual-key scope on enrollment codes and key minting at enroll.
"""

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from server import create_app

ADMIN = "admin-token"


def make_client(tmp_path: Path, mint_key=None) -> tuple[TestClient, Path]:
    db = tmp_path / "control-plane.sqlite"
    return TestClient(create_app(str(db), admin_token=ADMIN, mint_key=mint_key)), db


def mint_code(client: TestClient, device_id: str = "device-a", **extra) -> str:
    response = client.post(
        "/api/v1/enrollment-codes",
        json={"device_id": device_id, **extra},
        headers={"Authorization": f"Bearer {ADMIN}"},
    )
    assert response.status_code == 200, response.text
    return response.json()["code"]


def test_enrollment_code_stores_gateway_scope(tmp_path):
    client, db = make_client(tmp_path)
    code = mint_code(client, gateway_models=["or-cheap", "or-opus"], gateway_mcp_servers=["github"])
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT gateway_models, gateway_mcp_servers FROM enrollment_codes WHERE code = ?", (code,)
        ).fetchone()
    assert json.loads(row[0]) == ["or-cheap", "or-opus"]
    assert json.loads(row[1]) == ["github"]


def test_enrollment_code_without_scope_stores_null(tmp_path):
    client, db = make_client(tmp_path)
    code = mint_code(client)
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT gateway_models, gateway_mcp_servers FROM enrollment_codes WHERE code = ?", (code,)
        ).fetchone()
    assert row == (None, None)
