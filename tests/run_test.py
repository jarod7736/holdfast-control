"""
Tests for the holdfastctl run launcher (op run integration).
"""

from unittest.mock import patch

from typer.testing import CliRunner

from holdfastctl.cli import app

runner = CliRunner()


class _Proc:
    def __init__(self, rc: int = 0, out: str = "") -> None:
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


def _make_fake_run(op_version_rc=0, account_rc=0, launch_rc=0):
    """Return a subprocess.run fake recording calls."""
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        if cmd[0] == "op" and cmd[1] == "--version":
            return _Proc(op_version_rc, "2.38.1")
        if cmd[0] == "op" and cmd[1] == "account":
            return _Proc(account_rc, "{}")
        return _Proc(launch_rc)

    return fake_run, calls


class TestRunCommand:
    """Test cases for the run launcher."""

    def test_run_help(self):
        """Run command appears in the CLI help."""
        result = runner.invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        assert "opcode" in result.output or "tool" in result.output

    def test_unsupported_tool_rejected(self):
        """Only 'opencode' is supported."""
        result = runner.invoke(app, ["run", "something-else"])
        assert result.exit_code == 1
        assert "unsupported tool" in result.output

    def test_op_binary_missing(self):
        """Missing op binary exits 1 with a clear error."""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = runner.invoke(app, ["run", "opencode"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_account_integration_unavailable(self):
        """Signed-out/integration-unavailable state exits 1 and does not launch."""
        fake_run, calls = _make_fake_run(op_version_rc=0, account_rc=1)
        with patch("subprocess.run", side_effect=fake_run):
            result = runner.invoke(app, ["run", "opencode"])
        assert result.exit_code == 1
        assert "desktop integration unavailable" in result.output
        assert all(cmd[1] != "run" for cmd in calls), "opencode must not be launched"

    def test_launch_through_op_run(self):
        """Successful pre-checks launch `op run -- opencode` with passthrough args."""
        fake_run, calls = _make_fake_run(op_version_rc=0, account_rc=0, launch_rc=0)
        with patch("subprocess.run", side_effect=fake_run):
            result = runner.invoke(app, ["run", "opencode", "somefile", "--flag"])
        assert result.exit_code == 0
        launch = [cmd for cmd in calls if cmd[0] == "op" and cmd[1] == "run"]
        assert len(launch) == 1
        assert launch[0] == ["op", "run", "--", "opencode", "somefile", "--flag"]

    def test_exit_code_propagated(self):
        """The launched process exit code is propagated."""
        fake_run, _ = _make_fake_run(op_version_rc=0, account_rc=0, launch_rc=7)
        with patch("subprocess.run", side_effect=fake_run):
            result = runner.invoke(app, ["run", "opencode"])
        assert result.exit_code == 7
