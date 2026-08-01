"""
Tests for the holdfastctl CLI doctor command.
"""

import json

from typer.testing import CliRunner

from holdfastctl.cli import app

runner = CliRunner()


class TestDoctorCommand:
    """Test cases for the doctor command."""

    def test_doctor_help(self):
        """Doctor command appears in the CLI help."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "doctor" in result.output

    def test_doctor_runs_healthy(self):
        """Doctor exits 0 when no critical issues are found."""
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "System Diagnostics" in result.output

    def test_doctor_json_output(self):
        """Doctor supports --json output with a parseable structure."""
        result = runner.invoke(app, ["doctor", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "healthy" in data
        assert "checks" in data
        # Core diagnostic categories are always present
        for key in ["os", "opencode", "onepassword", "git", "ssh_agent", "agent"]:
            assert key in data["checks"], f"missing check category: {key}"

    def test_doctor_os_detection(self):
        """OS detection reports the running platform."""
        import platform

        result = runner.invoke(app, ["doctor", "--json"])
        data = json.loads(result.output)
        assert data["checks"]["os"]["system"].lower() == platform.system().lower()
