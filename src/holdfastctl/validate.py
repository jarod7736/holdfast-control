import os
import yaml
import typer
from pathlib import Path
from typing import List, Optional, Dict, Any
from holdfastctl.manifest_schema import (
    DeviceManifest, 
    ProfileManifest, 
    CredentialRef,
    validate_secret_literals,
    validate_path_safety,
    validate_no_arbitrary_commands,
    validate_duplicate_ids,
    validate_env_var_name,
    ProviderEntry,
    McpServerEntry,
    SkillEntry
)

app = typer.Typer()

def validate_manifest_file(file_path: Path) -> List[str]:
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
            except Exception as e:
                errors.append(f"Command validation failed: {str(e)}")
                return errors
                
            # Then validate the structure as DeviceManifest or ProfileManifest
            try:
                # Try DeviceManifest first
                manifest = DeviceManifest(**data)
        # Ensure we can check for profile fields
        if "profile" in data:
            # Try ProfileManifest
            try:
                profile_manifest = ProfileManifest(**data)
                # If this works, use it instead
                manifest = profile_manifest
            except Exception:
                # If ProfileManifest fails, keep DeviceManifest
                pass
                # Validate credentials
                validate_duplicate_ids(manifest.credentials)
                for cred in manifest.credentials:
                    validate_env_var_name(cred.inject_as)
                    # Check for secret patterns in non-reference fields
                    # We already validated that there are no secrets in reference field at the schema level
            except Exception as e:
                try:
                    # If DeviceManifest fails, try ProfileManifest
                    manifest = ProfileManifest(**data)
                    # Validate credentials
                    validate_duplicate_ids(manifest.credentials)
                    for cred in manifest.credentials:
                        validate_env_var_name(cred.inject_as)
                except Exception as e2:
                    errors.append(f"Manifest validation failed for both DeviceManifest and ProfileManifest: {str(e2)}")
                    return errors
            
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
            except Exception as e:
                errors.append(f"Path validation failed: {str(e)}")
                
        # Validate the data structures to ensure no literal secrets in non-reference fields
        # We'll do a more thorough scan of all string values
        validate_all_strings(data, errors)
        
    except Exception as e:
        errors.append(f"Failed to read manifest: {str(e)}")
        return errors
        
    return errors

def validate_catalog_file(file_path: Path) -> List[str]:
    """Validate a catalog file"""
    errors = []
    
    try:
        with open(file_path, 'r') as f:
            data = yaml.safe_load(f)
            
        if not data:
            errors.append(f"Empty catalog file: {file_path}")
            return errors
            
        # Get the filename to determine type
        filename = file_path.name
        
        # Validate based on filename
        if filename == "providers.yaml":
            # Validate ProviderEntry structures
            if "providers" in data:
                for provider in data["providers"]:
                    try:
                        # Validate provider entry
                        provider_entry = ProviderEntry(**provider)
                        # Validate secret literals in provider entry
                        validate_all_strings(provider, errors, "provider")
                        # Validate paths in provider entry
                        if 'source' in provider and isinstance(provider['source'], str):
                            validate_path_safety(provider['source'])
                    except Exception as e:
                        errors.append(f"Provider validation failed: {str(e)}")
                        
            if "mcp-servers" in data:
                for server in data["mcp-servers"]:
                    try:
                        # Validate mcp server entry
                        server_entry = McpServerEntry(**server)
                        # Validate secret literals in server entry
                        validate_all_strings(server, errors, "mcp-server")
                        # Validate paths in server entry
                        if 'source' in server and isinstance(server['source'], str):
                            validate_path_safety(server['source'])
                    except Exception as e:
                        errors.append(f"MCP server validation failed: {str(e)}")
                        
        elif filename == "mcp-servers.yaml":
            # Validate McpServerEntry structures
            if "mcp-servers" in data:
                for server in data["mcp-servers"]:
                    try:
                        # Validate mcp server entry
                        server_entry = McpServerEntry(**server)
                        # Validate secret literals in server entry
                        validate_all_strings(server, errors, "mcp-server")
                        # Validate paths in server entry
                        if 'source' in server and isinstance(server['source'], str):
                            validate_path_safety(server['source'])
                    except Exception as e:
                        errors.append(f"MCP server validation failed: {str(e)}")
                        
        elif filename == "skills.yaml":
            # Validate SkillEntry structures
            if "skills" in data:
                for skill in data["skills"]:
                    try:
                        # Validate skill entry
                        skill_entry = SkillEntry(**skill)
                        # Validate secret literals in skill entry
                        validate_all_strings(skill, errors, "skill")
                        # Validate paths in skill entry (source field)
                        if 'source' in skill and isinstance(skill['source'], str):
                            validate_path_safety(skill['source'])
                    except Exception as e:
                        errors.append(f"Skill validation failed: {str(e)}")
                        
        elif filename == "credentials.yaml":
            # Validate credentials entries
            if "credentials" in data:
                for cred in data["credentials"]:
                    try:
                        # Validate credential entry
                        credential_entry = CredentialRef(**cred)
                        # Validate secret literals in credential entry
                        validate_all_strings(cred, errors, "credential")
                    except Exception as e:
                        errors.append(f"Credentials validation failed: {str(e)}")
                        
        # Validate command fields in catalogs (allow npx/uvx with pinning)
        try:
            validate_no_arbitrary_commands(data, is_catalog=True)
        except Exception as e:
            errors.append(f"Catalog command validation failed: {str(e)}")
            
        # Validate paths in catalog files where appropriate
        try:
            validate_catalog_paths(data, errors)
        except Exception as e:
            errors.append(f"Catalog path validation failed: {str(e)}")
            
    except Exception as e:
        errors.append(f"Failed to read catalog: {str(e)}")
        return errors
        
    return errors

def validate_catalog_paths(data: Any, errors: List[str]) -> None:
    """Validate paths in catalog files"""
    if isinstance(data, dict):
        for key, value in data.items():
            # Look for path-related fields
            if key in ['source', 'path', 'managed_paths', 'url']:
                if isinstance(value, str):
                    # Validate path safety for path fields
                    try:
                        validate_path_safety(value)
                    except Exception as e:
                        errors.append(f"Path validation failed for {key}: {str(e)}")
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

def validate_all_strings(data: Any, errors: List[str], parent_key: str = "") -> None:
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
    """Check if the file is a catalog file"""
    return path.parent.name == "catalogs" and path.name in ["providers.yaml", "mcp-servers.yaml", "skills.yaml", "credentials.yaml"]

@app.command()
def validate(
    path: Optional[Path] = typer.Argument(None, help="Path to manifest file or directory to validate")
):
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
        for file_path in path.rglob("*.yaml"):
            yaml_files.append(file_path)
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
