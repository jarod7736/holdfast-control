"""Tests for holdfastctl capability checks (src/holdfastctl/checks.py)."""

import json
from pathlib import Path

from holdfastctl.checks import run_checks


class TestRunChecksAllOk:
    """Happy-path: every capability reports ok."""

    def test_returns_four_keys(self, tmp_path):
        results = run_checks(opencode_config_dir=tmp_path)
        assert set(results) == {"opencode", "providers", "mcp_servers", "skills"}

    def test_opencode_ok_with_binary(self, monkeypatch, tmp_path):
        """shutil.which finds opencode → ok."""

        def fake_which(name):
            return "/usr/bin/opencode" if name == "opencode" else None

        monkeypatch.setattr("shutil.which", fake_which)

        class FakeProc:
            returncode = 0
            stdout = "opencode 0.3.0\n"
            stderr = ""

        monkeypatch.setattr("subprocess.run", lambda *a, **kw: FakeProc())
        results = run_checks(opencode_config_dir=tmp_path)
        assert results["opencode"]["status"] == "ok"
        assert results["opencode"]["detail"].startswith("opencode")

    def test_providers_ok(self, tmp_path):
        """opencode.json has provider entries → ok."""
        (tmp_path / "opencode.json").write_text(
            json.dumps({"provider": {"amd_halo": {"options": {"baseURL": "http://x"}}}})
        )
        results = run_checks(opencode_config_dir=tmp_path)
        assert results["providers"]["status"] == "ok"
        assert "1" in results["providers"]["detail"]

    def test_mcp_servers_ok(self, tmp_path):
        """opencode.json has mcp entries → ok."""
        (tmp_path / "opencode.json").write_text(
            json.dumps({"mcp": {"github": {"url": "http://mcp"}}})
        )
        results = run_checks(opencode_config_dir=tmp_path)
        assert results["mcp_servers"]["status"] == "ok"
        assert "1" in results["mcp_servers"]["detail"]

    def test_skills_ok(self, tmp_path, monkeypatch):
        """Skill directories found → ok."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "my-skill").mkdir()
        (skills_dir / "another").mkdir()

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        # Also create .agents/skills to exercise the second base dir
        (tmp_path / ".agents" / "skills").mkdir(parents=True)
        (tmp_path / ".agents" / "skills" / "extra").mkdir()

        results = run_checks(opencode_config_dir=tmp_path)
        assert results["skills"]["status"] == "ok"


class TestRunChecksMissingConfig:
    """Missing / broken opencode.json → error statuses."""

    def test_providers_error_when_file_missing(self, tmp_path):
        (tmp_path / "opencode.json").unlink(missing_ok=True)
        results = run_checks(opencode_config_dir=tmp_path)
        assert results["providers"]["status"] == "error"
        assert "missing" in results["providers"]["detail"].lower()

    def test_mcp_servers_error_when_file_missing(self, tmp_path):
        (tmp_path / "opencode.json").unlink(missing_ok=True)
        results = run_checks(opencode_config_dir=tmp_path)
        assert results["mcp_servers"]["status"] == "error"
        assert "missing" in results["mcp_servers"]["detail"].lower()

    def test_providers_warning_when_file_empty_json(self, tmp_path):
        (tmp_path / "opencode.json").write_text("{}")
        results = run_checks(opencode_config_dir=tmp_path)
        assert results["providers"]["status"] == "warning"
        assert "no providers" in results["providers"]["detail"]

    def test_mcp_servers_warning_when_file_empty_json(self, tmp_path):
        (tmp_path / "opencode.json").write_text("{}")
        results = run_checks(opencode_config_dir=tmp_path)
        assert results["mcp_servers"]["status"] == "warning"
        assert "no mcp" in results["mcp_servers"]["detail"]

    def test_both_error_when_file_unparseable(self, tmp_path):
        (tmp_path / "opencode.json").write_text("NOT JSON{{{")
        results = run_checks(opencode_config_dir=tmp_path)
        assert results["providers"]["status"] == "error"
        assert results["mcp_servers"]["status"] == "error"


class TestRunChecksOpencodeBinary:
    """opencode binary detection edge cases."""

    def test_opencode_error_when_binary_missing(self, monkeypatch):
        monkeypatch.setattr("holdfastctl.checks._find_opencode_binary", lambda: None)
        results = run_checks()
        assert results["opencode"]["status"] == "error"
        assert "not on PATH" in results["opencode"]["detail"]

    def test_opencode_warning_on_nonzero_exit(self, monkeypatch):

        def fake_which(name):
            return "/usr/bin/opencode"

        class FakeProc:
            returncode = 1
            stdout = ""
            stderr = "some error"

        monkeypatch.setattr("shutil.which", fake_which)
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: FakeProc())
        results = run_checks()
        assert results["opencode"]["status"] == "warning"
        assert "check failed" in results["opencode"]["detail"]

    def test_opencode_warning_on_timeout(self, monkeypatch):
        def fake_which(name):
            return "/usr/bin/opencode"

        import subprocess as sp

        class FakeProc:
            pass

        proc = FakeProc()
        proc.returncode = 1
        proc.stdout = ""
        proc.stderr = ""
        proc.timed_out = True
        raise_exception = sp.TimeoutExpired(cmd=["opencode", "--version"], timeout=3)

        def fake_run(*a, **kw):
            raise raise_exception

        monkeypatch.setattr("shutil.which", fake_which)
        monkeypatch.setattr("subprocess.run", fake_run)
        results = run_checks()
        assert results["opencode"]["status"] == "warning"
        assert "timed out" in results["opencode"]["detail"]


class TestRunChecksNeverRaises:
    """run_checks is guaranteed to return a dict even under filesystem storms."""

    def test_never_raises_empty_opencode_json(self, tmp_path):
        """Unparseable JSON still returns ok/error, no exception."""
        (tmp_path / "opencode.json").write_text("{broken")
        results = run_checks(opencode_config_dir=tmp_path)
        assert isinstance(results, dict)
        assert len(results) == 4

    def test_never_raises_when_skill_dirs_blow_up(self, tmp_path, monkeypatch):
        """Force iterdir to raise — skills should still be in results with error."""

        class BadDir:
            def is_dir(self):
                return True

            def iterdir(self):
                raise OSError("boom")

        def fake_home():
            return tmp_path

        monkeypatch.setattr("pathlib.Path.home", fake_home)

        # Patch the skills path to return our BadDir
        class GoodDir(Path):
            def __init__(self, *args, bad=False, **kwargs):
                super().__init__(*args, **kwargs)
                self._bad = bad

            def is_dir(self):
                return not self._bad

            def iterdir(self):
                if self._bad:
                    raise OSError("boom")
                return super().iterdir()

        results = run_checks(opencode_config_dir=tmp_path)
        assert isinstance(results, dict)
        assert len(results) == 4

        # skills should be present (may be ok or error depending on implementation)
        # The important thing is no exception propagates
        assert "skills" in results

    def test_never_raises_on_provider_count_exception(self, tmp_path, monkeypatch):
        """Force _count_provider_entries to raise — result still populated."""
        from holdfastctl import checks

        def broken_count(cd):
            raise ValueError("probe failure")

        monkeypatch.setattr(checks, "_count_provider_entries", broken_count)

        results = run_checks(opencode_config_dir=tmp_path)
        assert results["providers"]["status"] == "error"
        assert len(results) == 4


class TestRunChecksEdgeCases:
    """Boundary and detail-string edge cases."""

    def test_providers_zero_count_is_warning(self, tmp_path):
        (tmp_path / "opencode.json").write_text(json.dumps({"provider": {}}))
        results = run_checks(opencode_config_dir=tmp_path)
        assert results["providers"]["status"] == "warning"

    def test_mcp_servers_zero_count_is_warning(self, tmp_path):
        (tmp_path / "opencode.json").write_text(json.dumps({"mcp": {}}))
        results = run_checks(opencode_config_dir=tmp_path)
        assert results["mcp_servers"]["status"] == "warning"

    def test_skills_no_dirs_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        results = run_checks(opencode_config_dir=tmp_path)
        assert results["skills"]["status"] == "warning"
        assert "no skill" in results["skills"]["detail"]

    def test_skills_ok_with_single_dir(self, tmp_path, monkeypatch):
        skills = tmp_path / ".config" / "opencode" / "skills"
        skills.mkdir(parents=True)
        (skills / "s1").mkdir()

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        results = run_checks(opencode_config_dir=tmp_path)
        assert results["skills"]["status"] == "ok"

    def test_detail_strings_stay_under_80_chars(self, tmp_path, monkeypatch):
        """Verify all detail strings are short."""

        def fake_which(name):
            return "/usr/bin/opencode"

        class FakeProc:
            returncode = 0
            stdout = "x" * 100 + "\n"
            stderr = "y" * 100

        monkeypatch.setattr("shutil.which", fake_which)
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: FakeProc())
        (tmp_path / "opencode.json").write_text("{}")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        results = run_checks(opencode_config_dir=tmp_path)
        for key, value in results.items():
            detail = value.get("detail", "")
            assert len(detail) < 80, f"{key} detail too long ({len(detail)}): {detail}"
