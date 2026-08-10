"""
Backup management module for Holdfast Control.
This module provides capabilities for creating, restoring, and managing backups.
"""

import datetime
import os
import shutil
from pathlib import Path


class BackupError(Exception):
    """Exception raised when backup operations fail."""


from typing import Any


class BackupManager:
    # Existing methods omitted for brevity
    # ... (existing code above) ...
    def write_manifest(self, plan_id: str, entries: list[dict[str, Any]]) -> None:
        """Write a manifest of backup entries for a plan.
        
        The manifest is stored as JSON in the backup directory with filename
        f"{plan_id}.manifest.json".
        """
        import json
        manifest_path = self.backup_dir / f"{plan_id}.manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(entries, f)

    def read_manifest(self, plan_id: str) -> list[dict[str, Any]]:
        """Read a manifest written by write_manifest.
        
        Returns an empty list if the manifest does not exist.
        """
        import json
        manifest_path = self.backup_dir / f"{plan_id}.manifest.json"
        if not manifest_path.is_file():
            return []
        with open(manifest_path, "r", encoding="utf-8") as f:
            loaded: list[dict[str, Any]] = json.load(f)
        return loaded
    """Manage device backups."""

    def __init__(self, backup_dir: Path | None = None):
        """Initialize the backup manager.
        
        Args:
            backup_dir: Directory to store backups. If None, uses default location.
        """
        if backup_dir is None:
            self.backup_dir = Path.home() / ".holdfast" / "backups"
        else:
            self.backup_dir = backup_dir
            
        # Create backup directory if it doesn't exist
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def create_backup(self, source_file: Path) -> Path | None:
        """
        Create a backup of a file.

        Returns the backup path, or None when the source does not exist -- a
        file about to be created has nothing to restore, and an empty backup
        would blank the live file on rollback.

        Raises:
            BackupError: If backup creation fails
        """
        try:
            if not os.path.exists(source_file):
                return None

            # Generate backup filename
            timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
            backup_filename = f"{source_file.name}.backup_{timestamp}"
            backup_path = self.backup_dir / backup_filename
            
            # Create the backup
            shutil.copy2(source_file, backup_path)
            
            return backup_path
            
        except Exception as e:  # noqa: BLE001 - wrap all backup failures into BackupError
            raise BackupError(f"Failed to create backup: {e!s}")

    def restore_from_backup(self, target_file: Path, backup_file: Path) -> bool:
        """
        Restore a file from a backup.
        
        Args:
            target_file: Path to the target file to restore
            backup_file: Path to the backup file
            
        Returns:
            True if successful, False otherwise
            
        Raises:
            BackupError: If restore fails
        """
        try:
            if not os.path.exists(backup_file):
                raise BackupError(f"Backup file does not exist: {backup_file}")
                
            # Restore from backup
            shutil.copy2(backup_file, target_file)
            
            return True
            
        except Exception as e:  # noqa: BLE001 - wrap all restore failures into BackupError
            raise BackupError(f"Failed to restore from backup: {e!s}")

    def list_backups(self, source_file: Path) -> list[Path]:
        """
        List all backups for a source file.
        
        Args:
            source_file: Path to the source file
            
        Returns:
            List of backup file paths
        """
        backups = []
        try:
            # Look for files that match the pattern
            for file in self.backup_dir.iterdir():
                if file.name.startswith(f"{source_file.name}.backup_"):
                    backups.append(file)
        except OSError:
            # If we can't list backups, return empty list
            pass

        return backups

    def cleanup_old_backups(self, source_file: Path, max_backups: int = 5) -> None:
        """
        Clean up old backups, keeping only the most recent ones.
        
        Args:
            source_file: Path to the source file
            max_backups: Maximum number of backups to keep
        """
        try:
            backups = self.list_backups(source_file)
            backups.sort(key=lambda x: x.name, reverse=True)
            
            # Remove old backups beyond the limit
            for backup in backups[max_backups:]:
                backup.unlink(missing_ok=True)
                
        except OSError:
            # If cleanup fails, just continue
            pass