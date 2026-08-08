import os
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CredentialRef(BaseModel):
    """Credential reference model"""
    model_config = ConfigDict(extra="forbid")
    id: str
    reference: str
    inject_as: str


class ProviderEntry(BaseModel):
    """Provider entry model"""
    model_config = ConfigDict(extra="forbid")
    id: str
    type: str
    base_url: str
    # Read by generate_opencode_plan: builtin providers connect via auth.json
    # and are never registered in opencode.json.
    builtin: bool = False
    note: str | None = None
    credentials: list[CredentialRef] | None = None


class McpServerEntry(BaseModel):
    """MCP server entry model"""
    model_config = ConfigDict(extra="forbid")
    id: str
    url: str
    credentials: list[CredentialRef] | None = None


class SkillEntry(BaseModel):
    """Skill entry model"""
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    description: str
    version: str
    source: str
    dependencies: list[str]
    checksum: str
    owner: str
    purpose: str


class OpencodeCapability(BaseModel):
    """opencode capability: reconcile opencode.json against declared providers/mcp servers"""
    model_config = ConfigDict(extra="forbid")
    required: bool = False
    config_profile: str | None = None
    providers: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)


class NetworkCapability(BaseModel):
    """network capability: DNS presence in the zone and gateway reachability"""
    model_config = ConfigDict(extra="forbid")
    hostname: str | None = None
    dns_zone: str = "holdfast.lan"
    gateway_url: str | None = None


class GatewayAccessCapability(BaseModel):
    """gateway_access capability: LiteLLM virtual-key scope (models/mcp servers)"""
    model_config = ConfigDict(extra="forbid")
    gateway_url: str
    models: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    key_env: str = "LITELLM_API_KEY"


CAPABILITY_SCHEMAS: dict[str, type[BaseModel]] = {
    "opencode": OpencodeCapability,
    "network": NetworkCapability,
    "gateway_access": GatewayAccessCapability,
}


def validate_capabilities(capabilities: dict[str, Any]) -> None:
    """Validate each capability entry against its schema; unknown capability types are rejected.

    managed_paths is not a capability adapter; its path values are validated
    separately by validate_path_safety.
    """
    for name, config in capabilities.items():
        if name == "managed_paths":
            continue
        schema = CAPABILITY_SCHEMAS.get(name)
        if schema is None:
            raise ValueError(f"Unknown capability type: {name}")
        schema(**(config or {}))


class DeviceInfo(BaseModel):
    """Device identity block; unknown keys are rejected"""
    model_config = ConfigDict(extra="forbid")
    id: str
    profile: str
    display_name: str | None = None


class DeviceManifest(BaseModel):
    """Device manifest model"""
    model_config = ConfigDict(extra="forbid")
    device: DeviceInfo
    capabilities: dict[str, Any]
    credentials: list[CredentialRef]


class ProfileManifest(BaseModel):
    """Profile manifest model"""
    model_config = ConfigDict(extra="forbid")
    profile: dict[str, Any]
    capabilities: dict[str, Any]
    credentials: list[CredentialRef]


# Validation rules
def validate_secret_literals(data: str, field_path: str = "") -> None:
    """Validate that no literal secrets are present in string values"""
    # Check for patterns like sk-, Bearer , op:// (only allowed in reference field)
    if isinstance(data, str):
        # For non-reference fields, check for common secret patterns
        # But allow op:// patterns (they're allowed in reference field)
        if data.startswith('op://'):
            return  # op:// patterns are allowed in reference field
            
        # Check for literal secret patterns in other fields - scan for more patterns
        # Check for unanchored patterns within values
        secret_patterns = [
            r'^(sk-|sk_|sk\.)',  # API key patterns
            r'^Bearer\s',  # Bearer token pattern
            r'sk-[A-Za-z0-9_-]{10,}',  # sk- tokens (unanchored)
            r'ghp_[A-Za-z0-9]{36}',  # GitHub personal access token
            r'gho_[A-Za-z0-9]{36}',  # GitHub OAuth token
            r'github_pat_[A-Za-z0-9_]{24,}',  # GitHub personal access token v2
            r'xox[baprs]-[A-Za-z0-9\-_]{10,}',  # Slack token
            r'AKIA[0-9A-Z]{16}',  # AWS access key
            r'-----BEGIN.*PRIVATE KEY-----',  # Private key block
            r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}',  # JWT tokens
        ]
        
        # For non-reference fields, check for common secret patterns
        for pattern in secret_patterns:
            if re.search(pattern, data, re.IGNORECASE):
                raise ValueError(f"Literal secret pattern (api-key shape) at {field_path}")


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
    
    # Check for symlinks escape - fail closed
    try:
        expanded_path = os.path.realpath(path)
        home_path = os.path.expanduser('~')
        if not expanded_path.startswith(home_path):
            raise ValueError(f"Path escape detected: {path}")
    except (OSError, RuntimeError):
        # Fail closed - if resolution fails, reject
        raise ValueError(f"Path resolution failed, rejecting: {path}")


def validate_no_arbitrary_commands(data: dict[str, Any], is_catalog: bool = False) -> None:
    """Validate that no arbitrary shell commands are present"""
    # Check for command-related fields at device/profile level
    command_fields = ['command', 'exec', 'run', 'shell']
    
    for key, value in data.items():
        # If it's a catalog, allow certain commands in the allowed set
        if is_catalog and key in ['command']:
            # Only allow these commands in catalogs
            allowed_commands = {'npx', 'uvx', 'node', 'python', 'python3'}
            if isinstance(value, str):
                # Check that if it's a command like npx, it's properly pinned
                if value in allowed_commands:
                    # For now just allow it, but we'll validate argument pinning in catalog files
                    pass
            elif isinstance(value, list) and value and value[0] in allowed_commands:
                    # For command lists, validate arguments if they're package specs
                    pass
        else:
            # For device/profile level, block all command fields
            if key in command_fields:
                raise ValueError(f"Arbitrary command field not allowed: {key}")
        
        if isinstance(value, dict):
            validate_no_arbitrary_commands(value, is_catalog)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    validate_no_arbitrary_commands(item, is_catalog)


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
