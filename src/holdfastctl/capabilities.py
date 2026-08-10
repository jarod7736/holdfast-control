"""
Capability adapters for device reconciliation.

Each capability type declared in a device manifest (opencode, network,
gateway_access) is reconciled by its own adapter. Adapters read current
state through injectable probes on the ReconcileContext so reconciliation
is testable offline and never fabricates drift on probe failure.
"""

import hashlib
import json
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from holdfastctl.reconcile import (
    ConfigurationPlan,
    generate_opencode_plan,
    load_current_opencode_state,
    normalize_id,
)

if TYPE_CHECKING:
    from holdfastctl.backup import BackupManager

Resolver = Callable[[str], str | None]
HttpGet = Callable[..., tuple[int, Any] | None]
#: (url, api_key) -> (status, reported server name), or None on transport failure.
McpProbe = Callable[[str, str | None], tuple[int, str | None] | None]

def _server_name_from_response(text: str) -> str | None:
    """Pull result.serverInfo.name out of an initialize response.

    Handles both a plain JSON body and the SSE framing (`data: {...}`) the
    LiteLLM gateway replies with.
    """
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("data:"):
            line = line[len("data:") :].strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        result = payload.get("result")
        server_info = result.get("serverInfo") if isinstance(result, dict) else None
        name = server_info.get("name") if isinstance(server_info, dict) else None
        if name:
            return str(name)
    return None


def default_mcp_probe(url: str, api_key: str | None) -> tuple[int, str | None] | None:
    """POST an MCP `initialize` and report (status, the server name it identifies as).

    Returns None on transport failure so an unreachable network is never read as
    drift. A gateway URL whose path matches no MCP route is rejected outright;
    one whose alias does not match answers 200 under a different server name.
    """
    import requests

    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "holdfastctl", "version": "1"},
        },
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return response.status_code, None
    return response.status_code, _server_name_from_response(response.text)


def default_resolve_host(fqdn: str) -> str | None:
    """Resolve a hostname to an IP, or None if it does not resolve."""
    import socket

    try:
        return socket.gethostbyname(fqdn)
    except OSError:
        return None


def default_http_get(url: str, headers: dict[str, str] | None = None) -> tuple[int, Any] | None:
    """GET a URL with a short timeout. Returns (status, parsed body) or None on network failure."""
    import requests

    try:
        response = requests.get(url, headers=headers, timeout=5)
    except requests.RequestException:
        return None
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text
    return response.status_code, body


@dataclass
class ReconcileContext:
    """Shared inputs for every capability adapter run on one device."""

    device_id: str
    manifest_commit: str
    credentials: list[dict[str, Any]]
    catalog: dict[str, Any]
    opencode_config_dir: Path
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    resolve_host: Resolver = default_resolve_host
    http_get: HttpGet = default_http_get
    mcp_probe: McpProbe = default_mcp_probe


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _plan(
    context: ReconcileContext,
    *,
    action: str,
    target: str,
    description: str,
    state: Any,
) -> ConfigurationPlan:
    """Build a ConfigurationPlan whose current_hash fingerprints the probed state."""
    return ConfigurationPlan(
        plan_id=str(uuid.uuid4()),
        device_id=context.device_id,
        desired_commit=context.manifest_commit,
        current_hash=_sha256_hex(json.dumps(state, sort_keys=True, default=str)),
        expiry_timestamp=(datetime.now(UTC) + timedelta(hours=24)).timestamp(),
        action=action,
        target=target,
        source="desired",
        description=description,
        checksum=_sha256_hex(target),
    )


class OpencodeAdapter:
    """Reconcile opencode.json against the capability's declared providers/mcp servers."""

    name = "opencode"

    def reconcile(self, desired: dict[str, Any], context: ReconcileContext) -> list[ConfigurationPlan]:
        providers_by_id = {p["id"]: p for p in context.catalog.get("providers", [])}
        mcp_by_id = {m["id"]: m for m in context.catalog.get("mcp-servers", [])}
        desired_state = {
            "device_id": context.device_id,
            "providers": [providers_by_id[pid] for pid in desired.get("providers", []) if pid in providers_by_id],
            "mcp_servers": [mcp_by_id[mid] for mid in desired.get("mcp_servers", []) if mid in mcp_by_id],
            "credentials": context.credentials,
            "manifest_commit": context.manifest_commit,
        }
        current = load_current_opencode_state(context.opencode_config_dir)
        plans = generate_opencode_plan(current, desired_state)
        plans.extend(self._probe_mcp_urls(current, context))
        return plans

    def apply(
        self,
        plan: ConfigurationPlan,
        context: ReconcileContext,
        *,
        backup_manager: "BackupManager",
        allowed_prefixes: tuple[Path, ...] | None = None,
    ) -> dict[str, str | int]:
        """Merge one plan action into opencode.json, preserving everything else.

        Returns a backup-manifest entry: {"target", "backup", "mode"}. Only
        provider and mcp_server targets are applicable; anything else (env
        references, repair probes) is advisory and rejected here.
        """
        import json as _json

        from holdfastctl.apply import DEFAULT_MANAGED_PREFIXES, ApplyError, atomic_write

        config_file = context.opencode_config_dir / "opencode.json"
        prefixes = allowed_prefixes if allowed_prefixes is not None else DEFAULT_MANAGED_PREFIXES

        config: dict[str, Any] = {}
        if config_file.is_file():
            config = _json.loads(config_file.read_text(encoding="utf-8"))

        kind, _, name = plan.target.partition(":")
        data = plan.target_data or {}

        if kind == "provider":
            providers = config.setdefault("provider", {})
            entry = providers.setdefault(name, {})
            options = entry.setdefault("options", {})
            if data.get("base_url"):
                options["baseURL"] = data["base_url"]
            entry.setdefault("name", data.get("name", name))
        elif kind == "mcp_server":
            servers = config.setdefault("mcp", {})
            entry = servers.setdefault(name, {})
            if data.get("url"):
                entry["url"] = data["url"]
            entry.setdefault("type", "remote")
            entry.setdefault("enabled", True)
        else:
            raise ApplyError(f"OpencodeAdapter cannot apply target '{plan.target}'")

        mode = config_file.stat().st_mode & 0o777 if config_file.exists() else 0o600
        backup_path = backup_manager.create_backup(config_file)
        atomic_write(config_file, _json.dumps(config, indent=2) + "\n", allowed_prefixes=prefixes)

        return {"target": str(config_file), "backup": str(backup_path) if backup_path else "", "mode": mode}

    def _probe_mcp_urls(self, current: dict[str, Any], context: ReconcileContext) -> list[ConfigurationPlan]:
        """Check that each configured MCP URL actually reaches the server it names.

        A URL can be reachable and still be wrong: an alias the gateway does not
        recognize answers 200 as the aggregate gateway, exposing a different tool
        set under a name that is not the one configured.
        """
        plans: list[ConfigurationPlan] = []
        api_key = context.env.get("LITELLM_API_KEY")
        for name, entry in current["mcp_servers"].items():
            url = entry.get("url", "")
            if not url:
                continue
            result = context.mcp_probe(url, api_key)
            if result is None:
                continue  # probe failure is unknown state, not drift
            status, server_name = result
            if status != 200:
                description = f"MCP server '{name}' at {url} returned HTTP {status} - URL matches no gateway MCP route"
            elif server_name and normalize_id(server_name) != normalize_id(name):
                description = f"MCP server '{name}' at {url} reports '{server_name}' - URL does not select this server"
            else:
                continue
            plans.append(
                _plan(
                    context,
                    action="repair",
                    target=f"mcp_server_url:{name}",
                    description=description,
                    state={"name": name, "url": url, "status": status, "server_name": server_name},
                )
            )
        return plans


class NetworkAdapter:
    """Check the device's DNS presence in the zone and gateway reachability.

    Any HTTP response (including 401/404) counts as reachable; only a network
    failure counts as unreachable.
    """

    name = "network"

    def reconcile(self, desired: dict[str, Any], context: ReconcileContext) -> list[ConfigurationPlan]:
        plans: list[ConfigurationPlan] = []
        hostname = desired.get("hostname") or context.device_id
        dns_zone = desired.get("dns_zone", "holdfast.lan")
        fqdn = f"{hostname}.{dns_zone}"
        ip = context.resolve_host(fqdn)
        state = {"fqdn": fqdn, "ip": ip}
        if ip is None:
            plans.append(
                _plan(
                    context,
                    action="repair",
                    target=f"dns:{fqdn}",
                    description=f"'{fqdn}' does not resolve - device missing from {dns_zone} DNS",
                    state=state,
                )
            )
        gateway_url = desired.get("gateway_url")
        if gateway_url and context.http_get(f"{gateway_url}/health/liveliness") is None:
            plans.append(
                _plan(
                    context,
                    action="repair",
                    target=f"gateway:{gateway_url}",
                    description=f"gateway {gateway_url} is unreachable from this device",
                    state=state,
                )
            )
        return plans


class GatewayAccessAdapter:
    """Check the device's LiteLLM virtual-key scope against the manifest declaration.

    Declared mcp_servers are recorded in the manifest but not yet verified live;
    only model scope is checked against the gateway.
    """

    name = "gateway_access"

    def reconcile(self, desired: dict[str, Any], context: ReconcileContext) -> list[ConfigurationPlan]:
        key_env = desired.get("key_env", "LITELLM_API_KEY")
        gateway_url = desired["gateway_url"]
        key = context.env.get(key_env)
        if not key:
            return [
                _plan(
                    context,
                    action="add",
                    target=f"gateway_key:{key_env}",
                    description=f"no virtual key in {key_env} - enroll the device to mint one",
                    state={"key_env": key_env, "key_present": False},
                )
            ]
        response = context.http_get(f"{gateway_url}/v1/models", {"Authorization": f"Bearer {key}"})
        if response is None or response[0] != 200:
            return [
                _plan(
                    context,
                    action="unknown",
                    target="gateway:scope",
                    description="could not verify virtual-key scope against the gateway",
                    state={"status": None if response is None else response[0]},
                )
            ]
        live_models = {m.get("id") for m in response[1].get("data", [])}
        return [
            _plan(
                context,
                action="grant",
                target=f"model:{model}",
                description=f"declared model '{model}' is not accessible with this key",
                state={"live_models": sorted(m for m in live_models if m)},
            )
            for model in desired.get("models", [])
            if model not in live_models
        ]


ADAPTERS: dict[str, OpencodeAdapter | NetworkAdapter | GatewayAccessAdapter] = {
    adapter.name: adapter for adapter in (OpencodeAdapter(), NetworkAdapter(), GatewayAccessAdapter())
}


def collect_device_state(
    manifest_path: Path,
    catalog_path: Path,
    *,
    opencode_config_dir: Path,
) -> dict[str, Any]:
    """Collect current device state for fingerprinting.
    Returns a dict containing manifest and catalog data and a commit hash.
    """
    import yaml
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest = yaml.safe_load(manifest_text) or {}
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    # Compute manifest commit hash
    from hashlib import sha256
    manifest_commit = sha256(manifest_text.encode("utf-8")).hexdigest()
    return {
        "manifest": manifest,
        "catalog": catalog,
        "manifest_commit": manifest_commit,
    }

def device_state_fingerprint(state: dict[str, Any]) -> str:
    """Return a fingerprint (SHA256) of the given state dict."""
    import json
    from hashlib import sha256
    # Ensure deterministic ordering
    text = json.dumps(state, sort_keys=True, default=str)
    return sha256(text.encode("utf-8")).hexdigest()

def reconcile_device(

    manifest_path: Path,
    catalog_path: Path,
    *,
    opencode_config_dir: Path,
    env: Mapping[str, str] | None = None,
    resolve_host: Resolver | None = None,
    http_get: HttpGet | None = None,
) -> dict[str, list[ConfigurationPlan]]:
    """Load a device manifest and dispatch every declared capability to its adapter.

    Returns plans grouped by capability name. Unknown capability types raise
    ValueError (validated before any adapter runs).
    """
    import yaml

    from holdfastctl.manifest_schema import validate_capabilities

    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest = yaml.safe_load(manifest_text) or {}
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    capabilities = manifest.get("capabilities", {}) or {}
    validate_capabilities(capabilities)

    context = ReconcileContext(
        device_id=(manifest.get("device") or {}).get("id", "unknown"),
        manifest_commit=_sha256_hex(manifest_text),
        credentials=manifest.get("credentials", []) or [],
        catalog=catalog,
        opencode_config_dir=opencode_config_dir,
    )
    if env is not None:
        context.env = env
    if resolve_host is not None:
        context.resolve_host = resolve_host
    if http_get is not None:
        context.http_get = http_get

    return {
        name: ADAPTERS[name].reconcile(config or {}, context)
        for name, config in capabilities.items()
        if name in ADAPTERS
    }
