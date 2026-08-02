"""Enrollment code lifecycle for the Holdfast control plane."""

import json
import secrets
import time
import uuid
from typing import Any

from fastapi import HTTPException, status

from server.auth import hash_token
from server.storage import connection


def create_enrollment_code(
    database_path: str,
    device_id: str,
    expires_in_seconds: int,
    gateway_models: list[str] | None = None,
    gateway_mcp_servers: list[str] | None = None,
) -> dict[str, Any]:
    code = secrets.token_urlsafe(32)
    expires_at = time.time() + expires_in_seconds
    with connection(database_path) as conn:
        conn.execute(
            "INSERT INTO enrollment_codes(code, device_id, expires_at, gateway_models, gateway_mcp_servers) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                code,
                device_id,
                expires_at,
                json.dumps(gateway_models) if gateway_models else None,
                json.dumps(gateway_mcp_servers) if gateway_mcp_servers else None,
            ),
        )
    return {
        "code": code,
        "device_id": device_id,
        "expires_at": expires_at,
        "gateway_models": gateway_models or [],
        "gateway_mcp_servers": gateway_mcp_servers or [],
    }


def exchange_enrollment_code(database_path: str, code: str, device_id: str) -> dict[str, str]:
    with connection(database_path) as conn:
        row = conn.execute(
            "SELECT device_id, expires_at, used_at FROM enrollment_codes WHERE code = ?", (code,)
        ).fetchone()
        if row is None or row["used_at"] is not None or row["expires_at"] <= time.time():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or expired enrollment code")
        if row["device_id"] != device_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Enrollment code is bound to another device")
        # Atomically claim the code so concurrent exchanges cannot both succeed.
        claimed = conn.execute(
            "UPDATE enrollment_codes SET used_at = ? WHERE code = ? AND used_at IS NULL", (time.time(), code)
        )
        if claimed.rowcount != 1:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or expired enrollment code")
        raw_token = secrets.token_urlsafe(48)
        conn.execute(
            "INSERT INTO tokens(id, device_id, token_hash, created_at) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), device_id, hash_token(raw_token), time.time()),
        )
    return {"report_token": raw_token}
