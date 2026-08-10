"""
Atomic configuration application module for Holdfast Control.
This module provides capabilities for applying configuration changes atomically with backup support.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .backup import BackupError, BackupManager
from .manifest_schema import validate_path_safety

DEFAULT_MANAGED_PREFIXES: tuple[Path, ...] = (
    Path.home() / ".config" / "opencode",
    Path.home() / ".config" / "holdfast",
    Path.home() / ".claude" / "skills",
    Path.home() / ".agents" / "skills",
)


def _check_allowed(path: Path, allowed_prefixes: tuple[Path, ...]) -> Path:
    """Resolve path and confirm it sits under an allowed prefix. Fail closed.

    validate_path_safety only constrains paths to $HOME, so it is skipped when
    allowed_prefixes has been overridden (e.g. tests writing under tmp_path,
    outside $HOME) -- the injected prefixes already govern scope there. For the
    default $HOME-rooted prefixes it still runs as an extra traversal check.
    """
    if allowed_prefixes == DEFAULT_MANAGED_PREFIXES:
        try:
            validate_path_safety(str(path))
        except ValueError as e:
            raise ApplyError(str(e)) from e
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError) as e:
        raise ApplyError(f"Path resolution failed, rejecting: {path}") from e
    for prefix in allowed_prefixes:
        try:
            resolved.relative_to(prefix.resolve())
        except ValueError:
            continue
        return resolved
    raise ApplyError(f"{path} is not an allowed managed path")


def atomic_write(
    path: Path,
    data: str,
    *,
    allowed_prefixes: tuple[Path, ...] = DEFAULT_MANAGED_PREFIXES,
) -> None:
    """Atomically replace path's contents with data.

    The temp file is created in path's own directory so os.replace is a true
    rename rather than a cross-filesystem copy. Mode is preserved when the file
    already exists, and defaults to 0600 when it does not.
    """
    target = _check_allowed(path, allowed_prefixes)
    mode = 0o600
    if target.exists():
        mode = os.stat(target).st_mode & 0o777
    fd, temp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, target)
    except Exception as e:
        temp_path.unlink(missing_ok=True)
        raise ApplyError(f"Failed to write {target}: {e!s}") from e


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