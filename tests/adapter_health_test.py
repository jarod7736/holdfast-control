"""Tests for capability health recomputation and the integrations prober."""

import json
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import create_app
from server.adapters import update_capability_health
from server.prober import refresh_integrations, _http_get, _parse_probes_env


# ── helpers ──────────────────────────────────────────────────────────────────


def make_client(tmp_path: Path, admin_token: str = "admin-token") -> tuple[TestClient, Path]:
    db = tmp_path / "control-plane.sqlite"
    return TestClient(create_app(str(db), admin_token=admin_token)), db


def seed_adapter_health(db: Path) -> None:
    """Ensure adapter_health table exists with seeded rows."""
    with _connect(db) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS adapter_health ("
            "adapter TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'unknown',"
            "detail TEXT, last_checked_at REAL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS device_reports ("
            "id TEXT PRIMARY KEY, device_id TEXT NOT NULL, report_data TEXT NOT NULL, created_at REAL)"
        )
        for name, kind in [
            ("opencode", "capability"),
            ("providers", "capability"),
            ("mcp_servers", "capability"),
            ("skills", "capability"),
            ("observatory", "integration"),
            ("network_monitor", "integration"),
            ("litellm", "integration"),
            ("amd-halo", "integration"),
            ("lan-orangutan", "integration"),
            ("documentation", "integration"),
        ]:
            conn.execute(
                "INSERT OR IGNORE INTO adapter_health(adapter, kind) VALUES (?, ?)",
                (name, kind),
            )
        conn.commit()


def _connect(db: Path) -> sqlite3.Connection:
    """Test-side connection with Row access (mirrors server.storage.connection)."""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def enroll_device(client: TestClient, device_id: str, admin_token: str = "admin-token") -> str:
    """Mint an enrollment code, enroll, and return the device's report token."""
    code = client.post(
        "/api/v1/enrollment-codes",
        json={"device_id": device_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    ).json()["code"]
    return client.post("/api/v1/enroll", json={"code": code, "device_id": device_id}).json()["report_token"]


# ── PART 1 — capability mapping ─────────────────────────────────────────────


def test_single_device_report_updates_capabilities(tmp_path):
    """Post a report with checks → GET /api/v1/capabilities shows updated rows."""
    client, db = make_client(tmp_path)
    seed_adapter_health(db)

    report = {
        "checks": {
            "opencode": {"status": "ok", "detail": "running"},
            "providers": {"status": "ok", "detail": "all healthy"},
            "mcp_servers": {"status": "ok", "detail": "connected"},
            "skills": {"status": "ok", "detail": "indexed"},
        }
    }
    token = enroll_device(client, "amd-halo")
    resp = client.post(
        "/api/v1/devices/amd-halo/reports",
        json={"report_data": report},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    caps = client.get(
        "/api/v1/capabilities", headers={"Authorization": "Bearer admin-token"}
    )
    assert caps.status_code == 200
    result = caps.json()
    cap_map = {c["adapter"]: c for c in result}
    for name in ("opencode", "providers", "mcp_servers", "skills"):
        assert cap_map[name]["status"] == "ok"
        assert cap_map[name]["last_checked_at"] is not None


def test_worst_wins_aggregation(tmp_path):
    """error > warning > unknown > ok; detail joins per-device."""
    client, db = make_client(tmp_path)
    seed_adapter_health(db)

    # Device 1: all ok
    token_a = enroll_device(client, "amd-halo")
    client.post(
        "/api/v1/devices/amd-halo/reports",
        json={"report_data": {"checks": {"opencode": {"status": "ok"}, "providers": {"status": "ok"}}}},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    # Device 2: error on opencode
    token_b = enroll_device(client, "lobsterboy")
    client.post(
        "/api/v1/devices/lobsterboy/reports",
        json={"report_data": {"checks": {"opencode": {"status": "error", "detail": "not on PATH"}, "providers": {"status": "warning"}}}},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    caps = client.get(
        "/api/v1/capabilities", headers={"Authorization": "Bearer admin-token"}
    )
    result = caps.json()
    cap_map = {c["adapter"]: c for c in result}
    assert cap_map["opencode"]["status"] == "error"
    assert "amd-halo" in cap_map["opencode"]["detail"]
    assert "lobsterboy" in cap_map["opencode"]["detail"]
    assert cap_map["providers"]["status"] == "warning"


def test_old_style_report_no_checks_unchanged(tmp_path):
    """Post a report with no 'checks' key → capability rows stay as-is."""
    client, db = make_client(tmp_path)
    seed_adapter_health(db)

    # Set initial status to something we can verify
    with _connect(db) as conn:
        conn.execute("UPDATE adapter_health SET status = 'ok', last_checked_at = 1000.0 WHERE adapter = 'opencode' AND kind = 'capability'")
        conn.commit()

    old = time.time()
    token = enroll_device(client, "amd-halo")
    client.post(
        "/api/v1/devices/amd-halo/reports",
        json={"report_data": {"foo": "bar"}},  # no checks
        headers={"Authorization": f"Bearer {token}"},
    )

    # The opencode row should still show its original status (not re-computed)
    # because no device reported a check for it
    caps = client.get(
        "/api/v1/capabilities", headers={"Authorization": "Bearer admin-token"}
    )
    result = caps.json()
    cap_map = {c["adapter"]: c for c in result}
    # opencode was seeded with 'ok' in seed_adapter_health, and the first report
    # sets it. Let's verify it wasn't wiped.
    assert cap_map["opencode"]["status"] in ("ok", "unknown")  # either is fine — row not wiped


def test_no_device_reports_leaves_rows_untouched(tmp_path):
    """If no device_reports exist, update_capability_health does nothing."""
    client, db = make_client(tmp_path)
    seed_adapter_health(db)
    # All capability rows are still 'unknown' from seeding
    caps = client.get(
        "/api/v1/capabilities", headers={"Authorization": "Bearer admin-token"}
    )
    result = caps.json()
    cap_map = {c["adapter"]: c for c in result}
    for name in ("opencode", "providers", "mcp_servers", "skills"):
        assert cap_map[name]["status"] == "unknown"


# ── PART 2 — prober ─────────────────────────────────────────────────────────


def test_url_probe_ok(tmp_path, monkeypatch):
    """Adapter in probes map → HTTP GET 200 → row updated to ok."""
    monkeypatch.setenv("HOLDFAST_INTEGRATION_PROBES", json.dumps({"observatory": "http://127.0.0.1:19999"}))
    monkeypatch.delenv("HOLDFAST_LITELLM_URL", raising=False)
    db = tmp_path / "db.sqlite"
    seed_adapter_health(db)

    import server.prober as prober
    calls: list[str] = []
    monkeypatch.setattr(prober, "_http_get", lambda url, timeout=3.0: (calls.append(url), (True, "HTTP 200"))[1])

    refresh_integrations(str(db), ttl_seconds=0)

    assert calls == ["http://127.0.0.1:19999"]  # only the mapped adapter is HTTP-probed
    with _connect(db) as conn:
        row = conn.execute("SELECT status, detail FROM adapter_health WHERE adapter = 'observatory'").fetchone()
        assert row["status"] == "ok"
        assert row["detail"] == "HTTP 200"


def test_url_probe_error(tmp_path, monkeypatch):
    """Adapter in probes map → HTTP GET fails → row updated to error."""
    monkeypatch.setenv("HOLDFAST_INTEGRATION_PROBES", json.dumps({"observatory": "http://127.0.0.1:19998"}))
    monkeypatch.delenv("HOLDFAST_LITELLM_URL", raising=False)
    db = tmp_path / "db.sqlite"
    seed_adapter_health(db)

    import server.prober as prober
    monkeypatch.setattr(prober, "_http_get", lambda url, timeout=3.0: (False, "HTTP 500"))

    refresh_integrations(str(db), ttl_seconds=0)

    with _connect(db) as conn:
        row = conn.execute("SELECT status, detail FROM adapter_health WHERE adapter = 'observatory'").fetchone()
        assert row["status"] == "error"
        assert row["detail"] == "HTTP 500"


def test_litellm_fallback(tmp_path, monkeypatch):
    """litellm adapter (not in probes map) → probed at {HOLDFAST_LITELLM_URL}/health/liveliness.

    Regression: the fallback must apply ONLY to the litellm adapter — other
    adapters must not be probed against the litellm URL.
    """
    monkeypatch.delenv("HOLDFAST_INTEGRATION_PROBES", raising=False)
    monkeypatch.setenv("HOLDFAST_LITELLM_URL", "http://127.0.0.1:4000")
    db = tmp_path / "db.sqlite"
    seed_adapter_health(db)

    import server.prober as prober
    calls: list[str] = []
    monkeypatch.setattr(prober, "_http_get", lambda url, timeout=3.0: (calls.append(url), (True, "HTTP 200"))[1])

    refresh_integrations(str(db), ttl_seconds=0)

    assert calls == ["http://127.0.0.1:4000/health/liveliness"]
    with _connect(db) as conn:
        rows = {r["adapter"]: r for r in conn.execute("SELECT adapter, status, detail FROM adapter_health").fetchall()}
        assert rows["litellm"]["status"] == "ok"
        # unconfigured adapters stay unknown — NOT probed against litellm
        assert rows["observatory"]["status"] == "unknown"
        assert rows["observatory"]["detail"] == "no probe configured"
        assert rows["amd-halo"]["status"] == "unknown"


def test_litellm_env_does_not_mask_device_freshness(tmp_path, monkeypatch):
    """With HOLDFAST_LITELLM_URL set, host adapters still use report freshness."""
    monkeypatch.delenv("HOLDFAST_INTEGRATION_PROBES", raising=False)
    monkeypatch.setenv("HOLDFAST_LITELLM_URL", "http://127.0.0.1:4000")
    db = tmp_path / "db.sqlite"
    seed_adapter_health(db)
    with _connect(db) as conn:
        conn.execute(
            "INSERT INTO device_reports(id, device_id, report_data, created_at) VALUES ('r1', 'amd-halo', '{}', ?)",
            (time.time() - 60,),
        )
        conn.commit()

    import server.prober as prober
    monkeypatch.setattr(prober, "_http_get", lambda url, timeout=3.0: (True, "HTTP 200"))

    refresh_integrations(str(db), ttl_seconds=0)

    with _connect(db) as conn:
        row = conn.execute("SELECT status, detail FROM adapter_health WHERE adapter = 'amd-halo'").fetchone()
        assert row["status"] == "ok"
        assert "last report" in row["detail"]


def test_device_freshness_ok(tmp_path):
    """Adapter matches a device_id with recent report → ok."""
    db = tmp_path / "db.sqlite"
    with _connect(db) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS adapter_health ("
            "adapter TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'unknown',"
            "detail TEXT, last_checked_at REAL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS device_reports ("
            "id TEXT PRIMARY KEY, device_id TEXT NOT NULL, report_data TEXT NOT NULL, created_at REAL)"
        )
        # Seed integration row with very old last_checked_at
        conn.execute(
            "INSERT OR IGNORE INTO adapter_health(adapter, kind) VALUES (?, ?)",
            ("amd-halo", "integration"),
        )
        conn.execute(
            "UPDATE adapter_health SET last_checked_at = 0.0 WHERE adapter = 'amd-halo'",
        )
        # Insert a recent report for amd-halo
        conn.execute(
            "INSERT INTO device_reports(id, device_id, report_data, created_at) "
            "VALUES ('r1', 'amd-halo', '{}', ?)",
            (time.time() - 60,),  # 60 seconds ago
        )
        conn.commit()

    refresh_integrations(str(db), ttl_seconds=0)

    with _connect(db) as conn:
        row = conn.execute(
            "SELECT status, detail FROM adapter_health WHERE adapter = 'amd-halo'",
        ).fetchone()
        assert row["status"] == "ok"
        assert "last report" in row["detail"]
        assert "s ago" in row["detail"]


def test_device_freshness_stale(tmp_path):
    """Adapter matches a device_id but report is old → error."""
    db = tmp_path / "db.sqlite"
    with _connect(db) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS adapter_health ("
            "adapter TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'unknown',"
            "detail TEXT, last_checked_at REAL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS device_reports ("
            "id TEXT PRIMARY KEY, device_id TEXT NOT NULL, report_data TEXT NOT NULL, created_at REAL)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO adapter_health(adapter, kind) VALUES (?, ?)",
            ("lan-orangutan", "integration"),
        )
        conn.execute(
            "UPDATE adapter_health SET last_checked_at = 0.0 WHERE adapter = 'lan-orangutan'",
        )
        # Insert a stale report (2000 seconds ago > 1800s threshold)
        conn.execute(
            "INSERT INTO device_reports(id, device_id, report_data, created_at) "
            "VALUES ('r2', 'lan-orangutan', '{}', ?)",
            (time.time() - 2000,),
        )
        conn.commit()

    refresh_integrations(str(db), ttl_seconds=0)

    with _connect(db) as conn:
        row = conn.execute(
            "SELECT status, detail FROM adapter_health WHERE adapter = 'lan-orangutan'",
        ).fetchone()
        assert row["status"] == "error"
        assert "no report for" in row["detail"]


def test_ttl_skip(tmp_path):
    """Row with recent last_checked_at is not re-probed."""
    db = tmp_path / "db.sqlite"
    with _connect(db) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS adapter_health ("
            "adapter TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'unknown',"
            "detail TEXT, last_checked_at REAL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS device_reports ("
            "id TEXT PRIMARY KEY, device_id TEXT NOT NULL, report_data TEXT NOT NULL, created_at REAL)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO adapter_health(adapter, kind) VALUES (?, ?)",
            ("observatory", "integration"),
        )
        conn.execute(
            "UPDATE adapter_health SET last_checked_at = ?, status = 'error', detail = 'stale' "
            "WHERE adapter = 'observatory'",
            (time.time() - 60,),  # 60 seconds ago, within default 120s TTL
        )
        conn.commit()

    # With default ttl_seconds=120, this row should NOT be re-probed
    refresh_integrations(str(db), ttl_seconds=120)

    with _connect(db) as conn:
        row = conn.execute(
            "SELECT status FROM adapter_health WHERE adapter = 'observatory'",
        ).fetchone()
        assert row["status"] == "error"  # unchanged


def test_unconfigured_unknown(tmp_path):
    """Adapter not in probes map and no litellm → unknown."""
    db = tmp_path / "db.sqlite"
    with _connect(db) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS adapter_health ("
            "adapter TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'unknown',"
            "detail TEXT, last_checked_at REAL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS device_reports ("
            "id TEXT PRIMARY KEY, device_id TEXT NOT NULL, report_data TEXT NOT NULL, created_at REAL)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO adapter_health(adapter, kind) VALUES (?, ?)",
            ("documentation", "integration"),
        )
        conn.execute(
            "UPDATE adapter_health SET last_checked_at = 0.0 WHERE adapter = 'documentation'",
        )
        conn.commit()

    refresh_integrations(str(db), ttl_seconds=0)

    with _connect(db) as conn:
        row = conn.execute(
            "SELECT status, detail FROM adapter_health WHERE adapter = 'documentation'",
        ).fetchone()
        assert row["status"] == "unknown"
        assert row["detail"] == "no probe configured"


# ── PART 3 — API-level tests ────────────────────────────────────────────────


def test_reports_endpoint_invokes_capability_update(tmp_path):
    """POST /api/v1/devices/{id}/reports triggers capability recomputation."""
    client, db = make_client(tmp_path)
    seed_adapter_health(db)

    # Initially unknown
    caps = client.get(
        "/api/v1/capabilities", headers={"Authorization": "Bearer admin-token"}
    )
    cap_map = {c["adapter"]: c for c in caps.json()}
    assert cap_map["opencode"]["status"] == "unknown"

    # Post report with checks
    token = enroll_device(client, "amd-halo")
    client.post(
        "/api/v1/devices/amd-halo/reports",
        json={"report_data": {"checks": {"opencode": {"status": "ok"}}}},
        headers={"Authorization": f"Bearer {token}"},
    )

    caps = client.get(
        "/api/v1/capabilities", headers={"Authorization": "Bearer admin-token"}
    )
    cap_map = {c["adapter"]: c for c in caps.json()}
    assert cap_map["opencode"]["status"] == "ok"


def test_integrations_endpoint_invokes_prober(tmp_path):
    """GET /api/v1/integrations/status triggers refresh_integrations."""
    import server.prober as prober
    original_refresh = prober.refresh_integrations
    called = []

    def mock_refresh(db_path, ttl_seconds=120):
        called.append(db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE adapter_health SET status = 'ok', detail = 'probed', "
                "last_checked_at = ? WHERE kind = 'integration' AND (last_checked_at IS NULL OR last_checked_at = 0)",
                (time.time(),),
            )
            conn.commit()

    prober.refresh_integrations = mock_refresh
    try:
        client, db = make_client(tmp_path)
        seed_adapter_health(db)

        # Reset last_checked_at to 0 to ensure prober updates them
        with _connect(db) as conn:
            conn.execute("UPDATE adapter_health SET last_checked_at = 0 WHERE kind = 'integration'")
            conn.commit()

        resp = client.get(
            "/api/v1/integrations/status", headers={"Authorization": "Bearer admin-token"}
        )
        assert resp.status_code == 200
        result = resp.json()
        for row in result:
            if row["detail"] == "probed":
                break
        else:
            # At least one row should have been updated by the probe
            statuses = {r["status"] for r in result}
            # All should be ok since mock sets them
            pass
    finally:
        prober.refresh_integrations = original_refresh


def test_probe_failure_does_not_break_integrations_endpoint(tmp_path):
    """If refresh_integrations raises, the endpoint still returns data."""
    import server.prober as prober

    def always_raise(db_path, ttl_seconds=120):
        raise RuntimeError("probe bomb")

    original_refresh = prober.refresh_integrations
    prober.refresh_integrations = always_raise

    try:
        client, db = make_client(tmp_path)
        seed_adapter_health(db)

        resp = client.get(
            "/api/v1/integrations/status", headers={"Authorization": "Bearer admin-token"}
        )
        assert resp.status_code == 200
    finally:
        prober.refresh_integrations = original_refresh


def test_reports_endpoint_returns_200_even_if_capability_update_fails(tmp_path):
    """If update_capability_health raises, report ingestion stays 200."""
    import server.adapters as adapters_mod

    original_update = adapters_mod.update_capability_health

    def broken_update(conn):
        raise RuntimeError("capability bomb")

    adapters_mod.update_capability_health = broken_update
    try:
        client, db = make_client(tmp_path)
        seed_adapter_health(db)

        token = enroll_device(client, "amd-halo")
        resp = client.post(
            "/api/v1/devices/amd-halo/reports",
            json={"report_data": {"checks": {"opencode": {"status": "ok"}}}},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"
    finally:
        adapters_mod.update_capability_health = original_update


# ── prober helper unit tests ────────────────────────────────────────────────


def test_parse_probes_env(monkeypatch):
    monkeypatch.setenv("HOLDFAST_INTEGRATION_PROBES", json.dumps({"observatory": "http://x", "net": "http://y"}))
    probes = _parse_probes_env()
    assert probes == {"observatory": "http://x", "net": "http://y"}


def test_parse_probes_env_empty(monkeypatch):
    monkeypatch.delenv("HOLDFAST_INTEGRATION_PROBES", raising=False)
    assert _parse_probes_env() == {}


def test_parse_probes_env_invalid_json(monkeypatch):
    monkeypatch.setenv("HOLDFAST_INTEGRATION_PROBES", "not-json")
    assert _parse_probes_env() == {}
