"""
Tests for the apply module.
"""

from pathlib import Path
from unittest.mock import Mock, mock_open, patch

import pytest

from holdfastctl.apply import ApplyError, AtomicApplier, ConfigurationApplier


class TestAtomicApplier:
    """Test cases for AtomicApplier."""

    def test_init(self):
        """Test AtomicApplier initialization."""
        backup_manager = Mock()
        applier = AtomicApplier(backup_manager)
        assert applier.backup_manager == backup_manager

    @patch('shutil.copy2')
    @patch('shutil.move')
    @patch('tempfile.NamedTemporaryFile')
    def test_apply_configuration_success(self, mock_tempfile, mock_move, mock_copy2):
        """Test successful configuration application."""
        # Setup mock tempfile
        mock_temp_file = Mock()
        mock_temp_file.name = "/tmp/test.tmp"
        mock_tempfile.return_value.__enter__.return_value = mock_temp_file
        
        backup_manager = Mock()
        backup_manager.create_backup.return_value = Path("/tmp/backup")
        
        applier = AtomicApplier(backup_manager)
        
        # Mock file operations
        with patch('builtins.open', mock_open(read_data='{"test": "data"}')):
            result = applier.apply_configuration(Path("/tmp/test.json"), {"new": "config"})
            assert result is True

    @patch('shutil.copy2')
    @patch('shutil.move')
    @patch('tempfile.NamedTemporaryFile')
    def test_apply_configuration_failure(self, mock_tempfile, mock_move, mock_copy2):
        """Test configuration application failure with restore."""
        # Setup mock tempfile
        mock_temp_file = Mock()
        mock_temp_file.name = "/tmp/test.tmp"
        mock_tempfile.return_value.__enter__.return_value = mock_temp_file
        
        # Mock file operations to raise an exception
        mock_move.side_effect = Exception("Test error")
        
        backup_manager = Mock()
        backup_manager.create_backup.return_value = Path("/tmp/backup")
        backup_manager.restore_from_backup.return_value = True
        
        applier = AtomicApplier(backup_manager)
        
        with (
            patch('builtins.open', mock_open(read_data='{"test": "data"}')),
            pytest.raises(ApplyError),
        ):
            applier.apply_configuration(Path("/tmp/test.json"), {"new": "config"})

    @patch('shutil.copy2')
    @patch('shutil.move')
    @patch('tempfile.NamedTemporaryFile')
    def test_apply_plan(self, mock_tempfile, mock_move, mock_copy2):
        """Test applying a single plan."""
        mock_temp_file = Mock()
        mock_temp_file.name = "/tmp/test.tmp"
        mock_tempfile.return_value.__enter__.return_value = mock_temp_file
        
        backup_manager = Mock()
        backup_manager.create_backup.return_value = Path("/tmp/backup")
        
        applier = AtomicApplier(backup_manager)
        
        plan = {
            "action": "update",
            "target_data": {"test": "data"}
        }
        
        with patch('builtins.open', mock_open(read_data='{"old": "data"}')):
            result = applier.apply_plan(plan, Path("/tmp/test.json"))
            assert result is True


class TestConfigurationApplier:
    """Test cases for ConfigurationApplier."""

    def test_init(self):
        """Test ConfigurationApplier initialization."""
        applier = ConfigurationApplier()
        assert applier.backup_manager is not None
        assert applier.applier is not None

    @patch('shutil.copy2')
    @patch('shutil.move')
    @patch('tempfile.NamedTemporaryFile')
    def test_apply_success(self, mock_tempfile, mock_move, mock_copy2):
        """Test successful configuration application."""
        mock_temp_file = Mock()
        mock_temp_file.name = "/tmp/test.tmp"
        mock_tempfile.return_value.__enter__.return_value = mock_temp_file
        
        backup_manager = Mock()
        backup_manager.create_backup.return_value = Path("/tmp/backup")
        
        applier = ConfigurationApplier()
        applier.backup_manager = backup_manager
        
        with patch('builtins.open', mock_open(read_data='{"test": "data"}')):
            result = applier.apply(Path("/tmp/test.json"), {"new": "config"})
            assert result is True

    @patch('shutil.copy2')
    @patch('shutil.move')
    @patch('tempfile.NamedTemporaryFile')
    def test_apply_plan_success(self, mock_tempfile, mock_move, mock_copy2):
        """Test applying a single plan."""
        mock_temp_file = Mock()
        mock_temp_file.name = "/tmp/test.tmp"
        mock_tempfile.return_value.__enter__.return_value = mock_temp_file
        
        backup_manager = Mock()
        backup_manager.create_backup.return_value = Path("/tmp/backup")
        
        applier = ConfigurationApplier()
        applier.backup_manager = backup_manager
        
        plan = {
            "action": "update",
            "target_data": {"test": "data"}
        }
        
        with patch('builtins.open', mock_open(read_data='{"old": "data"}')):
            result = applier.apply_plan(plan, Path("/tmp/test.json"))
            assert result is True

class TestAtomicWrite:
    """The module-level atomic_write primitive (robust replacement)."""

    def test_writes_and_preserves_mode(self, tmp_path):
        import os
        import stat

        from holdfastctl.apply import atomic_write

        target = tmp_path / "config.json"
        target.write_text("old")
        os.chmod(target, 0o600)
        atomic_write(target, "new", allowed_prefixes=(tmp_path,))
        assert target.read_text() == "new"
        assert stat.S_IMODE(target.stat().st_mode) == 0o600

    def test_new_file_not_world_readable(self, tmp_path):
        import stat

        from holdfastctl.apply import atomic_write

        target = tmp_path / "fresh.json"
        atomic_write(target, "{}", allowed_prefixes=(tmp_path,))
        assert stat.S_IMODE(target.stat().st_mode) & 0o077 == 0

    def test_rejects_path_outside_allowlist(self, tmp_path):
        from holdfastctl.apply import atomic_write

        with pytest.raises(ApplyError, match="not an allowed managed path"):
            atomic_write(tmp_path / "config.json", "{}", allowed_prefixes=(tmp_path / "nested",))
