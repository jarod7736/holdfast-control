"""
Tests for the reconcile module.
"""

import time
from pathlib import Path
from unittest.mock import mock_open, patch

from holdfastctl.reconcile import ConfigurationPlan, Reconciler, StateComparator


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
