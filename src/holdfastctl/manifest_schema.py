import os
import re
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
import pathlib


class CredentialRef(BaseModel):
    """Credential reference model"""
    id: str
    reference: str
    inject_as: str


class ProviderEntry(BaseModel):
    """Provider entry model"""
    id: str
    type: str
    base_url: str
    credentials: Optional[list[CredentialRef]] = None


class McpServerEntry(BaseModel):
    """MCP server entry model"""
    id: str
    url: str
    credentials: Optional[list[CredentialRef]] = None


class SkillEntry(BaseModel):
    """Skill entry model"""
    id: str
    name: str
    description: str
    version: str
    source: str
    dependencies: list[str]
    checksum: str
    owner: str
    purpose: str


class DeviceManifest(BaseModel):
    """Device manifest model"""
    device: dict[str, Any]
    capabilities: dict[str, Any]
    credentials: list[CredentialRef]


class ProfileManifest(BaseModel):
    """Profile manifest model"""
    device: dict[str, Any]
    capabilities: dict[str, Any]
    credentials: list[CredentialRef]


# Validation rules
def validate_secret_literals(data: str) -> None:
    """Validate that no literal secrets are present in string values"""
    # Check for patterns like sk-, Bearer , op:// (only allowed in reference field)
    if isinstance(data, str):
        # For non-reference fields, check for common secret patterns
        # But allow op:// patterns (they're allowed in reference field)
        if data.startswith('op://'):
            return  # op:// patterns are allowed in reference field
            
        # Check for literal secret patterns in other fields
        secret_patterns = [
            r'^(sk-|sk_|sk\.)',  # API key patterns
            r'^Bearer\s',  # Bearer token pattern
        ]
        
        # For non-reference fields, check for common secret patterns
        for pattern in secret_patterns:
            if re.search(pattern, data, re.IGNORECASE):
                raise ValueError(f"Literal secret pattern found in non-reference field: {data}")


def validate_path_safety(path: str) -> None:
    """Validate that paths are within allowlist and safe"""
    if not isinstance(path, str):
        return
        
    # Check for path traversal
    if '..' in path:
        raise ValueError(f"Path traversal detected: {path}")
    
    # Check for absolute paths outside home
    if path.startswith('/') and not path.startswith(os.path.expanduser('~')):
        raise ValueError(f"Absolute path outside home detected: {path}")
    
    # Check for symlinks escape
    try:
        expanded_path = os.path.realpath(path)
        home_path = os.path.expanduser('~')
        if not expanded_path.startswith(home_path):
            raise ValueError(f"Path escape detected: {path}")
    except Exception:
        pass  # If path resolution fails, allow it through for now


def validate_no_arbitrary_commands(data: Dict[str, Any]) -> None:
    """Validate that no arbitrary shell commands are present"""
    # Check for command-related fields at device/profile level
    command_fields = ['command', 'exec', 'run', 'shell']
    
    for key, value in data.items():
        if key in command_fields:
            raise ValueError(f"Arbitrary command field not allowed: {key}")
        
        if isinstance(value, dict):
            validate_no_arbitrary_commands(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    validate_no_arbitrary_commands(item)


def validate_duplicate_ids(credentials: list[CredentialRef]) -> None:
    """Validate that no duplicate credential ids or inject_as values exist"""
    ids = set()
    inject_as_names = set()
    
    for cred in credentials:
        if cred.id in ids:
            raise ValueError(f"Duplicate credential id found: {cred.id}")
        ids.add(cred.id)
        
        if cred.inject_as in inject_as_names:
            raise ValueError(f"Duplicate credential inject_as found: {cred.inject_as}")
        inject_as_names.add(cred.inject_as)


def validate_env_var_name(name: str) -> None:
    """Validate that env var name follows the required pattern"""
    if not re.match(r'^[A-Z][A-Z0-9_]*$', name):
        raise ValueError(f"Invalid environment variable name: {name}")
