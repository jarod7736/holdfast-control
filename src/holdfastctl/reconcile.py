"""
State comparison and reconciliation module for Holdfast Control.
This module provides capabilities for comparing device states and generating configuration plans.
"""

import hashlib
import json
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
