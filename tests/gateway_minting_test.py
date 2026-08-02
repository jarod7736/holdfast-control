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


def enroll(client: TestClient, code: str, device_id: str = "device-a"):
    return client.post("/api/v1/enroll", json={"code": code, "device_id": device_id})


def test_enroll_without_scope_returns_only_report_token(tmp_path):
    client, _ = make_client(tmp_path)
    code = mint_code(client)
    response = enroll(client, code)
    assert response.status_code == 200
    body = response.json()
    assert "report_token" in body
    assert "gateway_key" not in body


def test_enroll_with_scope_mints_scoped_key(tmp_path):
    minted: dict = {}

    def fake_mint(device_id, models, mcp_servers):
        minted.update(device_id=device_id, models=models, mcp_servers=mcp_servers)
        return "sk-minted-key", f"holdfast-{device_id}-abcd1234"

    client, _ = make_client(tmp_path, mint_key=fake_mint)
    code = mint_code(client, gateway_models=["or-cheap"], gateway_mcp_servers=["github"])
    response = enroll(client, code)
    assert response.status_code == 200
    body = response.json()
    assert body["gateway_key"] == "sk-minted-key"
    assert body["gateway_key_alias"] == "holdfast-device-a-abcd1234"
    assert minted == {"device_id": "device-a", "models": ["or-cheap"], "mcp_servers": ["github"]}


def test_gateway_key_material_never_persisted(tmp_path):
    client, db = make_client(tmp_path, mint_key=lambda d, m, s: ("sk-canary-key-material", "alias-1"))
    code = mint_code(client, gateway_models=["or-cheap"])
    assert enroll(client, code).status_code == 200
    assert b"sk-canary-key-material" not in db.read_bytes()


def test_gateway_key_metadata_recorded(tmp_path):
    client, db = make_client(tmp_path, mint_key=lambda d, m, s: ("sk-x", "holdfast-device-a-ff00ff00"))
    code = mint_code(client, gateway_models=["or-cheap"], gateway_mcp_servers=["github"])
    assert enroll(client, code).status_code == 200
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT device_id, key_alias, models, mcp_servers FROM gateway_keys"
        ).fetchone()
    assert row[0] == "device-a"
    assert row[1] == "holdfast-device-a-ff00ff00"
    assert json.loads(row[2]) == ["or-cheap"]
    assert json.loads(row[3]) == ["github"]


def test_mint_failure_returns_502_and_code_stays_usable(tmp_path):
    from server.gateway import GatewayError

    attempts: list[int] = []

    def flaky_mint(device_id, models, mcp_servers):
        attempts.append(1)
        if len(attempts) == 1:
            raise GatewayError("gateway unreachable")
        return "sk-second-try", "alias-2"

    client, _ = make_client(tmp_path, mint_key=flaky_mint)
    code = mint_code(client, gateway_models=["or-cheap"])
    assert enroll(client, code).status_code == 502
    retry = enroll(client, code)
    assert retry.status_code == 200
    assert retry.json()["gateway_key"] == "sk-second-try"


def test_scope_without_configured_minter_returns_503(tmp_path):
    client, _ = make_client(tmp_path, mint_key=None)
    code = mint_code(client, gateway_models=["or-cheap"])
    assert enroll(client, code).status_code == 503
