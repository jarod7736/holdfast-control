"""
Tests for the WSL agent install assets and the `report` command.
"""

import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from holdfastctl.cli import app

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parent.parent


class TestSystemdUnits:
    """The systemd user units are present and contain the required directives."""

    def test_units_exist(self):
        service = REPO_ROOT / "templates/systemd/holdfastctl-agent.service"
        timer = REPO_ROOT / "templates/systemd/holdfastctl-agent.timer"
        assert service.is_file(), "holdfastctl-agent.service missing"
        assert timer.is_file(), "holdfastctl-agent.timer missing"

    def test_service_oneshot_and_execstart(self):
        text = (REPO_ROOT / "templates/systemd/holdfastctl-agent.service").read_text()
        assert "Type=oneshot" in text
        assert "ExecStart=%h/.local/bin/holdfastctl report" in text
        assert "User=" not in text, "user units must not hardcode User="

    def test_timer_schedule(self):
        text = (REPO_ROOT / "templates/systemd/holdfastctl-agent.timer").read_text()
        assert "OnCalendar=" in text
        assert "Persistent=true" in text
        assert "Unit=holdfastctl-agent.service" in text


class TestInstallScript:
    """The WSL install script exists, is executable, and fails fast."""

    def test_script_exists_and_executable(self):
        script = REPO_ROOT / "scripts/install-agent.sh"
        assert script.is_file(), "scripts/install-agent.sh missing"
        assert os.access(script, os.X_OK), "install script must be executable"

    def test_script_is_safe(self):
        text = (REPO_ROOT / "scripts/install-agent.sh").read_text()
        assert "set -euo pipefail" in text
        assert "holdfastctl-agent.timer" in text
        assert "systemctl --user" in text

    def test_shellcheck_smoke(self):
        if shutil.which("shellcheck") is None:
            pytest.skip("shellcheck not installed")
        result = os.system("shellcheck scripts/install-agent.sh")
        assert result == 0, "shellcheck reported issues"


class TestReportCommand:
    """The `report` command inspects and posts status, or fails cleanly."""

    def _write_config(self, tmp_path, device_id="current-wsl", url="http://cp.test"):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(f"device_id: {device_id}\ncontrol_plane_url: {url}\nschedule: 15m\n")
        return cfg

    def test_missing_config(self, tmp_path):
        result = runner.invoke(app, ["report", "--config", str(tmp_path / "nope.yaml")])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_report_success(self, tmp_path):
        cfg = self._write_config(tmp_path)
        with patch("holdfastctl.reporting.StatusReporter.report_status", return_value=True):
            result = runner.invoke(app, ["report", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "Reported current-wsl" in result.output

    def test_report_failure_clean_exit(self, tmp_path):
        cfg = self._write_config(tmp_path)
        from holdfastctl.reporting import ReportingError

        with patch(
            "holdfastctl.reporting.StatusReporter.report_status",
            side_effect=ReportingError("boom"),
        ):
            result = runner.invoke(app, ["report", "--config", str(cfg)])
        assert result.exit_code == 1
        assert "failed" in result.output
        assert "Traceback" not in result.output
