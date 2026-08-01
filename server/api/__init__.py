"""FastAPI routes for the Holdfast control plane."""

import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from server import adapters, auth, enrollment
from server.storage import connection


class EnrollmentCodeRequest(BaseModel):
    device_id: str = Field(min_length=1)
    expires_in_seconds: int = Field(default=600, ge=1, le=3600)


class EnrollmentRequest(BaseModel):
    code: str
    device_id: str = Field(min_length=1)


class DeviceReportRequest(BaseModel):
    report_data: dict[str, Any]


class PlanCreateRequest(BaseModel):
    desired_commit: str = Field(min_length=1)
    current_hash: str = Field(min_length=1)
    expiry_hours: int = Field(default=24, ge=0, le=168)


class PlanApprovalRequest(BaseModel):
    current_hash: str = Field(min_length=1)
    desired_commit: str = Field(min_length=1)


def create_router(database_path: str) -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "healthy"}

    @router.get("/readyz")
    def readyz() -> dict[str, str]:
        return {"status": "ready"}

    @router.post("/api/v1/enrollment-codes")
    def create_enrollment_code(request: EnrollmentCodeRequest) -> dict[str, Any]:
        return enrollment.create_enrollment_code(database_path, request.device_id, request.expires_in_seconds)

    @router.post("/api/v1/enroll")
    def enroll(request: EnrollmentRequest) -> dict[str, str]:
        return enrollment.exchange_enrollment_code(database_path, request.code, request.device_id)

    @router.post("/api/v1/devices/{device_id}/reports")
    def report(device_id: str, request: DeviceReportRequest, authorization: str | None = Header(default=None)) -> dict[str, str]:
        raw_token = auth.authorization_token(authorization)
        auth.validate_report_data(request.report_data)
        with connection(database_path) as conn:
            auth.authorize_report(conn, raw_token, device_id)
            conn.execute(
                "INSERT INTO device_reports(id, device_id, report_data, created_at) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), device_id, json.dumps(request.report_data, sort_keys=True), time.time()),
            )
        return {"status": "accepted"}

    @router.post("/api/v1/devices/{device_id}/plans")
    def create_plan(device_id: str, request: PlanCreateRequest) -> dict[str, Any]:
        plan_id = str(uuid.uuid4())
        expires_at = time.time() + request.expiry_hours * 3600
        created_at = time.time()
        with connection(database_path) as conn:
            conn.execute(
                "INSERT INTO plans(id, device_id, desired_commit, current_hash, expiry_timestamp, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (plan_id, device_id, request.desired_commit, request.current_hash, expires_at, created_at),
            )
        return {"id": plan_id, "device_id": device_id, "desired_commit": request.desired_commit, "current_hash": request.current_hash, "expiry_timestamp": expires_at, "approval_status": "pending", "created_at": created_at}

    @router.get("/api/v1/devices/{device_id}/plans")
    def list_plans(device_id: str) -> list[dict[str, Any]]:
        with connection(database_path) as conn:
            rows = conn.execute("SELECT * FROM plans WHERE device_id = ? ORDER BY created_at", (device_id,)).fetchall()
        return [dict(row) for row in rows]

    @router.post("/api/v1/devices/{device_id}/plans/{plan_id}/approve")
    def approve_plan(device_id: str, plan_id: str, request: PlanApprovalRequest) -> dict[str, str]:
        with connection(database_path) as conn:
            plan = conn.execute("SELECT * FROM plans WHERE id = ? AND device_id = ?", (plan_id, device_id)).fetchone()
            if plan is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
            if plan["expiry_timestamp"] <= time.time():
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Plan has expired")
            if plan["current_hash"] != request.current_hash or plan["desired_commit"] != request.desired_commit:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Plan state no longer matches approval")
            conn.execute("UPDATE plans SET approval_status = 'approved' WHERE id = ?", (plan_id,))
            conn.execute(
                "INSERT INTO plan_approvals(id, plan_id, device_id, current_hash, desired_commit, approved_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), plan_id, device_id, request.current_hash, request.desired_commit, time.time()),
            )
        return {"status": "approved"}

    @router.get("/api/v1/devices/{device_id}/drift")
    def drift(device_id: str) -> dict[str, Any]:
        with connection(database_path) as conn:
            report = conn.execute("SELECT created_at FROM device_reports WHERE device_id = ? ORDER BY created_at DESC LIMIT 1", (device_id,)).fetchone()
        return {"device_id": device_id, "status": "unknown" if report is None else "reported", "reported_at": None if report is None else report["created_at"]}

    @router.get("/api/v1/devices/{device_id}")
    def device(device_id: str) -> dict[str, Any]:
        with connection(database_path) as conn:
            report = conn.execute("SELECT created_at FROM device_reports WHERE device_id = ? ORDER BY created_at DESC LIMIT 1", (device_id,)).fetchone()
            plan_count = conn.execute("SELECT COUNT(*) FROM plans WHERE device_id = ?", (device_id,)).fetchone()
        return {
            "id": device_id,
            "last_reported_at": None if report is None else report["created_at"],
            "drift_status": "unknown" if report is None else "reported",
            "plan_count": 0 if plan_count is None else plan_count[0],
        }

    @router.get("/api/v1/devices")
    def devices() -> list[dict[str, Any]]:
        with connection(database_path) as conn:
            rows = conn.execute(
                "SELECT device_id, MAX(created_at) AS last_reported_at FROM device_reports GROUP BY device_id ORDER BY device_id"
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                plan_count = conn.execute("SELECT COUNT(*) FROM plans WHERE device_id = ?", (row["device_id"],)).fetchone()
                result.append(
                    {
                        "id": row["device_id"],
                        "last_reported_at": row["last_reported_at"],
                        "drift_status": "reported",
                        "plan_count": 0 if plan_count is None else plan_count[0],
                    }
                )
        return result

    @router.get("/api/v1/capabilities")
    def capabilities() -> list[dict[str, Any]]:
        return adapters.capabilities_status(database_path)

    @router.get("/api/v1/credentials/status")
    def credential_status() -> list[dict[str, Any]]:
        return adapters.credentials_status(database_path)

    @router.get("/api/v1/integrations/status")
    def integrations_status() -> list[dict[str, Any]]:
        return adapters.integrations_status(database_path)

    @router.get("/api/v1/docs/status")
    def docs_status() -> list[dict[str, Any]]:
        return adapters.docs_status()

    return router
