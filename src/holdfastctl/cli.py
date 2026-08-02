"""Command-line interface for holdfastctl."""

import json
import socket
import subprocess
import sys
from pathlib import Path

import typer

app = typer.Typer(help="Holdfast Control - configuration management for home lab devices")


@app.callback()
def main(ctx: typer.Context) -> None:
    """Holdfast Control - configuration management for home lab devices."""


def _resolve_device_id(config: dict[str, object]) -> str:
    """Resolve the device id: explicit config, then hostname (hosts/DNS), then ask the user."""
    explicit = config.get("device_id")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    hostname = socket.gethostname().strip() or socket.getfqdn().strip()
    if hostname:
        return hostname

    if sys.stdin.isatty():
        value = typer.prompt("Could not detect a device name. Device id", default="local")
        return value.strip() or "local"
    return "local"


@app.command()
def doctor(
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Run diagnostic checks on the local system."""
    results: dict[str, dict[str, object]] = {
        "os": {},
        "opencode": {},
        "onepassword": {},
        "lite_llm": {},
        "amd_halo": {},
        "git": {},
        "ssh_agent": {},
        "agent": {},
        "desired_state": {},
    }
    issues: list[str] = []
    
    # OS/Architecture
    try:
        import platform
        uname = platform.uname()
        results["os"] = {
            "system": uname.system,
            "node": uname.node,
            "release": uname.release,
            "version": uname.version,
            "machine": uname.machine,
            "processor": uname.processor,
        }
    except Exception as e:  # noqa: BLE001 - diagnostic checks must never crash the command
        issues.append(f"OS detection failed: {e}")
        results["os"]["error"] = str(e)
    
    # OpenCode version and config paths
    try:
        import subprocess
        result = subprocess.run(["opencode", "--version"], capture_output=True, text=True, timeout=5, check=False)
        if result.returncode == 0:
            results["opencode"]["version"] = result.stdout.strip()
            results["opencode"]["available"] = True
        else:
            results["opencode"]["available"] = False
            issues.append("OpenCode not found or not executable")
    except Exception as e:  # noqa: BLE001 - diagnostic checks must never crash the command
        results["opencode"]["available"] = False
        issues.append(f"OpenCode check failed: {e}")
    
    # OpenCode config paths
    config_paths = [
        Path.home() / ".config" / "opencode",
        Path.home() / ".opencode",
    ]
    results["opencode"]["config_paths"] = [str(p) for p in config_paths if p.exists()]
    
    # 1Password availability
    try:
        import subprocess
        result = subprocess.run(["op", "--version"], capture_output=True, text=True, timeout=5, check=False)
        if result.returncode == 0:
            results["onepassword"]["version"] = result.stdout.strip()
            results["onepassword"]["available"] = True
        else:
            results["onepassword"]["available"] = False
            issues.append("1Password CLI not found")
    except Exception as e:  # noqa: BLE001 - diagnostic checks must never crash the command
        results["onepassword"]["available"] = False
        issues.append(f"1Password check failed: {e}")
    
    # LiteLLM health (basic check)
    results["lite_llm"] = {"status": "not_configured", "note": "Configure LiteLLM endpoint to enable health checks"}
    
    # amd-halo health
    results["amd_halo"] = {"status": "not_configured", "note": "Configure amd-halo endpoint to enable health checks"}
    
    # Git state
    try:
        import subprocess
        result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=5, cwd=Path.home(), check=False)
        if result.returncode == 0:
            results["git"]["clean"] = len(result.stdout.strip()) == 0
            results["git"]["status"] = "clean" if results["git"]["clean"] else "dirty"
        else:
            results["git"]["clean"] = False
            results["git"]["status"] = "not_a_repo_or_error"
    except Exception as e:  # noqa: BLE001 - diagnostic checks must never crash the command
        results["git"]["clean"] = False
        issues.append(f"Git check failed: {e}")
    
    # SSH agent
    try:
        import subprocess
        result = subprocess.run(["ssh-add", "-l"], capture_output=True, text=True, timeout=5, check=False)
        if result.returncode == 0:
            results["ssh_agent"]["running"] = True
            results["ssh_agent"]["keys"] = len(result.stdout.strip().split("\n")) if result.stdout.strip() else 0
        else:
            results["ssh_agent"]["running"] = False
            issues.append("SSH agent not running or no keys loaded")
    except Exception as e:  # noqa: BLE001 - diagnostic checks must never crash the command
        results["ssh_agent"]["running"] = False
        issues.append(f"SSH agent check failed: {e}")
    
    # Agent version
    results["agent"]["version"] = "0.1.0"
    results["agent"]["name"] = "holdfastctl"
    
    # Applied desired-state commit (placeholder)
    results["desired_state"]["commit"] = "unknown"
    results["desired_state"]["note"] = "Implement desired state tracking"
    
    # Output
    if json_output:
        output = {
            "healthy": len(issues) == 0,
            "issues": issues,
            "checks": results,
        }
        print(json.dumps(output, indent=2))
    else:
        print("Holdfast Control - System Diagnostics")
        print("=" * 50)
        print(f"Overall: {'HEALTHY' if len(issues) == 0 else 'ISSUES FOUND'}")
        print()
        
        for category, data in results.items():
            print(f"{category.upper()}:")
            if isinstance(data, dict):
                for key, value in data.items():
                    print(f"  {key}: {value}")
            else:
                print(f"  {data}")
            print()
        
        if issues:
            print("ISSUES:")
            for issue in issues:
                print(f"  - {issue}")
    
    if issues:
        sys.exit(1)
    sys.exit(0)


@app.command(context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
def run(
    ctx: typer.Context,
    tool: str = typer.Argument(..., help="Tool to launch (only 'opencode' is supported)"),
) -> None:
    """Launch a tool with secrets injected via 1Password (op run)."""
    if tool != "opencode":
        typer.echo(f"Error: unsupported tool '{tool}'. Only 'opencode' is supported.", err=True)
        raise typer.Exit(code=1)


    args = ctx.args
    # Verify the 1Password CLI is present.
    try:
        op_check = subprocess.run(["op", "--version"], capture_output=True, text=True, check=False)
    except (FileNotFoundError, OSError):
        typer.echo("Error: 1Password CLI ('op') not found or not executable.", err=True)
        raise typer.Exit(code=1)
    if op_check.returncode != 0:
        typer.echo("Error: 1Password CLI ('op') not found or not executable.", err=True)
        raise typer.Exit(code=1)

    # Verify 1Password desktop integration (signed in to an unlocked/default account).
    try:
        account_check = subprocess.run(["op", "account", "get"], capture_output=True, text=True, check=False)
    except (FileNotFoundError, OSError):
        typer.echo(
            "Error: 1Password desktop integration unavailable. "
            "Sign in to 1Password and unlock the desktop app, then retry.",
            err=True,
        )
        raise typer.Exit(code=1)
    if account_check.returncode != 0:
        typer.echo(
            "Error: 1Password desktop integration unavailable. "
            "Sign in to 1Password and unlock the desktop app, then retry.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Launch OpenCode through op run. stdout/stderr are inherited directly so the
    # resolved environment is never captured, printed, or written to disk.
    result = subprocess.run(["op", "run", "--", "opencode", *args], check=False)
    sys.exit(result.returncode)


@app.command()
def report(
    config_path: Path = typer.Option(  # noqa: B008 - idiomatic typer default
        Path.home() / ".config" / "holdfastctl" / "config.yaml",  # noqa: B008 - idiomatic typer default
        "--config",
        "-c",
        help="Agent config file",
    ),
    enrollment_code: str | None = typer.Option(
        None,
        "--enrollment-code",
        help="Operator-provisioned one-time code used on first enrollment",
    ),
) -> None:
    """Inspect local state and report it to the control plane."""
    import yaml

    from holdfastctl.inspect import DeviceInspector
    from holdfastctl.manifest_schema import DeviceInfo
    from holdfastctl.reporting import ReportingError, StatusReporter

    if not config_path.exists():
        typer.echo(f"Error: agent config not found at {config_path}", err=True)
        raise typer.Exit(code=1)

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
    except Exception as e:  # noqa: BLE001 - surface any config read error to the user
        typer.echo(f"Error: failed to read agent config: {e}", err=True)
        raise typer.Exit(code=1)

    device_id = _resolve_device_id(config)
    control_plane_url = config.get("control_plane_url", "http://127.0.0.1:8000")

    device_info = DeviceInfo(id=device_id, profile=config.get("profile", "linux-wsl"), display_name=device_id)
    inspector = DeviceInspector(device_info)
    status = inspector.inspect_system()
    status["device_id"] = device_id

    token_path = config.get("token_path") or str(config_path.parent / "report.token")
    reporter = StatusReporter(control_plane_url, device_id, token_path=token_path)
    try:
        ok = reporter.report_status(status, enrollment_code=enrollment_code)
    except ReportingError as e:
        typer.echo(f"Error: reporting to {control_plane_url} failed: {e}", err=True)
        raise typer.Exit(code=1)

    if not ok:
        typer.echo(f"Error: control plane at {control_plane_url} rejected the report", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Reported {device_id} to {control_plane_url}")
    if reporter.pending_gateway_key:
        typer.echo("")
        typer.echo("Gateway virtual key minted for this device (shown ONCE, never stored):")
        typer.echo(f"  alias: {reporter.pending_gateway_key_alias}")
        typer.echo(f"  key:   {reporter.pending_gateway_key}")
        typer.echo("Store it in 1Password now, then export it in your shell profile, e.g.:")
        typer.echo(
            f"  op item create --vault holdfast-lan --category 'API Credential' "
            f"--title 'litellm-{device_id}' credential='<paste key>'"
        )


@app.command("enroll-code")
def enroll_code(
    device_id: str = typer.Argument(..., help="Device id the one-time code is bound to"),
    models: str = typer.Option("", "--models", help="Comma-separated gateway model ids for the device's virtual key"),
    mcp_servers: str = typer.Option("", "--mcp", help="Comma-separated MCP server ids recorded in key metadata"),
    control_plane_url: str = typer.Option("http://127.0.0.1:8000", "--control-plane", help="Control plane URL"),
    expires_in_seconds: int = typer.Option(600, "--expires", help="Code lifetime in seconds"),
) -> None:
    """Operator: mint a one-time enrollment code (requires HOLDFAST_ADMIN_TOKEN)."""
    import os

    import requests

    admin_token = os.environ.get("HOLDFAST_ADMIN_TOKEN")
    if not admin_token:
        typer.echo("Error: HOLDFAST_ADMIN_TOKEN is not set", err=True)
        raise typer.Exit(code=1)
    payload = {
        "device_id": device_id,
        "expires_in_seconds": expires_in_seconds,
        "gateway_models": [m.strip() for m in models.split(",") if m.strip()],
        "gateway_mcp_servers": [m.strip() for m in mcp_servers.split(",") if m.strip()],
    }
    try:
        response = requests.post(
            f"{control_plane_url}/api/v1/enrollment-codes",
            json=payload,
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=10,
        )
    except requests.RequestException as e:
        typer.echo(f"Error: control plane unreachable: {e}", err=True)
        raise typer.Exit(code=1)
    if response.status_code != 200:
        typer.echo(f"Error: code creation failed (status {response.status_code})", err=True)
        raise typer.Exit(code=1)
    code = response.json()["code"]
    typer.echo(f"Enrollment code for {device_id} (valid {expires_in_seconds}s):")
    typer.echo(f"  {code}")
    typer.echo(f"On the device: holdfastctl report --enrollment-code {code}")


@app.command()
def reconcile(
    manifest_path: Path = typer.Option(  # noqa: B008 - idiomatic typer default
        Path("manifests/devices/jarod7736-laptop.yaml"),
        "--manifest",
        "-m",
        help="Device manifest YAML",
    ),
    catalog_path: Path = typer.Option(  # noqa: B008 - idiomatic typer default
        Path("manifests/catalogs/credentials.yaml"),
        "--catalog",
        help="Credential catalog YAML",
    ),
    config_dir: Path = typer.Option(  # noqa: B008 - idiomatic typer default
        Path.home() / ".config" / "opencode",  # noqa: B008 - idiomatic typer default
        "--config-dir",
        help="OpenCode config directory (reads opencode.json)",
    ),
) -> None:
    """Reconcile every capability declared in the device manifest and print the plan."""
    from holdfastctl.capabilities import reconcile_device

    if not manifest_path.is_file():
        typer.echo(f"Error: manifest not found at {manifest_path}", err=True)
        raise typer.Exit(code=1)
    if not catalog_path.is_file():
        typer.echo(f"Error: catalog not found at {catalog_path}", err=True)
        raise typer.Exit(code=1)

    try:
        results = reconcile_device(manifest_path, catalog_path, opencode_config_dir=config_dir)
    except Exception as e:  # noqa: BLE001 - surface any reconcile failure cleanly
        typer.echo(f"Error: reconcile failed: {e}", err=True)
        raise typer.Exit(code=1)

    total = sum(len(plans) for plans in results.values())
    for capability, plans in results.items():
        typer.echo(f"[{capability}]")
        for plan in plans:
            typer.echo(f"  [{plan.action}] {plan.target}: {plan.description}")
        if not plans:
            typer.echo("  no drift")

    if total:
        typer.echo(f"\nReconcile plan: {total} action(s) across {len(results)} capabilities.")
    else:
        typer.echo("\nNo drift - device matches manifest.")


if __name__ == "__main__":
    app()