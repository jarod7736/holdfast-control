"""Capability checks that holdfastctl agents run during reporting.

Each check probes local state and returns {"status": ..., "detail": ...}.
Exactly four capability keys: opencode, providers, mcp_servers, skills.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


def _count_config_entries(opencode_config_dir: Path, key: str) -> tuple[int, bool, bool]:
    """Count entries under ``key`` in opencode.json.

    Returns (count, file_found, parseable): file_found is False when the file
    cannot be read at all; parseable is False when it exists but is not valid JSON.
    """
    config_file = opencode_config_dir / "opencode.json"
    try:
        raw = config_file.read_text(encoding="utf-8")
    except OSError:
        return (0, False, False)

    try:
        cfg: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        return (0, True, False)

    entries = cfg.get(key) or {}
    count = len(entries) if isinstance(entries, dict) else 0
    return (count, True, True)


def _count_provider_entries(opencode_config_dir: Path) -> tuple[int, bool, bool]:
    """Count provider entries in opencode.json under the ``provider`` key."""
    return _count_config_entries(opencode_config_dir, "provider")


def _count_mcp_entries(opencode_config_dir: Path) -> tuple[int, bool, bool]:
    """Count MCP server entries in opencode.json under the ``mcp`` key."""
    return _count_config_entries(opencode_config_dir, "mcp")


def _count_skill_dirs() -> int:
    """Count skill directories across ``~/.config/opencode/skills/`` and ``~/.agents/skills/``.

    Only counts directories (not files). Returns 0 when both dirs are missing.
    """
    total = 0
    for base in (
        Path.home() / ".config" / "opencode" / "skills",
        Path.home() / ".agents" / "skills",
    ):
        if base.is_dir():
            total += len([p for p in base.iterdir() if p.is_dir()])
    return total


def _find_opencode_binary() -> str | None:
    """Locate the opencode binary: PATH first, then well-known user locations.

    systemd user timers and cron run with a sparse PATH, so probe the
    standard install dirs as a fallback.
    """
    binary = shutil.which("opencode")
    if binary:
        return binary
    for candidate in (
        Path.home() / ".opencode" / "bin" / "opencode",
        Path.home() / ".local" / "bin" / "opencode",
    ):
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        except OSError:
            continue
    return None


def run_checks(
    opencode_config_dir: Path | None = None,
) -> dict[str, dict[str, str]]:
    """Run capability checks and return results.

    Returns a dict mapping capability name to ``{"status": <str>, "detail": <str>}``.
    Exactly four keys: **opencode**, **providers**, **mcp_servers**, **skills**.

    Guarantees: **never raises** — every check is individually wrapped.
    """
    if opencode_config_dir is None:
        opencode_config_dir = Path.home() / ".config" / "opencode"

    results: dict[str, dict[str, str]] = {
        "opencode": {"status": "ok", "detail": "ok"},
        "providers": {"status": "ok", "detail": "ok"},
        "mcp_servers": {"status": "ok", "detail": "ok"},
        "skills": {"status": "ok", "detail": "ok"},
    }

    # ---- opencode binary check ----
    try:
        binary = _find_opencode_binary()
        if binary is None:
            results["opencode"] = {"status": "error", "detail": "opencode not on PATH"}
        else:
            proc = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if proc.returncode == 0:
                ver = proc.stdout.strip()
                if len(ver) > 70:
                    ver = ver[:70]
                results["opencode"] = {"status": "ok", "detail": f"opencode {ver}"}
            else:
                err_detail = (proc.stderr.strip() or f"exit code {proc.returncode}")[:70]
                results["opencode"] = {"status": "warning", "detail": f"opencode check failed: {err_detail}"}
    except subprocess.TimeoutExpired:
        results["opencode"] = {"status": "warning", "detail": "opencode --version timed out"}
    except Exception as exc:
        d = str(exc)
        if len(d) > 70:
            d = d[:70]
        results["opencode"] = {"status": "error", "detail": d}

    # ---- providers check ----
    try:
        count, file_found, parseable = _count_provider_entries(opencode_config_dir)
        if not file_found:
            results["providers"] = {"status": "error", "detail": "opencode.json missing"}
        elif not parseable:
            results["providers"] = {"status": "error", "detail": "opencode.json unparseable"}
        elif count == 0:
            results["providers"] = {"status": "warning", "detail": "no providers configured"}
        else:
            results["providers"] = {"status": "ok", "detail": f"{count} provider(s) configured"}
    except Exception as exc:
        d = str(exc)
        if len(d) > 70:
            d = d[:70]
        results["providers"] = {"status": "error", "detail": d}

    # ---- mcp_servers check ----
    try:
        count, file_found, parseable = _count_mcp_entries(opencode_config_dir)
        if not file_found:
            results["mcp_servers"] = {"status": "error", "detail": "opencode.json missing"}
        elif not parseable:
            results["mcp_servers"] = {"status": "error", "detail": "opencode.json unparseable"}
        elif count == 0:
            results["mcp_servers"] = {"status": "warning", "detail": "no mcp servers configured"}
        else:
            results["mcp_servers"] = {"status": "ok", "detail": f"{count} mcp server(s) configured"}
    except Exception as exc:
        d = str(exc)
        if len(d) > 70:
            d = d[:70]
        results["mcp_servers"] = {"status": "error", "detail": d}

    # ---- skills check ----
    try:
        count = _count_skill_dirs()
        if count == 0:
            results["skills"] = {"status": "warning", "detail": "no skill dirs found"}
        else:
            results["skills"] = {"status": "ok", "detail": f"{count} skill dir(s) found"}
    except Exception as exc:
        d = str(exc)
        if len(d) > 70:
            d = d[:70]
        results["skills"] = {"status": "error", "detail": d}

    return results
