import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from server import create_app

ADMIN_TOKEN = "test-admin-token"


def make_client(tmp_path: Path) -> tuple[TestClient, Path]:
    database_path = tmp_path / "control-plane.sqlite"
    return TestClient(create_app(str(database_path), admin_token=ADMIN_TOKEN)), database_path


def admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def enroll(client: TestClient, device_id: str = "device-a", expires_in_seconds: int = 600) -> tuple[str, str]:
    code_response = client.post(
        "/api/v1/enrollment-codes",
        json={"device_id": device_id, "expires_in_seconds": expires_in_seconds},
        headers=admin_headers(),
    )
    assert code_response.status_code == 200
    code = code_response.json()["code"]
    enroll_response = client.post("/api/v1/enroll", json={"code": code, "device_id": device_id})
    assert enroll_response.status_code == 200
    return code, enroll_response.json()["report_token"]


def test_enrollment_is_one_time_expiring_and_device_bound(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    code_response = client.post("/api/v1/enrollment-codes", json={"device_id": "device-a"}, headers=admin_headers())
    code = code_response.json()["code"]
    assert client.post("/api/v1/enroll", json={"code": code, "device_id": "device-b"}).status_code == 403
    assert client.post("/api/v1/enroll", json={"code": code, "device_id": "device-a"}).status_code == 200
    assert client.post("/api/v1/enroll", json={"code": code, "device_id": "device-a"}).status_code == 403
    expired = client.post(
        "/api/v1/enrollment-codes",
        json={"device_id": "device-a", "expires_in_seconds": 1},
        headers=admin_headers(),
    ).json()["code"]
    connection = sqlite3.connect(tmp_path / "control-plane.sqlite")
    connection.execute("UPDATE enrollment_codes SET expires_at = 0 WHERE code = ?", (expired,))
    connection.commit()
    connection.close()
    assert client.post("/api/v1/enroll", json={"code": expired, "device_id": "device-a"}).status_code == 403


def test_token_is_hash_only_in_sqlite(tmp_path: Path) -> None:
    client, database_path = make_client(tmp_path)
    _, token = enroll(client)
    connection = sqlite3.connect(database_path)
    stored_hash = connection.execute("SELECT token_hash FROM tokens").fetchone()[0]
    connection.close()
    assert token != stored_hash
    assert token not in Path(database_path).read_bytes().decode("latin-1")
    assert len(stored_hash) == 64


def test_report_token_is_device_bound_and_revocable(tmp_path: Path) -> None:
    client, database_path = make_client(tmp_path)
    _, token_a = enroll(client, "device-a")
    _, token_b = enroll(client, "device-b")
    payload = {"report_data": {"status": "ok"}}
    assert client.post("/api/v1/devices/device-a/reports", json=payload).status_code == 401
    assert client.post("/api/v1/devices/device-a/reports", json=payload, headers={"Authorization": f"Bearer {token_b}"}).status_code == 403
    assert client.post("/api/v1/devices/device-a/reports", json=payload, headers={"Authorization": f"Bearer {token_a}"}).status_code == 200
    connection = sqlite3.connect(database_path)
    connection.execute("UPDATE tokens SET revoked = 1 WHERE device_id = ?", ("device-a",))
    connection.commit()
    connection.close()
    assert client.post("/api/v1/devices/device-a/reports", json=payload, headers={"Authorization": f"Bearer {token_a}"}).status_code == 403


def test_plan_approval_is_persisted_and_bound(tmp_path: Path) -> None:
    client, database_path = make_client(tmp_path)
    create = client.post(
        "/api/v1/devices/device-a/plans",
        json={"desired_commit": "commit-a", "current_hash": "hash-a"},
        headers=admin_headers(),
    )
    assert create.status_code == 200
    plan_id = create.json()["id"]
    assert client.get("/api/v1/devices/device-a/plans", headers=admin_headers()).json()[0]["id"] == plan_id
    assert client.post(f"/api/v1/devices/device-a/plans/{plan_id}/approve", json={"current_hash": "wrong", "desired_commit": "commit-a"}, headers=admin_headers()).status_code == 409
    assert client.post(f"/api/v1/devices/device-a/plans/{plan_id}/approve", json={"current_hash": "hash-a", "desired_commit": "wrong"}, headers=admin_headers()).status_code == 409
    assert client.post(f"/api/v1/devices/device-a/plans/{plan_id}/approve", json={"current_hash": "hash-a", "desired_commit": "commit-a"}, headers=admin_headers()).status_code == 200
    connection = sqlite3.connect(database_path)
    assert connection.execute("SELECT approval_status FROM plans WHERE id = ?", (plan_id,)).fetchone()[0] == "approved"
    assert connection.execute("SELECT COUNT(*) FROM plan_approvals WHERE plan_id = ?", (plan_id,)).fetchone()[0] == 1
    connection.execute("UPDATE plans SET expiry_timestamp = 0 WHERE id = ?", (plan_id,))
    connection.commit()
    connection.close()
    assert client.post(f"/api/v1/devices/device-a/plans/{plan_id}/approve", json={"current_hash": "hash-a", "desired_commit": "commit-a"}, headers=admin_headers()).status_code == 409


def test_secret_shaped_report_never_persists(tmp_path: Path) -> None:
    client, database_path = make_client(tmp_path)
    _, token = enroll(client)
    secret = "sk-1234567890"
    response = client.post(
        "/api/v1/devices/device-a/reports",
        json={"report_data": {"provider_key": secret}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422
    connection = sqlite3.connect(database_path)
    reports = connection.execute("SELECT report_data FROM device_reports").fetchall()
    connection.close()
    assert all(secret not in report[0] for report in reports)


def test_admin_token_required_for_operator_endpoints(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    # No admin token -> 401 on operator endpoints
    assert client.post("/api/v1/enrollment-codes", json={"device_id": "device-a"}).status_code == 401
    assert client.post("/api/v1/enrollment-codes", json={"device_id": "device-a"}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/v1/devices").status_code == 401
    assert client.get("/api/v1/devices/device-a").status_code == 401
    assert client.get("/api/v1/devices/device-a/drift").status_code == 401
    assert client.post("/api/v1/devices/device-a/plans", json={"desired_commit": "c", "current_hash": "h"}).status_code == 401
    assert client.get("/api/v1/devices/device-a/plans").status_code == 401
    assert client.get("/api/v1/capabilities").status_code == 401
    assert client.get("/api/v1/credentials/status").status_code == 401
    assert client.get("/api/v1/integrations/status").status_code == 401
    assert client.get("/api/v1/docs/status").status_code == 401
    # Valid admin token -> authorized
    assert client.post("/api/v1/enrollment-codes", json={"device_id": "device-a"}, headers=admin_headers()).status_code == 200
    assert client.get("/api/v1/devices", headers=admin_headers()).status_code == 200
    assert client.get("/api/v1/capabilities", headers=admin_headers()).status_code == 200
    assert client.get("/api/v1/docs/status", headers=admin_headers()).status_code == 200
    # Health endpoints stay public
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


def test_server_without_admin_token_fails_closed(tmp_path: Path) -> None:
    client = TestClient(create_app(str(tmp_path / "no-admin.sqlite")))
    assert client.post("/api/v1/enrollment-codes", json={"device_id": "device-a"}).status_code == 503
    assert client.get("/api/v1/devices").status_code == 503
    assert client.get("/healthz").status_code == 200
