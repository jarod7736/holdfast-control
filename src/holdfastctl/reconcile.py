"""
State comparison and reconciliation module for Holdfast Control.
This module provides capabilities for comparing device states and generating configuration plans.
"""

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass
class ConfigurationPlan:
    """Represents a configuration plan for applying changes."""
    plan_id: str
    device_id: str
    desired_commit: str
    current_hash: str
    expiry_timestamp: float
    action: str
    target: str
    source: str
    description: str
    checksum: str
    target_data: dict[str, Any] | None = None
    approval_status: str = "pending"  # pending, approved, expired, rejected
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class StateComparator:
    """Compare current and desired device states."""

    def get_current_state_hash(self, state_file: str | Path) -> str:
        """
        Get a hash of the current state.
        
        Args:
            state_file: Path to the state file
            
        Returns:
            SHA256 hash of the current state (deterministic canonical hash)
        """
        try:
            with open(state_file, 'r') as f:
                content = f.read()
                # Ensure deterministic hashing by sorting keys and using consistent formatting
                data = json.loads(content)
                sorted_content = json.dumps(data, sort_keys=True, separators=(',', ':'))
                return hashlib.sha256(sorted_content.encode()).hexdigest()
        except (OSError, json.JSONDecodeError):
            # If we can't read the file, return a dummy hash
            return hashlib.sha256(b"unknown").hexdigest()

    def compare_states(self, current_state: dict[str, Any], desired_state: dict[str, Any], 
                      device_id: str, desired_commit: str, current_hash: str) -> list[ConfigurationPlan]:
        """
        Compare current and desired states to generate plans.
        
        Args:
            current_state: Current device state
            desired_state: Desired device state
            device_id: Device identifier
            desired_commit: Desired git commit
            current_hash: Current state hash
            
        Returns:
            List of configuration plans with ID and metadata
        """
        plans = []
        
        # Compare configuration
        if 'configuration' in current_state and 'configuration' in desired_state:
            current_config = current_state['configuration']
            desired_config = desired_state['configuration']
            
            if current_config != desired_config:
                # Generate a plan for updating configuration
                plan_id = str(uuid.uuid4())
                expiry = datetime.now(UTC) + timedelta(hours=24)  # 24 hour expiry
                
                plans.append(ConfigurationPlan(
                    plan_id=plan_id,
                    device_id=device_id,
                    desired_commit=desired_commit,
                    current_hash=current_hash,
                    expiry_timestamp=expiry.timestamp(),
                    action='update',
                    target='configuration',
                    source='desired',
                    description='Update configuration',
                    checksum=self._generate_checksum(desired_config),
                    target_data=desired_config
                ))
        
        # Compare capabilities
        if 'capabilities' in current_state and 'capabilities' in desired_state:
            current_capabilities = current_state['capabilities']
            desired_capabilities = desired_state['capabilities']
            
            # Simple comparison for now - in real implementation would be more sophisticated
            if current_capabilities != desired_capabilities:
                plan_id = str(uuid.uuid4())
                expiry = datetime.now(UTC) + timedelta(hours=24)  # 24 hour expiry
                
                plans.append(ConfigurationPlan(
                    plan_id=plan_id,
                    device_id=device_id,
                    desired_commit=desired_commit,
                    current_hash=current_hash,
                    expiry_timestamp=expiry.timestamp(),
                    action='update',
                    target='capabilities',
                    source='desired',
                    description='Update capabilities',
                    checksum=self._generate_checksum(desired_capabilities),
                    target_data=desired_capabilities
                ))
        
        return plans

    def generate_plan(self, current_state: dict[str, Any], desired_state: dict[str, Any], 
                     device_id: str, desired_commit: str, current_hash: str) -> list[ConfigurationPlan]:
        """
        Generate a configuration plan for reconciling states.
        
        Args:
            current_state: Current device state
            desired_state: Desired device state
            device_id: Device identifier
            desired_commit: Desired git commit
            current_hash: Current state hash
            
        Returns:
            List of configuration plans
        """
        return self.compare_states(current_state, desired_state, device_id, desired_commit, current_hash)

    def _generate_checksum(self, data: dict[str, Any]) -> str:
        """Generate a checksum for data (deterministic canonical hash)."""
        # Ensure deterministic serialization
        data_str = json.dumps(data, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(data_str.encode()).hexdigest()


class Reconciler:
    """Reconcile device states by applying configuration plans."""

    def __init__(self, state_comparator: StateComparator):
        """Initialize the reconciler with a state comparator."""
        self.state_comparator = state_comparator

    def reconcile(self, current_state: dict[str, Any], desired_state: dict[str, Any], 
                 device_id: str, desired_commit: str, current_hash: str) -> list[ConfigurationPlan]:
        """
        Reconcile current and desired states.
        
        Args:
            current_state: Current device state
            desired_state: Desired device state
            device_id: Device identifier
            desired_commit: Desired git commit
            current_hash: Current state hash
            
        Returns:
            List of configuration plans to apply
        """
        return self.state_comparator.compare_states(current_state, desired_state, device_id, desired_commit, current_hash)

    def get_plan_summary(self, plans: list[ConfigurationPlan]) -> dict[str, Any]:
        """
        Get a summary of the configuration plans.
        
        Args:
            plans: List of configuration plans
            
        Returns:
            Summary of plans
        """
        summary: dict[str, Any] = {
            "total_actions": len(plans),
            "action_breakdown": {},
            "actions": []
        }
        
        # Group actions by type
        for plan in plans:
            action = plan.action
            if action not in summary["action_breakdown"]:
                summary["action_breakdown"][action] = 0
            summary["action_breakdown"][action] += 1
            
            # Add plan details
            plan_summary = {
                "action": plan.action,
                "target": plan.target,
                "source": plan.source,
                "description": plan.description,
                "plan_id": plan.plan_id
            }
            summary["actions"].append(plan_summary)
            
        return summary

    def is_plan_expired(self, plan: ConfigurationPlan) -> bool:
        """Check if a plan has expired."""
        return time.time() > plan.expiry_timestamp

    def approve_plan(self, plan: ConfigurationPlan) -> None:
        """Approve a plan."""
        plan.approval_status = "approved"

    def reject_plan(self, plan: ConfigurationPlan) -> None:
        """Reject a plan."""
        plan.approval_status = "rejected"


def normalize_id(value: str) -> str:
    """Normalize an id for comparison (catalog ids use hyphens, opencode config keys use underscores)."""
    return value.replace("-", "_")


def _sha256_hex(text: str) -> str:
    """Return the SHA256 hex digest of text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_desired_state(manifest_path: Path, catalog_path: Path) -> dict[str, Any]:
    """Load the desired device state from a device manifest and the credential catalog.

    Returns a structured dict with the device id, profile, declared providers,
    MCP servers, and credentials resolved against the catalog, plus a manifest
    commit fingerprint used as the desired-state reference.
    """
    import yaml

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    capabilities = manifest.get("capabilities", {})
    providers_by_id = {p["id"]: p for p in catalog.get("providers", [])}
    mcp_by_id = {m["id"]: m for m in catalog.get("mcp-servers", [])}

    return {
        "device_id": (manifest.get("device") or {}).get("id", "unknown"),
        "profile": (manifest.get("device") or {}).get("profile", "unknown"),
        "opencode": capabilities.get("opencode", {}),
        "providers": [providers_by_id[pid] for pid in capabilities.get("providers", []) if pid in providers_by_id],
        "mcp_servers": [mcp_by_id[mid] for mid in capabilities.get("mcp_servers", []) if mid in mcp_by_id],
        "credentials": manifest.get("credentials", []),
        "manifest_commit": _sha256_hex(manifest_path.read_text(encoding="utf-8")),
    }


def load_current_opencode_state(config_dir: Path) -> dict[str, Any]:
    """Read the real opencode config and summarize its current state.

    Returns the provider and MCP server entries, plugins, and every {env:VAR}
    reference found in the raw config text.
    """
    config_file = config_dir / "opencode.json"
    current: dict[str, Any] = {
        "config_file": config_file,
        "providers": {},
        "mcp_servers": {},
        "plugins": [],
        "env_refs": set(),
    }
    if not config_file.is_file():
        return current
    raw = config_file.read_text(encoding="utf-8")
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError:
        return current

    current["env_refs"] = set(re.findall(r"\{env:([A-Z0-9_]+)\}", raw))
    for key, entry in (cfg.get("provider") or {}).items():
        options = entry.get("options", {}) if isinstance(entry, dict) else {}
        current["providers"][key] = {"name": entry.get("name", key), "base_url": options.get("baseURL", "")}
    for key, entry in (cfg.get("mcp") or {}).items():
        current["mcp_servers"][key] = {"url": entry.get("url", "") if isinstance(entry, dict) else ""}
    current["plugins"] = cfg.get("plugin", [])
    return current


def generate_opencode_plan(current: dict[str, Any], desired: dict[str, Any]) -> list[ConfigurationPlan]:
    """Generate a plan of actions to bring the real opencode config to the desired state.

    Providers and MCP servers are matched by normalized id (hyphens vs underscores)
    or by base_url, so naming drift does not produce spurious plans. Credentials are
    checked by their inject_as env var appearing as an {env:VAR} reference.
    """
    plans: list[ConfigurationPlan] = []
    # config_file is a Path and env_refs is a set; drop the former and sort the
    # latter so the fingerprint is serializable and deterministic.
    hashable_state = {
        k: (sorted(v) if isinstance(v, set) else v)
        for k, v in current.items()
        if k != "config_file"
    }
    current_hash = _sha256_hex(json.dumps(hashable_state, sort_keys=True))
    desired_commit = desired.get("manifest_commit", "unknown")
    device_id = desired.get("device_id", "unknown")
    expiry = (datetime.now(UTC) + timedelta(hours=24)).timestamp()

    current_provider_keys = {normalize_id(k) for k in current["providers"]}
    current_base_urls = {p.get("base_url", "") for p in current["providers"].values()}
    for provider in desired["providers"]:
        # Built-in providers (e.g. OpenCode Zen) connect via auth.json and are
        # never registered as opencode.json providers, so they must not generate
        # an add plan.
        if provider.get("builtin"):
            continue
        pid = provider["id"]
        matched = normalize_id(pid) in current_provider_keys or provider.get("base_url", "") in current_base_urls
        if not matched:
            plans.append(
                ConfigurationPlan(
                    plan_id=str(uuid.uuid4()),
                    device_id=device_id,
                    desired_commit=desired_commit,
                    current_hash=current_hash,
                    expiry_timestamp=expiry,
                    action="add",
                    target=f"provider:{pid}",
                    source="desired",
                    description=f"Add provider '{pid}' (base_url: {provider.get('base_url', 'catalog')})",
                    checksum=_sha256_hex(json.dumps(provider, sort_keys=True)),
                    target_data=provider,
                )
            )

    current_mcp_keys = {normalize_id(k) for k in current["mcp_servers"]}
    for mcp in desired["mcp_servers"]:
        mid = mcp["id"]
        if normalize_id(mid) not in current_mcp_keys:
            plans.append(
                ConfigurationPlan(
                    plan_id=str(uuid.uuid4()),
                    device_id=device_id,
                    desired_commit=desired_commit,
                    current_hash=current_hash,
                    expiry_timestamp=expiry,
                    action="add",
                    target=f"mcp_server:{mid}",
                    source="desired",
                    description=f"Add MCP server '{mid}' (url: {mcp.get('url', 'catalog')})",
                    checksum=_sha256_hex(json.dumps(mcp, sort_keys=True)),
                    target_data=mcp,
                )
            )

    for cred in desired["credentials"]:
        env_name = cred.get("inject_as", "")
        if env_name and env_name not in current["env_refs"]:
            plans.append(
                ConfigurationPlan(
                    plan_id=str(uuid.uuid4()),
                    device_id=device_id,
                    desired_commit=desired_commit,
                    current_hash=current_hash,
                    expiry_timestamp=expiry,
                    action="add",
                    target=f"env:{env_name}",
                    source="desired",
                    description=f"Config must reference {{env:{env_name}}} (credential '{cred.get('id')}')",
                    checksum=_sha256_hex(env_name),
                )
            )

    return plans
