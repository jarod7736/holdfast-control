"""Authentication and token handling for the Holdfast control plane."""

import hashlib
import sqlite3
from typing import Any

from fastapi import HTTPException, status

from holdfastctl.manifest_schema import validate_secret_literals


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def authorization_token(authorization: str | None) -> str:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    return token


def validate_report_data(report_data: dict[str, Any]) -> None:
    def scan(value: Any, path: str = "report_data") -> None:
        if isinstance(value, str):
            validate_secret_literals(value, path)
        elif isinstance(value, dict):
            for key, item in value.items():
                scan(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                scan(item, f"{path}[{index}]")

    try:
        scan(report_data)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Report contains secret-shaped data") from exc


def authorize_report(connection: sqlite3.Connection, raw_token: str, device_id: str) -> None:
    token = connection.execute(
        "SELECT device_id FROM tokens WHERE token_hash = ? AND revoked = 0", (hash_token(raw_token),)
    ).fetchone()
    if token is None or token["device_id"] != device_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token is not authorized for this device")
