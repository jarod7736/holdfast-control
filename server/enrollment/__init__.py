"""Enrollment code lifecycle for the Holdfast control plane."""

import secrets
import time
import uuid
from typing import Any

from fastapi import HTTPException, status

from server.auth import hash_token
from server.storage import connection


def create_enrollment_code(database_path: str, device_id: str, expires_in_seconds: int) -> dict[str, Any]:
    code = secrets.token_urlsafe(32)
    expires_at = time.time() + expires_in_seconds
    with connection(database_path) as conn:
        conn.execute(
            "INSERT INTO enrollment_codes(code, device_id, expires_at) VALUES (?, ?, ?)",
            (code, device_id, expires_at),
        )
    return {"code": code, "device_id": device_id, "expires_at": expires_at}


def exchange_enrollment_code(database_path: str, code: str, device_id: str) -> dict[str, str]:
    with connection(database_path) as conn:
        row = conn.execute(
            "SELECT device_id, expires_at, used_at FROM enrollment_codes WHERE code = ?", (code,)
        ).fetchone()
        if row is None or row["used_at"] is not None or row["expires_at"] <= time.time():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or expired enrollment code")
        if row["device_id"] != device_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Enrollment code is bound to another device")
        conn.execute("UPDATE enrollment_codes SET used_at = ? WHERE code = ?", (time.time(), code))
        raw_token = secrets.token_urlsafe(48)
        conn.execute(
            "INSERT INTO tokens(id, device_id, token_hash, created_at) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), device_id, hash_token(raw_token), time.time()),
        )
    return {"report_token": raw_token}
