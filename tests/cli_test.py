"""
Tests for the holdfastctl CLI doctor command.
"""

import json

from typer.testing import CliRunner

from holdfastctl.cli import app

runner = CliRunner()


class TestValidateCommand:
    """The validate command must be reachable from the main CLI.

    validate.py defines the command on its own Typer app; if that app is never
    mounted, the validator exists but can never be run.
    """

    def test_validate_appears_in_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "validate" in result.output

    def test_validate_accepts_the_repo_manifests(self):
        result = runner.invoke(app, ["validate", "manifests"])
        assert result.exit_code == 0, result.output

    def test_validate_rejects_a_malformed_catalog(self, tmp_path):
        catalogs = tmp_path / "catalogs"
        catalogs.mkdir()
        (catalogs / "credentials.yaml").write_text(
            "mcp-servers:\n  - id: x\n    url: http://y\n    bogus_field: boom\n"
        )
        result = runner.invoke(app, ["validate", str(tmp_path)])
        assert result.exit_code == 1, result.output
        assert "validation" in result.output.lower()


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


class TestEnrollCodeCommand:
    """The operator enroll-code command mints a device-bound code with gateway scope."""

    def test_requires_admin_token_env(self, monkeypatch):
        from typer.testing import CliRunner

        from holdfastctl.cli import app

        monkeypatch.delenv("HOLDFAST_ADMIN_TOKEN", raising=False)
        result = CliRunner().invoke(app, ["enroll-code", "device-a"])
        assert result.exit_code == 1
        assert "HOLDFAST_ADMIN_TOKEN" in result.output

    def test_mints_code_with_scope(self, monkeypatch):
        from unittest.mock import patch

        from typer.testing import CliRunner

        from holdfastctl.cli import app

        monkeypatch.setenv("HOLDFAST_ADMIN_TOKEN", "admin-token")
        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"code": "one-time-code", "device_id": "device-a"}
            result = CliRunner().invoke(
                app,
                [
                    "enroll-code", "device-a",
                    "--models", "or-cheap,or-opus",
                    "--mcp", "github",
                    "--control-plane", "http://cp:8000",
                ],
            )
        assert result.exit_code == 0
        assert "one-time-code" in result.output
        request = mock_post.call_args
        assert request.args[0] == "http://cp:8000/api/v1/enrollment-codes"
        assert request.kwargs["headers"] == {"Authorization": "Bearer admin-token"}
        assert request.kwargs["json"]["gateway_models"] == ["or-cheap", "or-opus"]
        assert request.kwargs["json"]["gateway_mcp_servers"] == ["github"]
