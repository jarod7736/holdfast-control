"""
Tests for device-token access to plans (Phase 2 loop closure).

A device must be able to submit a fingerprint about itself and read whether its
plan was approved. It must never approve, nor touch another device's plans.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from server import create_app

ADMIN_TOKEN = "test-admin-token"


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(str(tmp_path / "control-plane.sqlite"), admin_token=ADMIN_TOKEN))


def admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def device_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def enroll(client: TestClient, device_id: str) -> str:
    code = client.post(
        "/api/v1/enrollment-codes",
        json={"device_id": device_id, "expires_in_seconds": 600},
        headers=admin_headers(),
    ).json()["code"]
    response = client.post("/api/v1/enroll", json={"code": code, "device_id": device_id})
    assert response.status_code == 200
    return str(response.json()["report_token"])


def test_device_token_can_create_and_read_own_plan(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    token = enroll(client, "device-a")
    created = client.post(
        "/api/v1/devices/device-a/plans",
        json={"desired_commit": "abc", "current_hash": "def", "expiry_hours": 24},
        headers=device_headers(token),
    )
    assert created.status_code == 200
    assert created.json()["approval_status"] == "pending"
    plan_id = created.json()["id"]

    got = client.get(f"/api/v1/devices/device-a/plans/{plan_id}", headers=device_headers(token))
    assert got.status_code == 200
    body = got.json()
    assert body["approval_status"] == "pending"
    assert body["current_hash"] == "def"
    assert body["desired_commit"] == "abc"
    assert set(body) == {"approval_status", "current_hash", "desired_commit", "expiry_timestamp"}


def test_device_token_cannot_create_for_another_device(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    token = enroll(client, "device-a")
    response = client.post(
        "/api/v1/devices/device-b/plans",
        json={"desired_commit": "abc", "current_hash": "def"},
        headers=device_headers(token),
    )
    assert response.status_code in (401, 403)


def test_device_token_cannot_read_another_devices_plan(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    token = enroll(client, "device-a")
    created = client.post(
        "/api/v1/devices/device-b/plans",
        json={"desired_commit": "abc", "current_hash": "def"},
        headers=admin_headers(),
    ).json()
    response = client.get(f"/api/v1/devices/device-b/plans/{created['id']}", headers=device_headers(token))
    assert response.status_code in (401, 403)


def test_report_token_cannot_approve(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    token = enroll(client, "device-a")
    created = client.post(
        "/api/v1/devices/device-a/plans",
        json={"desired_commit": "abc", "current_hash": "def"},
        headers=device_headers(token),
    ).json()
    response = client.post(
        f"/api/v1/devices/device-a/plans/{created['id']}/approve",
        json={"current_hash": "def", "desired_commit": "abc"},
        headers=device_headers(token),
    )
    assert response.status_code in (401, 403)


def test_admin_still_creates_and_reads(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post(
        "/api/v1/devices/device-a/plans",
        json={"desired_commit": "abc", "current_hash": "def"},
        headers=admin_headers(),
    )
    assert created.status_code == 200
    got = client.get(
        f"/api/v1/devices/device-a/plans/{created.json()['id']}",
        headers=admin_headers(),
    )
    assert got.status_code == 200
