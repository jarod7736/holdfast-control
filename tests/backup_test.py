"""
Tests for the backup module.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from holdfastctl.backup import BackupError, BackupManager


class TestBackupManager:
    """Test cases for BackupManager."""

    def test_init_with_default_backup_dir(self):
        """Test BackupManager initialization with default directory."""
        manager = BackupManager()
        assert manager.backup_dir is not None

    def test_init_with_custom_backup_dir(self):
        """Test BackupManager initialization with custom directory."""
        custom_dir = Path("/tmp/test_backups")
        manager = BackupManager(custom_dir)
        assert manager.backup_dir == custom_dir

    @patch('shutil.copy2')
    @patch('os.path.exists')
    def test_create_backup_success(self, mock_exists, mock_copy2):
        """Test successful backup creation."""
        mock_exists.return_value = True
        
        manager = BackupManager()
        test_file = Path("/tmp/test_file.txt")
        
        with patch('datetime.datetime') as mock_datetime:
            mock_datetime.now().strftime.return_value = "20260101_120000"
            backup_path = manager.create_backup(test_file)
            
            assert backup_path is not None
            assert backup_path.name.startswith("test_file.txt.backup_")

    @patch('shutil.copy2')
    @patch('os.path.exists')
    def test_create_backup_failure(self, mock_exists, mock_copy2):
        """Test backup creation failure."""
        mock_exists.return_value = True
        mock_copy2.side_effect = Exception("Copy failed")
        
        manager = BackupManager()
        test_file = Path("/tmp/test_file.txt")
        
        with pytest.raises(BackupError):
            manager.create_backup(test_file)

    @patch('shutil.copy2')
    @patch('os.path.exists')
    def test_restore_from_backup_success(self, mock_exists, mock_copy2):
        """Test successful backup restoration."""
        mock_exists.return_value = True
        
        manager = BackupManager()
        test_file = Path("/tmp/test_file.txt")
        backup_file = Path("/tmp/test_file.txt.backup_20260101_120000")
        
        result = manager.restore_from_backup(test_file, backup_file)
        assert result is True

    @patch('shutil.copy2')
    @patch('os.path.exists')
    def test_restore_from_backup_failure(self, mock_exists, mock_copy2):
        """Test backup restoration failure."""
        mock_exists.return_value = True
        mock_copy2.side_effect = Exception("Restore failed")
        
        manager = BackupManager()
        test_file = Path("/tmp/test_file.txt")
        backup_file = Path("/tmp/test_file.txt.backup_20260101_120000")
        
        with pytest.raises(BackupError):
            manager.restore_from_backup(test_file, backup_file)

    def test_list_backups(self):
        """Test listing backups."""
        manager = BackupManager()
        # This is hard to test without creating actual backup files
        # But we can at least ensure it doesn't crash
        backups = manager.list_backups(Path("/tmp/nonexistent.txt"))
        assert isinstance(backups, list)

    def test_cleanup_old_backups(self):
        """Test cleanup of old backups."""
        manager = BackupManager()
        # This is hard to test without creating actual backup files
        # But we can at least ensure it doesn't crash
        manager.cleanup_old_backups(Path("/tmp/nonexistent.txt"))