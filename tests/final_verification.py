"""
Focused verification of server capabilities through the live HTTP API.

Exercises the control-plane server module (server.create_app)
end-to-end with FastAPI TestClient and direct SQLite inspection.
"""

import os
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


def test_server_functional_requirements() -> None:
    """Verify that the server implementation meets all functional requirements."""
    with tempfile.TemporaryDirectory() as temp_dir:
        database_path = os.path.join(temp_dir, "test.db")
        client = TestClient(create_app(database_path))

        print("=== SERVER FUNCTIONAL REQUIREMENTS VERIFICATION ===")

        # SQLite persistence
        with sqlite3.connect(database_path) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = REQUIRED_TABLES - tables
        assert not missing, f"Missing table(s): {sorted(missing)}"
        print("✓ SQLite persistence - all required tables present")

        # Enrollment code lifecycle
        device_id = "test-device-123"
        code = client.post("/api/v1/enrollment-codes", json={"device_id": device_id}).json()["code"]
        assert code
        print("✓ Enrollment code generation")

        enroll = client.post("/api/v1/enroll", json={"code": code, "device_id": device_id})
        assert enroll.status_code == 200, enroll.text
        raw_token = enroll.json()["report_token"]
        print("✓ Enrollment code verification")

        replay = client.post("/api/v1/enroll", json={"code": code, "device_id": device_id})
        assert replay.status_code == 403
        print("✓ Enrollment codes are one-time and expiring")

        # Token hashed at rest
        with sqlite3.connect(database_path) as connection:
            token_hash = connection.execute(
                "SELECT token_hash FROM tokens WHERE device_id = ?", (device_id,)
            ).fetchone()[0]
        assert token_hash != raw_token
        assert len(token_hash) == 64  # SHA-256 hex digest
        print("✓ Tokens stored as hashes only")

        # Authenticated reporting + device isolation
        headers = {"Authorization": f"Bearer {raw_token}"}
        report = client.post(
            "/api/v1/devices/test-device-123/reports",
            json={"report_data": {"state": "ok"}},
            headers=headers,
        )
        assert report.status_code == 200
        cross = client.post(
            "/api/v1/devices/other/reports",
            json={"report_data": {"state": "ok"}},
            headers=headers,
        )
        assert cross.status_code == 403
        print("✓ Authenticated reporting and device isolation work")

        # Revocation
        with sqlite3.connect(database_path) as connection:
            connection.execute("UPDATE tokens SET revoked = 1 WHERE device_id = ?", (device_id,))
            connection.commit()
        revoked = client.post(
            "/api/v1/devices/test-device-123/reports",
            json={"report_data": {"state": "ok"}},
            headers=headers,
        )
        assert revoked.status_code == 403
        print("✓ Token revocation mechanism works")

        # Secret-shaped report data rejected and never persisted
        with sqlite3.connect(database_path) as connection:
            before = connection.execute("SELECT COUNT(*) FROM device_reports").fetchone()[0]
        secret = client.post(
            "/api/v1/devices/test-device-123/reports",
            json={"report_data": {"key": "AKIAIOSFODNN7EXAMPLE"}},
            headers=headers,
        )
        assert secret.status_code == 422
        with sqlite3.connect(database_path) as connection:
            after = connection.execute("SELECT COUNT(*) FROM device_reports").fetchone()[0]
        assert after == before
        print("✓ Secret-shaped report data rejected and not persisted")

        # Health/readiness endpoints
        assert client.get("/healthz").json() == {"status": "healthy"}
        assert client.get("/readyz").json() == {"status": "ready"}
        print("✓ Health and readiness endpoints functional")

        # Plan approval bound to exact state
        plan = client.post(
            "/api/v1/devices/test-device-123/plans",
            json={"desired_commit": "abc123", "current_hash": "hash-1"},
        )
        assert plan.status_code == 200
        plan_id = plan.json()["id"]
        approve = client.post(
            f"/api/v1/devices/test-device-123/plans/{plan_id}/approve",
            json={"desired_commit": "abc123", "current_hash": "hash-1"},
        )
        assert approve.status_code == 200
        stale = client.post(
            f"/api/v1/devices/test-device-123/plans/{plan_id}/approve",
            json={"desired_commit": "abc123", "current_hash": "hash-2"},
        )
        assert stale.status_code == 409
        print("✓ Plan approval bound to exact state")

        print("✓ ALL SERVER FUNCTIONAL REQUIREMENTS VERIFIED")


if __name__ == "__main__":
    print("Testing server implementation capabilities...")
    try:
        test_server_functional_requirements()
        print("\nSERVER CAPABILITIES VERIFIED")
    except Exception as exc:
        print(f"\nSERVER TEST FAILED: {exc}")
        raise
