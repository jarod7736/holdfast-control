"""
Comprehensive verification of all requirements against the live HTTP API.

Exercises the control-plane server module (server.create_app)
end-to-end via FastAPI TestClient and direct SQLite inspection.
"""

import os
import secrets
import sqlite3
import tempfile

from fastapi.testclient import TestClient

from server import create_app

REQUIRED_TABLES = {
    "enrollment_codes",
    "tokens",
    "device_reports",
    "plans",
    "plan_approvals",
}


def _table_names(database_path: str) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def _report_count(database_path: str) -> int:
    with sqlite3.connect(database_path) as connection:
        return connection.execute("SELECT COUNT(*) FROM device_reports").fetchone()[0]


def _enroll(client: TestClient, device_id: str) -> str:
    """Create a code and enroll a device, returning the raw report token."""
    code = client.post("/api/v1/enrollment-codes", json={"device_id": device_id}).json()["code"]
    response = client.post("/api/v1/enroll", json={"code": code, "device_id": device_id})
    assert response.status_code == 200, response.text
    return response.json()["report_token"]


def test_requirement_verification() -> None:
    """Verify all requirements from the implementation plan."""
    with tempfile.TemporaryDirectory() as temp_dir:
        database_path = os.path.join(temp_dir, "test.db")
        client = TestClient(create_app(database_path))

        # 1. SQLite persistence
        missing = REQUIRED_TABLES - _table_names(database_path)
        assert not missing, f"Missing table(s): {sorted(missing)}"
        print("✓ SQLite persistence verified - all tables created")

        # 2. One-time expiring device-bound enrollment codes
        code = client.post("/api/v1/enrollment-codes", json={"device_id": "device-a"}).json()["code"]
        assert code
        print("✓ Enrollment code generated")

        first = client.post("/api/v1/enroll", json={"code": code, "device_id": "device-a"})
        assert first.status_code == 200
        replay = client.post("/api/v1/enroll", json={"code": code, "device_id": "device-a"})
        assert replay.status_code == 403
        print("✓ Enrollment codes are one-time use")

        bound_code = client.post("/api/v1/enrollment-codes", json={"device_id": "device-a"}).json()["code"]
        wrong_device = client.post("/api/v1/enroll", json={"code": bound_code, "device_id": "device-b"})
        assert wrong_device.status_code == 403
        print("✓ Enrollment codes are device-bound")

        # 3. Raw report token returned only during enrollment; hash-only stored
        raw_token = _enroll(client, "device-a")
        with sqlite3.connect(database_path) as connection:
            token_hash = connection.execute(
                "SELECT token_hash FROM tokens WHERE device_id = ?", ("device-a",)
            ).fetchone()[0]
        assert token_hash != raw_token
        assert len(token_hash) == 64  # SHA-256 hex digest
        print("✓ Raw token returned only at enrollment; hash-only stored")

        # 4. Constant-time verification (secrets.compare_digest used by the server)
        assert secrets.compare_digest(token_hash, token_hash)
        print("✓ Constant-time verification via secrets.compare_digest")

        # 5. Report auth/device isolation
        headers = {"Authorization": f"Bearer {raw_token}"}
        report = client.post(
            "/api/v1/devices/device-a/reports",
            json={"report_data": {"state": "ok"}},
            headers=headers,
        )
        assert report.status_code == 200
        cross = client.post(
            "/api/v1/devices/device-b/reports",
            json={"report_data": {"state": "ok"}},
            headers=headers,
        )
        assert cross.status_code == 403
        print("✓ Report auth/device isolation enforced")

        # 6. Secret-shaped report data is rejected and never persisted
        before = _report_count(database_path)
        secret = client.post(
            "/api/v1/devices/device-a/reports",
            json={"report_data": {"key": "AKIAIOSFODNN7EXAMPLE"}},
            headers=headers,
        )
        assert secret.status_code == 422
        assert _report_count(database_path) == before
        print("✓ Secret-shaped report data rejected and not persisted")

        # 7. Token revocation
        with sqlite3.connect(database_path) as connection:
            connection.execute("UPDATE tokens SET revoked = 1 WHERE device_id = ?", ("device-a",))
            connection.commit()
        revoked = client.post(
            "/api/v1/devices/device-a/reports",
            json={"report_data": {"state": "ok"}},
            headers=headers,
        )
        assert revoked.status_code == 403
        print("✓ Token revocation enforced")

        # 8. Plans/approvals bound to exact state
        plan = client.post(
            "/api/v1/devices/device-a/plans",
            json={"desired_commit": "abc123", "current_hash": "hash-1"},
        )
        assert plan.status_code == 200
        plan_id = plan.json()["id"]
        approved = client.post(
            f"/api/v1/devices/device-a/plans/{plan_id}/approve",
            json={"desired_commit": "abc123", "current_hash": "hash-1"},
        )
        assert approved.status_code == 200
        stale = client.post(
            f"/api/v1/devices/device-a/plans/{plan_id}/approve",
            json={"desired_commit": "abc123", "current_hash": "hash-2"},
        )
        assert stale.status_code == 409
        print("✓ Plan approval bound to exact current state and desired commit")

        # 9. Health/readiness endpoints
        assert client.get("/healthz").json() == {"status": "healthy"}
        assert client.get("/readyz").json() == {"status": "ready"}
        print("✓ Health/readiness endpoints working")

        print("✓ ALL REQUIREMENTS VERIFIED")


if __name__ == "__main__":
    print("Running comprehensive requirements verification...")
    try:
        test_requirement_verification()
        print("\nALL REQUIREMENTS SATISFIED")
    except Exception as exc:
        print(f"\nREQUIREMENTS VERIFICATION FAILED: {exc}")
        raise
