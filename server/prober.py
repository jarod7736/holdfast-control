"""Lightweight HTTP probes for integration health rows."""

import json
import os
import time
from urllib import request
from urllib.error import URLError

from server.storage import connection

_DEVICE_FRESHNESS_SECONDS = 1800


def _parse_probes_env() -> dict[str, str]:
    """Parse HOLDFAST_INTEGRATION_PROBES env var JSON into {adapter_name: url}."""
    raw = os.environ.get("HOLDFAST_INTEGRATION_PROBES", "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _http_get(url: str, timeout: float = 3.0) -> tuple[bool, str]:
    """Perform an HTTP GET and return (is_ok, detail_string)."""
    try:
        req = request.Request(url, method="GET")
        resp = request.urlopen(req, timeout=timeout)
        code = resp.getcode()
        if 200 <= code < 400:
            return True, f"HTTP {code}"
        return False, f"HTTP {code}"
    except TimeoutError:
        return False, "request timed out"
    except URLError as exc:
        reason = str(exc.reason) if exc.reason else str(exc)
        return False, reason
    except Exception as exc:
        return False, str(exc)


def refresh_integrations(database_path: str, ttl_seconds: int = 120) -> None:
    """Probe all kind='integration' rows and update those that are stale.

    Probe rules (in priority order):
      1. URL probe from HOLDFAST_INTEGRATION_PROBES env var.
      2. litellm fallback from HOLDFAST_LITELLM_URL env var.
      3. Device-freshness: latest report age < 1800s → ok.
      4. Default → status stays 'unknown', detail "no probe configured".

    NEVER raises.
    """
    probes_map = _parse_probes_env()
    litellm_url = os.environ.get("HOLDFAST_LITELLM_URL", "")
    now = time.time()

    with connection(database_path) as conn:
        rows = conn.execute(
            "SELECT adapter, status, detail, last_checked_at FROM adapter_health WHERE kind = ?",
            ("integration",),
        ).fetchall()

        for row in rows:
            adapter = row["adapter"]
            last_checked = row["last_checked_at"]

            # Skip if within TTL
            if last_checked is not None and (now - last_checked) < ttl_seconds:
                continue

            new_status = "unknown"
            new_detail = "no probe configured"

            # Rule a: URL probe
            if adapter in probes_map:
                url = probes_map[adapter]
                ok, detail = _http_get(url)
                new_status = "ok" if ok else "error"
                new_detail = detail
            # Rule b: litellm fallback (only for the litellm adapter itself)
            elif adapter == "litellm" and litellm_url:
                health_url = f"{litellm_url.rstrip('/')}/health/liveliness"
                ok, detail = _http_get(health_url)
                new_status = "ok" if ok else "error"
                new_detail = detail
            # Rule c: device-freshness
            else:
                report = conn.execute(
                    "SELECT created_at FROM device_reports "
                    "WHERE device_id = ? ORDER BY created_at DESC LIMIT 1",
                    (adapter,),
                ).fetchone()
                if report is not None:
                    age = now - report["created_at"]
                    if age < _DEVICE_FRESHNESS_SECONDS:
                        new_status = "ok"
                        new_detail = f"last report {int(age)}s ago"
                    else:
                        new_status = "error"
                        new_detail = f"no report for {int(age)}s"
                # else: stays unknown with "no probe configured"

            new_checked = time.time()
            try:
                conn.execute(
                    "UPDATE adapter_health SET status = ?, detail = ?, last_checked_at = ? "
                    "WHERE adapter = ? AND kind = ?",
                    (new_status, new_detail, new_checked, adapter, "integration"),
                )
            except Exception:
                pass  # safety net — never break the endpoint
