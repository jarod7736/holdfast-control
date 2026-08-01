"""
Status reporting module for Holdfast Control.
This module provides capabilities for reporting device status and configuration changes.
"""

import datetime
from typing import Any

import requests


class ReportingError(Exception):
    """Exception raised when reporting operations fail."""


class StatusReporter:
    """Report device status and configuration changes to the control plane."""

    def __init__(self, control_plane_url: str, device_id: str):
        """Initialize the status reporter.
        
        Args:
            control_plane_url: URL of the control plane
            device_id: Unique identifier for this device
        """
        self.control_plane_url = control_plane_url
        self.device_id = device_id

    def report_status(self, status_data: dict[str, Any]) -> bool:
        """
        Report device status to the control plane.
        
        Args:
            status_data: Status information to report
            
        Returns:
            True if successful, False otherwise
            
        Raises:
            ReportingError: If reporting fails
        """
        try:
            url = f"{self.control_plane_url}/devices/{self.device_id}/status"
            response = requests.post(url, json=status_data)
            
            if response.status_code != 200:
                raise ReportingError(f"Failed to report status: {response.text}")
                
            return True
            
        except (ReportingError, requests.RequestException, ValueError) as e:
            raise ReportingError(f"Failed to report status: {e!s}")

    def report_configuration_change(self, old_config: dict[str, Any], new_config: dict[str, Any]) -> bool:
        """
        Report a configuration change to the control plane.
        
        Args:
            old_config: Previous configuration
            new_config: New configuration
            
        Returns:
            True if successful, False otherwise
            
        Raises:
            ReportingError: If reporting fails
        """
        try:
            url = f"{self.control_plane_url}/devices/{self.device_id}/config-change"
            data: dict[str, Any] = {
                "old_config": old_config,
                "new_config": new_config,
                "timestamp": datetime.datetime.now(datetime.UTC).isoformat()
            }
            response = requests.post(url, json=data)
            
            if response.status_code != 200:
                raise ReportingError(f"Failed to report configuration change: {response.text}")
                
            return True
            
        except (ReportingError, requests.RequestException, ValueError) as e:
            raise ReportingError(f"Failed to report configuration change: {e!s}")

    def report_error(self, error_message: str) -> bool:
        """
        Report an error to the control plane.
        
        Args:
            error_message: Error message to report
            
        Returns:
            True if successful, False otherwise
            
        Raises:
            ReportingError: If reporting fails
        """
        try:
            url = f"{self.control_plane_url}/devices/{self.device_id}/error"
            data: dict[str, Any] = {
                "error": error_message,
                "timestamp": datetime.datetime.now(datetime.UTC).isoformat()
            }
            response = requests.post(url, json=data)
            
            if response.status_code != 200:
                raise ReportingError(f"Failed to report error: {response.text}")
                
            return True
            
        except (ReportingError, requests.RequestException, ValueError) as e:
            raise ReportingError(f"Failed to report error: {e!s}")

    def get_device_status(self) -> dict[str, Any]:
        """
        Get current device status.
        
        Returns:
            Device status information
        """
        return {
            "device_id": self.device_id,
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "status": "healthy"
        }


class ReportingService:
    """High-level reporting service for device status."""

    def __init__(self, control_plane_url: str, device_id: str):
        """Initialize the reporting service.
        
        Args:
            control_plane_url: URL of the control plane
            device_id: Unique identifier for this device
        """
        self.reporter = StatusReporter(control_plane_url, device_id)

    def report_health(self) -> bool:
        """
        Report device health status.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            status = self.reporter.get_device_status()
            return self.reporter.report_status(status)
        except ReportingError:
            return False

    def report_change(self, old_config: dict[str, Any], new_config: dict[str, Any]) -> bool:
        """
        Report a configuration change.
        
        Args:
            old_config: Previous configuration
            new_config: New configuration
            
        Returns:
            True if successful, False otherwise
        """
        try:
            return self.reporter.report_configuration_change(old_config, new_config)
        except ReportingError:
            return False

    def report_error(self, error_message: str) -> bool:
        """
        Report an error.
        
        Args:
            error_message: Error message to report
            
        Returns:
            True if successful, False otherwise
        """
        try:
            return self.reporter.report_error(error_message)
        except ReportingError:
            return False