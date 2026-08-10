"""Command-line interface for holdfastctl."""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import typer
import yaml

from holdfastctl.capabilities import collect_device_state, device_state_fingerprint
from holdfastctl.reporting import ReportingError, StatusReporter
from holdfastctl.validate import validate as _validate

app = typer.Typer(help="Holdfast Control - configuration management for home lab devices")

# validate is defined on validate.py's own Typer app; register it here so it is
# reachable from the main CLI rather than existing only as an unmounted command.
app.command()(_validate)


@app.callback()
def main(ctx: typer.Context) -> None:
    """Holdfast Control - configuration management for home lab devices."""


def _resolve_device_id(config: dict[str, object]) -> str:
    """Resolve the device id from config or hostname."""
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

def _load_agent_config(config_path: Path) -> dict[str, Any]:
    """Load YAML config file."""
    try:
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:  # noqa: BLE001 - surface any config read error to the user
        typer.echo(f"Error: failed to read config {config_path}: {e}", err=True)
        raise typer.Exit(code=1)


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


def _echo_pending_gateway_key(reporter: Any, device_id: str) -> None:
    """Print the one-time gateway key banner if enrollment minted one.

    Called on every exit path once a key may have been minted: it is never
    persisted, so a report failure after enrollment must not lose it.
    """
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

    # Capability checks
    try:
        from holdfastctl.checks import run_checks

        status["checks"] = run_checks()
    except Exception:  # noqa: BLE001,S110 - catastrophic checks failure must not break reporting
        pass

    token_path = config.get("token_path") or str(config_path.parent / "report.token")
    reporter = StatusReporter(control_plane_url, device_id, token_path=token_path)
    try:
        ok = reporter.report_status(status, enrollment_code=enrollment_code)
    except ReportingError as e:
        # Ensure minted gateway key (if any) is shown before exiting
        _echo_pending_gateway_key(reporter, device_id)
        typer.echo(f"Error: reporting to {control_plane_url} failed: {e}", err=True)
        raise typer.Exit(code=1)

    if not ok:
        # Ensure minted key is shown even when the control plane rejects the report
        _echo_pending_gateway_key(reporter, device_id)
        typer.echo(f"Error: control plane at {control_plane_url} rejected the report", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Reported {device_id} to {control_plane_url}")
    # Show minted key on successful report as well
    _echo_pending_gateway_key(reporter, device_id)


@app.command()
def status(
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Report device status to the control plane (no enrollment)."""
    # Load config
    config_path = Path.home() / ".config" / "holdfastctl" / "config.yaml"
    if not config_path.exists():
        typer.echo(f"Error: config not found at {config_path}", err=True)
        raise typer.Exit(code=1)
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}
    device_id = _resolve_device_id(config)
    control_plane_url = config.get("control_plane_url", "http://127.0.0.1:8000")
    token_path = config.get("token_path") or str(config_path.parent / "report.token")
    reporter = StatusReporter(control_plane_url, device_id, token_path=token_path)
    # Build status dict
    status_data = reporter.get_device_status()
    if json_output:
        import json
        print(json.dumps(status_data, indent=2))
    else:
        for k, v in status_data.items():
            typer.echo(f"{k}: {v}")

    _echo_pending_gateway_key(reporter, device_id)


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




def _verify_plan_applicable(
    plan_data: dict[str, Any],
    *,
    current_hash: str,
    desired_commit: str,
    now: float,
) -> None:
    """Raise ValueError unless an approved plan still matches current state."""
    if plan_data.get("approval_status") != "approved":
        raise ValueError(f"Plan is not approved (status: {plan_data.get('approval_status')})")
    if float(plan_data.get("expiry_timestamp", 0)) <= now:
        raise ValueError("Plan has expired; run `holdfastctl plan` again")
    if plan_data.get("current_hash") != current_hash:
        raise ValueError("Local state has changed since approval; run `holdfastctl plan` again")
    if plan_data.get("desired_commit") != desired_commit:
        raise ValueError("Device manifest has changed since approval; run `holdfastctl plan` again")

def _reconcile_context_for(
    manifest_path: Path,
    catalog_path: Path,
    config_dir: Path,
) -> Any:
    """Build a ReconcileContext matching what reconcile_device uses internally."""
    from holdfastctl.capabilities import ReconcileContext, _sha256_hex
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest = yaml.safe_load(manifest_text) or {}
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    return ReconcileContext(
        device_id=(manifest.get("device") or {}).get("id", "unknown"),
        manifest_commit=_sha256_hex(manifest_text),
        credentials=manifest.get("credentials", []) or [],
        catalog=catalog,
        opencode_config_dir=config_dir,
    )

@app.command()
def plan(
    manifest_path: Path = typer.Option(  # noqa: B008 - idiomatic typer default
        Path("manifests/devices/jarod7736-laptop.yaml"), "--manifest", "-m", help="Device manifest YAML"
    ),
    catalog_path: Path = typer.Option(  # noqa: B008 - idiomatic typer default
        Path("manifests/catalogs/credentials.yaml"), "--catalog", help="Credential catalog YAML"
    ),
    config_dir: Path = typer.Option(  # noqa: B008 - idiomatic typer default
        Path.home() / ".config" / "opencode",  # noqa: B008 - idiomatic typer default
        "--config-dir",
        help="OpenCode config directory",
    ),
    local: bool = typer.Option(False, "--local", help="Print the plan without submitting it"),
    config_path: Path = typer.Option(  # noqa: B008 - idiomatic typer default
        Path.home() / ".config" / "holdfastctl" / "config.yaml",  # noqa: B008 - idiomatic typer default
        "--config",
        "-c",
        help="Agent config file",
    ),
) -> None:
    """Reconcile local state and submit a plan fingerprint to the control plane."""
    from holdfastctl.capabilities import reconcile_device

    results = reconcile_device(manifest_path, catalog_path, opencode_config_dir=config_dir)
    for capability, plans in results.items():
        for p in plans:
            typer.echo(f"{p.action} {p.target} ({capability})")

    if local:
        typer.echo("\nLocal plan only; not submitted.")
        return

    config = _load_agent_config(config_path)
    device_id = _resolve_device_id(config)
    control_plane_url = config.get("control_plane_url", "https://holdfast.tail1c66ec.ts.net")
    token_path = config.get("token_path") or str(config_path.parent / "report.token")

    state = collect_device_state(manifest_path, catalog_path, opencode_config_dir=config_dir)
    current_hash = device_state_fingerprint(state)
    desired_commit = state["manifest_commit"]

    reporter = StatusReporter(control_plane_url, device_id, token_path=token_path)
    try:
        created = reporter.create_plan(desired_commit, current_hash)
    except ReportingError as e:
        typer.echo(f"Error: plan submission to {control_plane_url} failed: {e}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"\nPlan {created['id']} submitted ({created['approval_status']}).")
    typer.echo(f"Approve with: holdfastctl approve {created['id']} --device {device_id} --control-plane {control_plane_url}")


@app.command()
def approve(
    plan_id: str = typer.Argument(..., help="Plan id to approve"),
    device_id_opt: str = typer.Option(..., "--device", "-d", help="Device the plan belongs to"),
    control_plane: str = typer.Option(..., "--control-plane", help="Control plane base URL"),
) -> None:
    """Approve a plan. Operator command: requires HOLDFAST_ADMIN_TOKEN."""
    import os

    from holdfastctl.reporting import StatusReporter
    admin_token = os.environ.get("HOLDFAST_ADMIN_TOKEN")
    if not admin_token:
        typer.echo("Error: HOLDFAST_ADMIN_TOKEN is not set", err=True)
        raise typer.Exit(code=1)
    reporter = StatusReporter(control_plane, device_id_opt)
    try:
        current = reporter.get_plan(plan_id, admin_token=admin_token)
    except ReportingError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    try:
        reporter.approve_plan(plan_id, current["current_hash"], current["desired_commit"], admin_token)
    except ReportingError as e:
        typer.echo(f"Error: approval failed: {e}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Plan {plan_id} approved.")

@app.command()
def apply(
    plan_id: str = typer.Argument(..., help="Approved plan id"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print what would change and exit without writing"),
    manifest_path: Path = typer.Option(  # noqa: B008 - idiomatic typer default
        Path("manifests/devices/jarod7736-laptop.yaml"), "--manifest", "-m", help="Device manifest YAML"
    ),
    catalog_path: Path = typer.Option(  # noqa: B008 - idiomatic typer default
        Path("manifests/catalogs/credentials.yaml"), "--catalog", help="Credential catalog YAML"
    ),
    config_dir: Path = typer.Option(  # noqa: B008 - idiomatic typer default
        Path.home() / ".config" / "opencode",  # noqa: B008 - idiomatic typer default
        "--config-dir",
        help="OpenCode config directory",
    ),
    config_path: Path = typer.Option(  # noqa: B008 - idiomatic typer default
        Path.home() / ".config" / "holdfastctl" / "config.yaml",  # noqa: B008 - idiomatic typer default
        "--config",
        "-c",
        help="Agent config file",
    ),
) -> None:
    """Apply an approved plan after re-verifying that local state still matches."""
    import time

    from holdfastctl.backup import BackupManager
    from holdfastctl.capabilities import (
        ADAPTERS,
        reconcile_device,
    )
    from holdfastctl.reporting import ReportingError, StatusReporter
    config = _load_agent_config(config_path)
    device_id = _resolve_device_id(config)
    control_plane_url = config.get("control_plane_url", "http://127.0.0.1:8000")
    token_path = config.get("token_path") or str(config_path.parent / "report.token")
    state = collect_device_state(manifest_path, catalog_path, opencode_config_dir=config_dir)
    current_hash = device_state_fingerprint(state)
    desired_commit = state["manifest_commit"]
    reporter = StatusReporter(control_plane_url, device_id, token_path=token_path)
    try:
        plan_data = reporter.get_plan(plan_id)
    except ReportingError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    try:
        _verify_plan_applicable(
            plan_data,
            current_hash=current_hash,
            desired_commit=desired_commit,
            now=time.time(),
        )
    except ValueError as e:
        typer.echo(f"Error: refusing to apply. {e}", err=True)
        raise typer.Exit(code=1)
    results = reconcile_device(manifest_path, catalog_path, opencode_config_dir=config_dir)
    if dry_run:
        for capability, plans in results.items():
            for p in plans:
                typer.echo(f"would {p.action} {p.target} ({capability})")
        typer.echo("\nDry run; nothing written.")
        return
    backup_manager = BackupManager()
    entries: list[dict[str, str | int]] = []
    try:
        for capability, plans in results.items():
            adapter = ADAPTERS[capability]
            applier = getattr(adapter, "apply", None)
            if applier is None:
                continue
            for p in plans:
                kind = p.target.split(":", 1)[0]
                if p.action != "add" or kind not in ("provider", "mcp_server"):
                    typer.echo(f"skip {p.action} {p.target} (advisory; fix by hand)")
                    continue
                context = _reconcile_context_for(manifest_path, catalog_path, config_dir)
                entries.append(applier(p, context, backup_manager=backup_manager))
                typer.echo(f"{p.action} {p.target}")
    except Exception as e:  # noqa: BLE001 - any apply failure triggers rollback below
        typer.echo(f"Error: apply failed: {e}; restoring", err=True)
        for entry in entries:
            if entry["backup"]:
                backup_manager.restore_from_backup(Path(str(entry["target"])), Path(str(entry["backup"])))
        raise typer.Exit(code=1)
    backup_manager.write_manifest(plan_id, entries)
    typer.echo(f"\nApplied plan {plan_id}. Roll back with: holdfastctl rollback {plan_id}")

@app.command()
def rollback(
    plan_id: str = typer.Argument(..., help="Plan id to roll back"),
) -> None:
    """Restore every file a plan backed up."""
    from holdfastctl.backup import BackupManager
    backup_manager = BackupManager()
    entries = backup_manager.read_manifest(plan_id)
    if not entries:
        typer.echo(f"Error: no backup manifest for plan {plan_id}", err=True)
        raise typer.Exit(code=1)
    for entry in entries:
        target = Path(str(entry["target"]))
        backup = str(entry["backup"])
        if not backup:
            target.unlink(missing_ok=True)
            typer.echo(f"removed {target} (did not exist before apply)")
            continue
        backup_manager.restore_from_backup(target, Path(backup))
        os.chmod(target, int(entry["mode"]))
        typer.echo(f"restored {target}")
    typer.echo(f"\nRolled back plan {plan_id}.")

if __name__ == "__main__":
    app()

