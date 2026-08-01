"""
Tests for the `mypy -p holdfastctl -p server` type-checking gate.

Ensures the strict type-checking gate that CI runs stays green.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_mypy() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mypy", "-p", "holdfastctl", "-p", "server"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class TestMypyGate:
    """The strict mypy gate passes for holdfastctl and server packages."""

    def test_mypy_is_available(self):
        if shutil.which("mypy") is None:
            pytest.skip("mypy not installed")
        assert shutil.which("mypy") is not None

    def test_mypy_passes(self):
        if shutil.which("mypy") is None:
            pytest.skip("mypy not installed")
        result = _run_mypy()
        assert result.returncode == 0, (
            f"mypy reported issues:\n{result.stdout}\n{result.stderr}"
        )

    def test_mypy_output_contains_no_issues(self):
        if shutil.which("mypy") is None:
            pytest.skip("mypy not installed")
        result = _run_mypy()
        assert "no issues found" in result.stdout

    def test_package_modules_are_checked(self):
        if shutil.which("mypy") is None:
            pytest.skip("mypy not installed")
        result = _run_mypy()
        assert "source files" in result.stdout
