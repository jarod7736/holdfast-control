from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from pydantic import BaseModel, ValidationError

from holdfastctl.manifest_schema import (
    CredentialRef,
    DeviceManifest,
    McpServerEntry,
    ProfileManifest,
    ProviderEntry,
    SkillEntry,
    validate_capabilities,
    validate_duplicate_ids,
    validate_env_var_name,
    validate_no_arbitrary_commands,
    validate_path_safety,
    validate_secret_literals,
)

app = typer.Typer()

def validate_manifest_file(file_path: Path) -> list[str]:
    """Validate a single manifest file"""
    errors = []
    
    try:
        with open(file_path, 'r') as f:
            data = yaml.safe_load(f)
            
        if not data:
            errors.append(f"Empty manifest file: {file_path}")
            return errors
            
        # Check if it's a device or profile manifest (has device section)
        if 'device' in data:
            # Validate the whole data structure for command fields first
            try:
                validate_no_arbitrary_commands(data, is_catalog=False)
            except (ValueError, ValidationError, yaml.YAMLError, OSError) as e:
                errors.append(f"Command validation failed: {e!s}")
                return errors
                
            # Then validate the structure as DeviceManifest or ProfileManifest
            try:
                manifest: DeviceManifest | ProfileManifest
                if "profile" in data:
                    manifest = ProfileManifest(**data)
                else:
                    manifest = DeviceManifest(**data)
            except (ValueError, ValidationError, yaml.YAMLError, OSError) as e:
                errors.append(f"Manifest structure validation failed: {e!s}")
                return errors
            # Validate capability entries against their per-type schemas
            try:
                validate_capabilities(data.get('capabilities', {}) or {})
            except (ValueError, ValidationError) as e:
                errors.append(f"Capability validation failed: {e!s}")
                return errors
            # Validate credentials
            validate_duplicate_ids(manifest.credentials)
            for cred in manifest.credentials:
                validate_env_var_name(cred.inject_as)
                if not cred.reference.startswith('op://'):
                    errors.append(
                        f"Literal secret pattern in credentials[{cred.id}].reference — reference must be an op:// URI, not a literal value"
                    )
            
            # Validate paths in device manifest
            # Validate device.id, capabilities paths if any, etc.
            try:
                # Validate managed_paths in capabilities if they exist
                if 'capabilities' in data:
                    capabilities = data['capabilities']
                    if 'managed_paths' in capabilities:
                        managed_paths = capabilities['managed_paths']
                        if isinstance(managed_paths, list):
                            for path in managed_paths:
                                validate_path_safety(path)
                        elif isinstance(managed_paths, dict):
                            for path in managed_paths.values():
                                if isinstance(path, str):
                                    validate_path_safety(path)
            except (ValueError, ValidationError, yaml.YAMLError, OSError) as e:
                errors.append(f"Path validation failed: {e!s}")
                
        # Validate the data structures to ensure no literal secrets in non-reference fields
        # We'll do a more thorough scan of all string values
        validate_all_strings(data, errors)
        
    except (ValueError, ValidationError, yaml.YAMLError, OSError) as e:
        errors.append(f"Failed to read manifest: {e!s}")
        return errors
        
    return errors

#: Catalog blocks and the model each entry must satisfy, as
#: (yaml key, model, error label, secret-scan context).
_CATALOG_BLOCKS: tuple[tuple[str, type[BaseModel], str, str], ...] = (
    ("providers", ProviderEntry, "Provider", "provider"),
    ("mcp-servers", McpServerEntry, "MCP server", "mcp-server"),
    ("skills", SkillEntry, "Skill", "skill"),
    ("credentials", CredentialRef, "Credentials", "credential"),
)


def _validate_catalog_block(
    data: Any,
    block: str,
    model: type[BaseModel],
    label: str,
    context: str,
    errors: list[str],
) -> None:
    """Validate every entry in one catalog block, appending any errors."""
    for entry in data.get(block) or []:
        try:
            model(**entry)
            validate_all_strings(entry, errors, context)
            if 'source' in entry and isinstance(entry['source'], str):
                validate_path_safety(entry['source'])
        except (ValueError, ValidationError, yaml.YAMLError, OSError) as e:
            errors.append(f"{label} validation failed: {e!s}")


def validate_catalog_file(file_path: Path) -> list[str]:
    """Validate a catalog file"""
    errors = []
    
    try:
        with open(file_path, 'r') as f:
            data = yaml.safe_load(f)
            
        if not data:
            errors.append(f"Empty catalog file: {file_path}")
            return errors
            
        # Validate every block the file actually contains, rather than guessing
        # from the filename: a catalog may carry providers, mcp-servers, skills
        # and credentials together, and the one loaded at runtime does.
        for block, model, label, context in _CATALOG_BLOCKS:
            _validate_catalog_block(data, block, model, label, context, errors)

        # Validate command fields in catalogs (allow npx/uvx with pinning)
        try:
            validate_no_arbitrary_commands(data, is_catalog=True)
        except (ValueError, ValidationError, yaml.YAMLError, OSError) as e:
            errors.append(f"Catalog command validation failed: {e!s}")
            
        # Validate paths in catalog files where appropriate
        try:
            validate_catalog_paths(data, errors)
        except (ValueError, ValidationError, yaml.YAMLError, OSError) as e:
            errors.append(f"Catalog path validation failed: {e!s}")
            
    except (ValueError, ValidationError, yaml.YAMLError, OSError) as e:
        errors.append(f"Failed to read catalog: {e!s}")
        return errors
        
    return errors

def validate_catalog_paths(data: Any, errors: list[str]) -> None:
    """Validate paths in catalog files"""
    if isinstance(data, dict):
        for key, value in data.items():
            # Look for path-related fields
            if key in ['source', 'path', 'managed_paths', 'url']:
                if isinstance(value, str):
                    # Validate path safety for path fields
                    try:
                        validate_path_safety(value)
                    except (ValueError, ValidationError, yaml.YAMLError, OSError) as e:
                        errors.append(f"Path validation failed for {key}: {e!s}")
            elif isinstance(value, dict):
                validate_catalog_paths(value, errors)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        validate_catalog_paths(item, errors)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                validate_catalog_paths(item, errors)

def validate_all_strings(data: Any, errors: list[str], parent_key: str = "") -> None:
    """Recursively validate all string values in the data structure"""
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, str):
                # Check for secret patterns in non-reference fields
                if key != 'reference' and not key.startswith('op://'):
                    try:
                        field_path = f"{parent_key}.{key}" if parent_key else key
                        validate_secret_literals(value, field_path)
                    except ValueError as e:
                        errors.append(str(e))
            elif isinstance(value, dict):
                validate_all_strings(value, errors, key)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        validate_all_strings(item, errors, key)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                validate_all_strings(item, errors, parent_key)

def is_catalog_file(path: Path) -> bool:
    """Check if the file is a catalog file.

    Any YAML under catalogs/ counts, so adding a catalog does not silently
    bypass catalog validation.
    """
    return path.parent.name == "catalogs" and path.suffix in (".yaml", ".yml")

@app.command()
def validate(
    path: Annotated[Path | None, typer.Argument(help="Path to manifest file or directory to validate")] = None,
) -> None:
    """Validate manifest files for security issues"""
    if not path:
        path = Path("manifests")
    
    if not path.exists():
        typer.echo(f"Error: Path {path} does not exist")
        raise typer.Exit(code=1)
    
    # Collect all YAML files to validate
    yaml_files = []
    if path.is_file() and path.suffix == '.yaml':
        yaml_files.append(path)
    elif path.is_dir():
        yaml_files = list(path.rglob("*.yaml"))
    else:
        typer.echo(f"Error: {path} is not a YAML file or directory")
        raise typer.Exit(code=1)
    
    # Validate all files
    total_errors = 0
    for file_path in yaml_files:
        if is_catalog_file(file_path):
            errors = validate_catalog_file(file_path)
        else:
            errors = validate_manifest_file(file_path)
        if errors:
            typer.echo(f"Errors in {file_path}:")
            for error in errors:
                typer.echo(f"  - {error}")
            total_errors += len(errors)
        else:
            typer.echo(f"✓ {file_path} is valid")
    
    if total_errors > 0:
        typer.echo(f"\n{total_errors} validation errors found")
        raise typer.Exit(code=1)
    else:
        typer.echo("\nAll manifests are valid")

if __name__ == "__main__":
    app()
