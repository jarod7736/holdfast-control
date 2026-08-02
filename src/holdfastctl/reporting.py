"""
Status reporting module for Holdfast Control.
This module provides capabilities for reporting device status and configuration changes.
"""

import datetime
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


@dataclass
class EnrollmentResult:
    """Outcome of exchanging an enrollment code: report token plus optional one-time gateway key."""

    report_token: str
    gateway_key: str | None = None
    gateway_key_alias: str | None = None


class ReportingError(Exception):
    """Exception raised when reporting operations fail."""


class StatusReporter:
    """Report device status and configuration changes to the control plane."""

    def __init__(self, control_plane_url: str, device_id: str, token_path: str | None = None):
        """Initialize the status reporter.

        Args:
            control_plane_url: URL of the control plane
            device_id: Unique identifier for this device
            token_path: Path where the report token is stored (mode 0600); created on first enrollment
        """
        self.control_plane_url = control_plane_url
        self.device_id = device_id
        self.token_path = token_path
        self.pending_gateway_key: str | None = None
        self.pending_gateway_key_alias: str | None = None

    def enroll(self, enrollment_code: str) -> EnrollmentResult:
        """Exchange an operator-provisioned one-time enrollment code for a report token.

        The response may include a one-time gateway virtual key; it is returned
        to the caller and never persisted.

        Raises:
            ReportingError: If enrollment fails
        """
        enroll_response = requests.post(
            f"{self.control_plane_url}/api/v1/enroll",
            json={"code": enrollment_code, "device_id": self.device_id},
        )
        if enroll_response.status_code != 200:
            raise ReportingError(f"Enrollment failed: {enroll_response.text}")
        body = enroll_response.json()
        token: Any = body.get("report_token")
        if not isinstance(token, str):
            raise ReportingError("Enrollment response missing report_token")
        gateway_key = body.get("gateway_key")
        gateway_key_alias = body.get("gateway_key_alias")
        return EnrollmentResult(
            report_token=token,
            gateway_key=gateway_key if isinstance(gateway_key, str) else None,
            gateway_key_alias=gateway_key_alias if isinstance(gateway_key_alias, str) else None,
        )

    def _load_or_create_token(self, enrollment_code: str | None = None) -> str:
        """Return the stored report token, or enroll using an operator-provisioned code (mode 0600).

        Raises:
            ReportingError: If no token is stored and no enrollment code was provided,
                or if enrollment fails
        """
        if self.token_path is not None:
            token_path = Path(self.token_path)
            if token_path.is_file():
                stored = token_path.read_text().strip()
                if stored:
                    return stored
        if not enrollment_code:
            raise ReportingError(
                "No report token found and no enrollment code provided. "
                "Ask the operator to mint a code (`holdfastctl enroll-code DEVICE_ID`) "
                "and retry with --enrollment-code."
            )
        result = self.enroll(enrollment_code)
        self.pending_gateway_key = result.gateway_key
        self.pending_gateway_key_alias = result.gateway_key_alias
        if self.token_path is not None:
            token_path = Path(self.token_path)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(result.report_token)
            os.chmod(token_path, 0o600)
        return result.report_token

    def report_status(self, status_data: dict[str, Any], enrollment_code: str | None = None) -> bool:
        """
        Report device status to the control plane.

        Args:
            status_data: Status information to report
            enrollment_code: Operator-provisioned one-time code used on first enrollment

        Returns:
            True if successful, False otherwise
            
        Raises:
            ReportingError: If reporting fails
        """
        try:
            token = self._load_or_create_token(enrollment_code)
            url = f"{self.control_plane_url}/api/v1/devices/{self.device_id}/reports"
            response = requests.post(
                url,
                json={"report_data": status_data},
                headers={"Authorization": f"Bearer {token}"},
            )

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

    def report_health(self, enrollment_code: str | None = None) -> bool:
        """
        Report device health status.
        
        Args:
            enrollment_code: Operator-provisioned one-time code used on first enrollment
            
        Returns:
            True if successful, False otherwise
        """
        try:
            status = self.reporter.get_device_status()
            return self.reporter.report_status(status, enrollment_code=enrollment_code)
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