"""Status adapters for the Holdfast control plane."""

from pathlib import Path
from typing import Any

from server.storage import connection

_CAPABILITY_KIND = "capability"
_INTEGRATION_KIND = "integration"


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
