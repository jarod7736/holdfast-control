"""
Tests for the inspect module.
"""

from unittest.mock import Mock, patch

import pytest

from holdfastctl.inspect import DeviceInspector
from holdfastctl.manifest_schema import DeviceInfo


@pytest.fixture
def mock_device_info():
    """Create a mock device info object."""
    return DeviceInfo(
        id="test-device-123",
        profile="test-profile",
        display_name="Test Device"
    )


@pytest.fixture
def inspector(mock_device_info):
    """Create a DeviceInspector instance."""
    return DeviceInspector(mock_device_info)


class TestDeviceInspector:
    """Test cases for DeviceInspector."""

    def test_init(self, mock_device_info):
        """Test DeviceInspector initialization."""
        inspector = DeviceInspector(mock_device_info)
        assert inspector.device_info == mock_device_info

    @patch('os.uname')
    def test_inspect_system(self, mock_uname, inspector):
        """Test system inspection."""
        mock_uname.return_value = Mock(
            nodename="test-host",
            sysname="Linux",
            release="5.4.0",
            version="#1 SMP",
            machine="x86_64"
        )
        
        system_info = inspector.inspect_system()
        assert system_info["device_id"] == "test-device-123"
        assert system_info["hostname"] == "test-host"
        assert system_info["os"]["name"] == "Linux"
        assert system_info["os"]["release"] == "5.4.0"

    @patch('os.path.exists')
    @patch('os.listdir')
    def test_get_network_info(self, mock_listdir, mock_exists, inspector):
        """Test network information gathering."""
        mock_exists.return_value = True
        mock_listdir.return_value = ["eth0", "lo"]
        
        network_info = inspector._get_network_info()
        # Check that interfaces are returned (we don't test exact content in this test)
        assert isinstance(network_info["interfaces"], list)

    def test_inspect_capabilities(self, inspector):
        """Test capability inspection."""
        capabilities = inspector.inspect_capabilities()
        assert "supported_operations" in capabilities
        assert "hardware" in capabilities

    def test_inspect_configuration(self, inspector):
        """Test configuration inspection."""
        config = inspector.inspect_configuration()
        assert config["device_id"] == "test-device-123"
        assert "configuration" in config

    def test_get_inspection_report(self, inspector):
        """Test complete inspection report."""
        report = inspector.get_inspection_report()
        assert "system" in report
        assert "capabilities" in report
        assert "configuration" in report