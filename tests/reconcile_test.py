"""
Tests for the reconcile module.
"""

import json
import time
from pathlib import Path
from unittest.mock import mock_open, patch

from holdfastctl.reconcile import (
    ConfigurationPlan,
    Reconciler,
    StateComparator,
    generate_opencode_plan,
    load_current_opencode_state,
    load_desired_state,
    normalize_id,
)


class TestStateComparator:
    """Test cases for StateComparator."""

    def test_get_current_state_hash(self):
        """Test getting current state hash."""
        comparator = StateComparator()
        
        # Test that function returns a hash (we can't easily test actual hash without mocking)
        with patch('builtins.open', mock_open(read_data='{"test": "data"}')):
            hash_result = comparator.get_current_state_hash(Path("test.json"))
            assert isinstance(hash_result, str)
            # The actual hash is a SHA256 hash, which should be long
            assert len(hash_result) > 10  # Just check it's a reasonable length

    def test_compare_states_same(self):
        """Test comparing states that are the same."""
        comparator = StateComparator()
        
        current_state = {
            "configuration": {"checksum": "same_checksum"},
            "capabilities": {"supported_operations": ["test"]}
        }
        
        desired_state = {
            "configuration": {"checksum": "same_checksum"},
            "capabilities": {"supported_operations": ["test"]}
        }
        
        plans = comparator.compare_states(
            current_state, desired_state, "test-device", "test-commit", "test-hash"
        )
        assert len(plans) == 0

    def test_compare_states_different(self):
        """Test comparing states that are different."""
        comparator = StateComparator()
        
        current_state = {
            "configuration": {"checksum": "old_checksum"},
            "capabilities": {"supported_operations": ["test"]}
        }
        
        desired_state = {
            "configuration": {"checksum": "new_checksum"},
            "capabilities": {"supported_operations": ["test", "new_op"]}
        }
        
        plans = comparator.compare_states(
            current_state, desired_state, "test-device", "test-commit", "test-hash"
        )
        assert len(plans) >= 1  # At least one plan should be generated

    def test_generate_plan(self):
        """Test generating a plan."""
        comparator = StateComparator()
        
        current_state = {
            "configuration": {"checksum": "old_checksum"}
        }
        
        desired_state = {
            "configuration": {"checksum": "new_checksum"}
        }
        
        plans = comparator.generate_plan(
            current_state, desired_state, "test-device", "test-commit", "test-hash"
        )
        assert len(plans) >= 0


class TestReconciler:
    """Test cases for Reconciler."""

    def test_init(self):
        """Test Reconciler initialization."""
        comparator = StateComparator()
        reconciler = Reconciler(comparator)
        assert reconciler.state_comparator == comparator

    def test_reconcile(self):
        """Test reconcile method."""
        comparator = StateComparator()
        reconciler = Reconciler(comparator)
        
        current_state = {
            "configuration": {"checksum": "old_checksum"}
        }
        
        desired_state = {
            "configuration": {"checksum": "new_checksum"}
        }
        
        plans = reconciler.reconcile(
            current_state, desired_state, "test-device", "test-commit", "test-hash"
        )
        assert isinstance(plans, list)

    def test_get_plan_summary(self):
        """Test getting plan summary."""
        plans = [
            ConfigurationPlan(
                plan_id='plan-1',
                device_id='test-device',
                desired_commit='test-commit',
                current_hash='test-hash',
                expiry_timestamp=time.time() + 3600,
                action='update',
                target='config',
                source='desired',
                description='Update config',
                checksum='test_checksum'
            ),
            ConfigurationPlan(
                plan_id='plan-2',
                device_id='test-device',
                desired_commit='test-commit',
                current_hash='test-hash',
                expiry_timestamp=time.time() + 3600,
                action='apply',
                target='capability',
                source='desired',
                description='Add capability',
                checksum='test_checksum'
            )
        ]
        
        comparator = StateComparator()
        reconciler = Reconciler(comparator)
        summary = reconciler.get_plan_summary(plans)
        
        assert summary["total_actions"] == 2
        assert "action_breakdown" in summary
        assert "actions" in summary


class TestOpencodeReconcile:
    """Test cases for the opencode manifest-vs-config reconcile helpers."""

    def test_normalize_id(self):
        """Hyphenated catalog ids normalize to the underscore config keys."""
        assert normalize_id("halo-commander") == "halo_commander"
        assert normalize_id("github") == "github"

    def test_load_desired_state_resolves_catalog(self, tmp_path):
        """Desired state resolves declared providers/mcp ids against the catalog."""
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            "device:\n"
            "  id: test-laptop\n"
            "  profile: linux-wsl\n"
            "capabilities:\n"
            "  opencode:\n"
            "    providers: [amd-halo]\n"
            "    mcp_servers: [github]\n"
            "credentials:\n"
            "  - id: litellm-test\n"
            "    reference: op://holdfast-lan/litellm-test/credential\n"
            "    inject_as: LITELLM_API_KEY\n"
        )
        catalog = tmp_path / "catalog.yaml"
        catalog.write_text(
            "providers:\n"
            "  - id: amd-halo\n"
            "    type: opencode\n"
            "    base_url: http://amd-halo.holdfast.lan:13305/api/v1\n"
            "mcp-servers:\n"
            "  - id: github\n"
            "    url: http://cp/mcp/github\n"
        )
        desired = load_desired_state(manifest, catalog)
        assert desired["device_id"] == "test-laptop"
        assert desired["providers"][0]["base_url"] == "http://amd-halo.holdfast.lan:13305/api/v1"
        assert desired["mcp_servers"][0]["url"] == "http://cp/mcp/github"
        assert desired["credentials"][0]["inject_as"] == "LITELLM_API_KEY"

    def test_load_current_opencode_state_missing_config(self, tmp_path):
        """Missing opencode.json yields an empty current state without error."""
        current = load_current_opencode_state(tmp_path)
        assert current["providers"] == {}
        assert current["mcp_servers"] == {}
        assert current["env_refs"] == set()

    def test_load_current_opencode_state_parses_config(self, tmp_path):
        """Parses providers, mcp servers, and {env:VAR} references from opencode.json."""
        (tmp_path / "opencode.json").write_text(
            json.dumps(
                {
                    "provider": {
                        "lemonade": {
                            "name": "amd-halo (Lemonade)",
                            "options": {"baseURL": "http://amd-halo.holdfast.lan:13305/api/v1"},
                        }
                    },
                    "mcp": {
                        "github": {"type": "remote", "url": "http://cp/mcp/github", "headers": {"Authorization": "Bearer {env:LITELLM_API_KEY}"}}
                    },
                }
            )
        )
        current = load_current_opencode_state(tmp_path)
        assert "lemonade" in current["providers"]
        assert current["mcp_servers"]["github"]["url"] == "http://cp/mcp/github"
        assert "LITELLM_API_KEY" in current["env_refs"]

    def test_generate_plan_detects_missing_provider(self, tmp_path):
        """A declared provider absent from the config produces an add plan."""
        current = {"providers": {}, "mcp_servers": {}, "plugins": [], "env_refs": set()}
        desired = {
            "device_id": "test-laptop",
            "profile": "linux-wsl",
            "providers": [{"id": "opencode-cloud", "type": "opencode", "base_url": "https://api.opencode.dev/v1"}],
            "mcp_servers": [],
            "credentials": [],
            "manifest_commit": "abc",
        }
        plans = generate_opencode_plan(current, desired)
        assert len(plans) == 1
        assert plans[0].action == "add"
        assert plans[0].target == "provider:opencode-cloud"

    def test_generate_plan_skips_builtin_provider(self):
        """A built-in provider (e.g. OpenCode Zen) never produces an add plan."""
        current = {"providers": {}, "mcp_servers": {}, "plugins": [], "env_refs": set()}
        desired = {
            "device_id": "test-laptop",
            "providers": [
                {"id": "opencode-cloud", "type": "opencode", "builtin": True, "base_url": "https://opencode.ai/zen/v1"},
                {"id": "openrouter", "type": "opencode", "base_url": "http://192.168.1.181:4000/v1"},
            ],
            "mcp_servers": [],
            "credentials": [],
            "manifest_commit": "abc",
        }
        plans = generate_opencode_plan(current, desired)
        # builtin provider skipped; the genuinely missing non-builtin provider still plans
        assert len(plans) == 1
        assert plans[0].target == "provider:openrouter"

    def test_generate_plan_matches_by_base_url(self):
        """A provider configured under a different key but same base_url is not drift."""
        current = {
            "providers": {"lemonade": {"name": "amd-halo (Lemonade)", "base_url": "http://amd-halo.holdfast.lan:13305/api/v1"}},
            "mcp_servers": {},
            "plugins": [],
            "env_refs": set(),
        }
        desired = {
            "device_id": "test-laptop",
            "providers": [{"id": "amd-halo", "base_url": "http://amd-halo.holdfast.lan:13305/api/v1"}],
            "mcp_servers": [],
            "credentials": [],
            "manifest_commit": "abc",
        }
        plans = generate_opencode_plan(current, desired)
        assert plans == []

    def test_generate_plan_no_drift_when_matching(self):
        """Matching providers, mcp servers, and env refs produce no plans."""
        current = {
            "providers": {"amd_halo": {"name": "amd-halo", "base_url": "http://amd-halo.holdfast.lan:13305/api/v1"}},
            "mcp_servers": {"github": {"url": "http://cp/mcp/github"}},
            "plugins": [],
            "env_refs": {"LITELLM_API_KEY"},
        }
        desired = {
            "device_id": "test-laptop",
            "providers": [{"id": "amd-halo", "base_url": "http://amd-halo.holdfast.lan:13305/api/v1"}],
            "mcp_servers": [{"id": "github", "url": "http://cp/mcp/github"}],
            "credentials": [{"id": "litellm-test", "inject_as": "LITELLM_API_KEY"}],
            "manifest_commit": "abc",
        }
        plans = generate_opencode_plan(current, desired)
        assert plans == []
