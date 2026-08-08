"""Status adapters for the Holdfast control plane."""

import json
import sqlite3
from pathlib import Path
from typing import Any

from server.storage import connection

_CAPABILITY_KIND = "capability"
_INTEGRATION_KIND = "integration"

_STATUS_PRIORITY = {"ok": 0, "unknown": 1, "warning": 2, "error": 3}

_CAPABILITY_NAMES = ("opencode", "providers", "mcp_servers", "skills")


def capabilities_status(database_path: str) -> list[dict[str, Any]]:
    with connection(database_path) as conn:
        rows = conn.execute(
            "SELECT adapter, status, detail, last_checked_at FROM adapter_health WHERE kind = ? ORDER BY adapter",
            (_CAPABILITY_KIND,),
        ).fetchall()
    return [dict(row) for row in rows]


def integrations_status(database_path: str) -> list[dict[str, Any]]:
    with connection(database_path) as conn:
        rows = conn.execute(
            "SELECT adapter, status, detail, last_checked_at FROM adapter_health WHERE kind = ? ORDER BY adapter",
            (_INTEGRATION_KIND,),
        ).fetchall()
    return [dict(row) for row in rows]


def update_capability_health(conn: "sqlite3.Connection") -> None:
    """Recompute kind='capability' rows from the latest report of each device.

    Reads the *latest* device_reports row per device (grouped by device_id,
    ordered by created_at DESC LIMIT 1 per device), parses the "checks"
    mapping, and aggregates worst-wins across all devices.

    Rows are updated in-place.  Devices with no "checks" key in their
    latest report are silently skipped.  If *no* device reports checks the
    capability rows are left untouched.
    """
    # Step 1: get the latest report per device
    rows = conn.execute(
        "SELECT device_id, report_data, created_at FROM device_reports "
        "ORDER BY device_id, created_at DESC"
    ).fetchall()

    # Step 2: keep only the latest per device
    latest_per_device: dict[str, dict[str, Any]] = {}
    for row in rows:
        did = row["device_id"]
        if did not in latest_per_device:
            latest_per_device[did] = dict(row)

    if not latest_per_device:
        return  # no reports at all — nothing to do

    # Step 3: parse checks from each device's latest report
    # Each entry: capability_name -> list of (status, detail, device_id, created_at)
    agg: dict[str, list[tuple[str, str, str, float]]] = {name: [] for name in _CAPABILITY_NAMES}
    has_any_checks = False

    for dev in latest_per_device.values():
        try:
            rd = json.loads(dev["report_data"])
        except (json.JSONDecodeError, TypeError):
            continue
        checks = rd.get("checks")
        if not isinstance(checks, dict):
            continue
        for cap_name, info in checks.items():
            if cap_name not in agg:
                continue
            has_any_checks = True
            if isinstance(info, dict):
                status_val = info.get("status", "unknown")
                detail_val = info.get("detail", "")
            else:
                status_val = str(info)
                detail_val = ""
            agg[cap_name].append((status_val, detail_val, dev["device_id"], dev["created_at"]))

    if not has_any_checks:
        return  # no device has checks — leave rows untouched

    # Step 4: worst-wins + detail construction per capability
    for cap_name, entries in agg.items():
        if not entries:
            continue

        worst_status = max(entries, key=lambda e: _STATUS_PRIORITY.get(e[0], 1))[0]

        # Build detail: "device-a: ok; device-b: error — reason"
        parts: list[str] = []
        for status_val, detail_val, device_id, created_at in entries:
            part = f"{device_id}: {status_val}"
            if detail_val and status_val != "ok":
                part += f" — {detail_val}"
            parts.append(part)
        detail_str = "; ".join(parts)
        if len(detail_str) > 200:
            detail_str = detail_str[:200]

        # last_checked_at = max created_at of contributing reports
        last_checked_at = max(e[3] for e in entries)

        conn.execute(
            "UPDATE adapter_health SET status = ?, detail = ?, last_checked_at = ? "
            "WHERE adapter = ? AND kind = ?",
            (worst_status, detail_str, last_checked_at, cap_name, _CAPABILITY_KIND),
        )


def credentials_status(database_path: str) -> list[dict[str, Any]]:
    with connection(database_path) as conn:
        rows = conn.execute(
            "SELECT id, owner, purpose, environment, device, service, created_at, rotated_at, rotation_days, "
            "managed_by, credential_type, recovery_required FROM credential_metadata ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


def docs_status() -> list[dict[str, Any]]:
    docs_dir = Path(__file__).resolve().parents[2] / "docs"
    if not docs_dir.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for path in sorted(docs_dir.glob("*.md")):
        results.append({"doc_path": path.name, "modified_at": path.stat().st_mtime, "status": "present"})
    return results
