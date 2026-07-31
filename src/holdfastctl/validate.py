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
    validate_env_var_name
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
                validate_no_arbitrary_commands(data)
            except Exception as e:
                errors.append(f"Command validation failed: {str(e)}")
                return errors
                
            # Then validate the structure as DeviceManifest or ProfileManifest
            try:
                # Try DeviceManifest first
                manifest = DeviceManifest(**data)
                # Validate credentials
                validate_duplicate_ids(manifest.credentials)
                for cred in manifest.credentials:
                    validate_env_var_name(cred.inject_as)
                    # Check that reference field doesn't contain secret patterns (but it's allowed in reference)
                    # This is actually fine - reference is the only field allowed to have op:// values
                    # We already checked for arbitrary commands above
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
                    
        # Validate the data structures to ensure no literal secrets in non-reference fields
        # We'll do a more thorough scan of all string values
        validate_all_strings(data, errors)
        
    except Exception as e:
        errors.append(f"Failed to read manifest: {str(e)}")
        return errors
        
    return errors

def validate_all_strings(data: Any, errors: List[str], parent_key: str = "") -> None:
    """Recursively validate all string values in the data structure"""
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, str):
                # Check for secret patterns in non-reference fields
                if key != 'reference' and not key.startswith('op://'):
                    try:
                        validate_secret_literals(value)
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
