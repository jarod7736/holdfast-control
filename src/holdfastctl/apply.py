"""
Atomic configuration application module for Holdfast Control.
This module provides capabilities for applying configuration changes atomically with backup support.
"""

import os

def atomic_write(target, new_config, allowed_prefixes=()):  # noqa: D401
    from pathlib import Path
    """Write JSON config atomically to target path within allowed prefixes.
    Simple implementation for tests.
    """
    # Ensure target is within allowed prefixes
    if allowed_prefixes:
        if not any(str(target).startswith(str(p)) for p in allowed_prefixes):
            raise ValueError(f"Target {target} not within allowed prefixes")
    # Write to temp file then rename
    import json, tempfile, shutil
    with tempfile.NamedTemporaryFile(mode='w', delete=False, dir=target.parent) as tf:
        json.dump(new_config, tf, indent=2)
        temp_name = tf.name
    shutil.move(temp_name, target)
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .backup import BackupError, BackupManager


class ApplyError(Exception):
    """Exception raised when configuration application fails."""


class AtomicApplier:
    """Apply configuration changes atomically with backup support."""

    def __init__(self, backup_manager: BackupManager):
        """Initialize the atomic applier with a backup manager."""
        self.backup_manager = backup_manager

    def apply_configuration(self, target_file: Path, new_config: dict[str, Any]) -> bool:
        """
        Apply a new configuration to a file atomically.
        
        Args:
            target_file: Path to the target configuration file
            new_config: New configuration data
            
        Returns:
            True if successful, False otherwise
            
        Raises:
            ApplyError: If application fails
        """
        backup_path = None
        original_mode = None
        original_owner = None
        
        try:
            # Save original file permissions and ownership if file exists
            if target_file.exists():
                stat_info = target_file.stat()
                original_mode = stat_info.st_mode
                original_owner = (stat_info.st_uid, stat_info.st_gid)
                
                # Create backup before applying
                backup_path = self.backup_manager.create_backup(target_file)
            
            # Write new configuration to temporary file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.tmp', delete=False) as temp_file:
                import json
                json.dump(new_config, temp_file, indent=2)
                temp_file_path = temp_file.name
            
            # Atomically move the temporary file to target
            shutil.move(temp_file_path, target_file)
            
            # Restore original permissions if file existed
            if original_mode is not None:
                os.chmod(target_file, original_mode)
                if original_owner is not None:
                    os.chown(target_file, original_owner[0], original_owner[1])
            
            return True
            
        except Exception as e:  # noqa: BLE001 - wrap all apply failures into ApplyError
            # If something went wrong, restore from backup
            if backup_path is not None and target_file.exists():
                try:
                    self.backup_manager.restore_from_backup(target_file, backup_path)
                except BackupError:
                    # If restoration also fails, we're in a bad state
                    raise ApplyError(f"Failed to apply configuration and restore backup: {e!s}")
            
            raise ApplyError(f"Failed to apply configuration: {e!s}")

    def apply_plan(self, plan: dict[str, Any], target_file: Path) -> bool:
        """
        Apply a single configuration plan.
        
        Args:
            plan: Configuration plan
            target_file: Path to the target file
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Extract target data from plan
            target_data = plan.get('target_data')
            if not target_data:
                raise ApplyError("Plan missing target_data")
            
            # Apply configuration
            return self.apply_configuration(target_file, target_data)
            
        except (ApplyError, OSError) as e:
            raise ApplyError(f"Failed to apply plan: {e!s}")


class ConfigurationApplier:
    """High-level configuration applier with backup management."""

    def __init__(self) -> None:
        """Initialize the configuration applier."""
        self.backup_manager = BackupManager()
        self.applier = AtomicApplier(self.backup_manager)

    def apply(self, target_file: Path, new_config: dict[str, Any]) -> bool:
        """
        Apply a new configuration to a file.
        
        Args:
            target_file: Path to the target configuration file
            new_config: New configuration data
            
        Returns:
            True if successful, False otherwise
        """
        return self.applier.apply_configuration(target_file, new_config)

    def apply_plan(self, plan: dict[str, Any], target_file: Path) -> bool:
        """
        Apply a single configuration plan.
        
        Args:
            plan: Configuration plan
            target_file: Path to the target file
            
        Returns:
            True if successful, False otherwise
        """
        return self.applier.apply_plan(plan, target_file)