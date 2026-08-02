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

    def test_report_error_still_prints_minted_key(self, tmp_path):
        """A key minted during enrollment must not be lost if the report POST then fails."""
        cfg = self._write_config(tmp_path)
        from holdfastctl.reporting import ReportingError

        def fake_report_status(self, status_data, enrollment_code=None):
            self.pending_gateway_key = "sk-minted-key"
            self.pending_gateway_key_alias = "holdfast-current-wsl-aa11"
            raise ReportingError("report POST failed")

        with patch("holdfastctl.reporting.StatusReporter.report_status", new=fake_report_status):
            result = runner.invoke(app, ["report", "--config", str(cfg)])
        assert result.exit_code == 1
        assert "sk-minted-key" in result.output
        assert "holdfast-current-wsl-aa11" in result.output

    @patch("requests.post")
    def test_minted_key_survives_failed_report_post(self, mock_post, tmp_path):
        """End-to-end: enroll mints a key over real report_status/requests.post, the
        report POST 500s, and the key still reaches the operator instead of being lost."""
        cfg = self._write_config(tmp_path)

        def responses(url, **kwargs):
            reply = type("R", (), {})()
            if url.endswith("/api/v1/enroll"):
                reply.status_code = 200
                reply.json = lambda: {
                    "report_token": "fresh-token",
                    "gateway_key": "sk-minted-key",
                    "gateway_key_alias": "holdfast-current-wsl-aa11",
                }
            else:
                reply.status_code = 500
                reply.text = "report rejected"
                reply.json = lambda: {"error": "report rejected"}
            return reply

        mock_post.side_effect = responses
        result = runner.invoke(app, ["report", "--config", str(cfg), "--enrollment-code", "code-123"])
        assert result.exit_code == 1
        assert "sk-minted-key" in result.output
        assert "holdfast-current-wsl-aa11" in result.output

    def test_report_rejected_still_prints_minted_key(self, tmp_path):
        """A key minted during enrollment must not be lost if the control plane rejects the report."""
        cfg = self._write_config(tmp_path)

        def fake_report_status(self, status_data, enrollment_code=None):
            self.pending_gateway_key = "sk-minted-key"
            self.pending_gateway_key_alias = "holdfast-current-wsl-aa11"
            return False

        with patch("holdfastctl.reporting.StatusReporter.report_status", new=fake_report_status):
            result = runner.invoke(app, ["report", "--config", str(cfg)])
        assert result.exit_code == 1
        assert "sk-minted-key" in result.output
        assert "holdfast-current-wsl-aa11" in result.output
