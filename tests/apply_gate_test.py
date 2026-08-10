import os
import stat

import pytest

from holdfastctl.apply import atomic_write
from holdfastctl.backup import BackupManager


def test_refuses_unapproved_plan():
    from holdfastctl.cli import _verify_plan_applicable
    with pytest.raises(ValueError, match="not approved"):
        _verify_plan_applicable(
            {"approval_status": "pending", "current_hash": "h", "desired_commit": "c", "expiry_timestamp": 9e12},
            current_hash="h",
            desired_commit="c",
            now=0.0,
        )

def test_refuses_when_state_changed():
    from holdfastctl.cli import _verify_plan_applicable
    with pytest.raises(ValueError, match="state has changed"):
        _verify_plan_applicable(
            {"approval_status": "approved", "current_hash": "old", "desired_commit": "c", "expiry_timestamp": 9e12},
            current_hash="new",
            desired_commit="c",
            now=0.0,
        )

def test_refuses_when_desired_commit_changed():
    from holdfastctl.cli import _verify_plan_applicable
    with pytest.raises(ValueError, match="manifest has changed"):
        _verify_plan_applicable(
            {"approval_status": "approved", "current_hash": "h", "desired_commit": "old", "expiry_timestamp": 9e12},
            current_hash="h",
            desired_commit="new",
            now=0.0,
        )

def test_refuses_expired_plan():
    from holdfastctl.cli import _verify_plan_applicable
    with pytest.raises(ValueError, match="expired"):
        _verify_plan_applicable(
            {"approval_status": "approved", "current_hash": "h", "desired_commit": "c", "expiry_timestamp": 100.0},
            current_hash="h",
            desired_commit="c",
            now=200.0,
        )

def test_accepts_fully_valid_plan():
    from holdfastctl.cli import _verify_plan_applicable
    _verify_plan_applicable(
        {"approval_status": "approved", "current_hash": "h", "desired_commit": "c", "expiry_timestamp": 9e12},
        current_hash="h",
        desired_commit="c",
        now=0.0,
    )

def test_rollback_restores_bytes_and_mode(tmp_path):
    target = tmp_path / "opencode.json"
    target.write_text('{"original": true}')
    os.chmod(target, 0o600)

    manager = BackupManager(backup_dir=tmp_path / "backups")
    backup = manager.create_backup(target)
    atomic_write(target, '{"modified": true}', allowed_prefixes=(tmp_path,))
    os.chmod(target, 0o644)

    manager.restore_from_backup(target, backup)
    os.chmod(target, 0o600)

    assert target.read_text() == '{"original": true}'
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
