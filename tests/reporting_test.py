"""
Tests for the reporting module.
"""

from unittest.mock import patch

import pytest

from holdfastctl.reporting import ReportingError, ReportingService, StatusReporter


class TestStatusReporter:
    """Test cases for StatusReporter."""

    def test_init(self):
        """Test StatusReporter initialization."""
        reporter = StatusReporter("http://test-control-plane", "test-device-123")
        assert reporter.control_plane_url == "http://test-control-plane"
        assert reporter.device_id == "test-device-123"

    @patch('requests.post')
    def test_report_status_success(self, mock_post, tmp_path):
        """Test successful status reporting."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"status": "ok"}

        token_file = tmp_path / "report.token"
        token_file.write_text("test-token")
        reporter = StatusReporter("http://test-control-plane", "test-device-123", token_path=str(token_file))
        result = reporter.report_status({"status": "healthy"})
        assert result is True
        request = mock_post.call_args
        assert request.kwargs["headers"] == {"Authorization": "Bearer test-token"}
        assert request.kwargs["json"] == {"report_data": {"status": "healthy"}}
        assert request.args[0] == "http://test-control-plane/api/v1/devices/test-device-123/reports"

    @patch('requests.post')
    def test_report_status_failure(self, mock_post, tmp_path):
        """Test status reporting failure."""
        mock_post.return_value.status_code = 500
        mock_post.return_value.json.return_value = {"error": "server error"}

        token_file = tmp_path / "report.token"
        token_file.write_text("test-token")
        reporter = StatusReporter("http://test-control-plane", "test-device-123", token_path=str(token_file))
        with pytest.raises(ReportingError):
            reporter.report_status({"status": "healthy"})

    @patch('requests.post')
    def test_report_status_enrolls_with_provisioned_code(self, mock_post, tmp_path):
        """Test that reporting enrolls with an operator-provisioned code and persists it 0600."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"report_token": "fresh-token"}

        token_file = tmp_path / "report.token"
        reporter = StatusReporter("http://test-control-plane", "test-device-123", token_path=str(token_file))
        result = reporter.report_status({"status": "healthy"}, enrollment_code="code-123")
        assert result is True
        assert token_file.read_text() == "fresh-token"
        assert token_file.stat().st_mode & 0o777 == 0o600
        urls = [call.args[0] for call in mock_post.call_args_list]
        assert urls == [
            "http://test-control-plane/api/v1/enroll",
            "http://test-control-plane/api/v1/devices/test-device-123/reports",
        ]

    @patch('requests.post')
    def test_report_status_requires_code_when_no_token(self, mock_post, tmp_path):
        """Test that reporting raises when no token is stored and no code is provided."""
        token_file = tmp_path / "report.token"
        reporter = StatusReporter("http://test-control-plane", "test-device-123", token_path=str(token_file))
        with pytest.raises(ReportingError, match="no enrollment code provided"):
            reporter.report_status({"status": "healthy"})
        assert mock_post.call_count == 0

    @patch('requests.post')
    def test_report_status_reuses_stored_token(self, mock_post, tmp_path):
        """Test that an existing stored token is reused without re-enrolling."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"status": "ok"}

        token_file = tmp_path / "report.token"
        token_file.write_text("stored-token")
        reporter = StatusReporter("http://test-control-plane", "test-device-123", token_path=str(token_file))
        assert reporter.report_status({"status": "healthy"}) is True
        assert len(mock_post.call_args_list) == 1
        request = mock_post.call_args
        assert request.kwargs["headers"] == {"Authorization": "Bearer stored-token"}

    @patch('requests.post')
    def test_report_configuration_change_success(self, mock_post):
        """Test successful configuration change reporting."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"status": "ok"}
        
        reporter = StatusReporter("http://test-control-plane", "test-device-123")
        result = reporter.report_configuration_change(
            {"old": "config"}, 
            {"new": "config"}
        )
        assert result is True

    @patch('requests.post')
    def test_report_configuration_change_failure(self, mock_post):
        """Test configuration change reporting failure."""
        mock_post.return_value.status_code = 500
        mock_post.return_value.json.return_value = {"error": "server error"}
        
        reporter = StatusReporter("http://test-control-plane", "test-device-123")
        with pytest.raises(ReportingError):
            reporter.report_configuration_change(
                {"old": "config"}, 
                {"new": "config"}
            )

    @patch('requests.post')
    def test_report_error_success(self, mock_post):
        """Test successful error reporting."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"status": "ok"}
        
        reporter = StatusReporter("http://test-control-plane", "test-device-123")
        result = reporter.report_error("Test error message")
        assert result is True

    @patch('requests.post')
    def test_report_error_failure(self, mock_post):
        """Test error reporting failure."""
        mock_post.return_value.status_code = 500
        mock_post.return_value.json.return_value = {"error": "server error"}
        
        reporter = StatusReporter("http://test-control-plane", "test-device-123")
        with pytest.raises(ReportingError):
            reporter.report_error("Test error message")

    def test_get_device_status(self):
        """Test getting device status."""
        reporter = StatusReporter("http://test-control-plane", "test-device-123")
        status = reporter.get_device_status()

        assert status["device_id"] == "test-device-123"
        assert "timestamp" in status
        assert status["status"] == "healthy"

    @patch('requests.post')
    def test_enroll_returns_gateway_key_and_never_stores_it(self, mock_post, tmp_path):
        """A minted gateway key is surfaced on the reporter but never written to disk."""
        def responses(url, **kwargs):
            reply = type('R', (), {})()
            reply.status_code = 200
            if url.endswith('/api/v1/enroll'):
                reply.json = lambda: {
                    "report_token": "fresh-token",
                    "gateway_key": "sk-minted-key",
                    "gateway_key_alias": "holdfast-test-device-123-aa11",
                }
            else:
                reply.json = lambda: {"status": "accepted"}
            return reply

        mock_post.side_effect = responses
        token_file = tmp_path / "report.token"
        reporter = StatusReporter("http://cp", "test-device-123", token_path=str(token_file))
        assert reporter.report_status({"status": "healthy"}, enrollment_code="code-123") is True
        assert reporter.pending_gateway_key == "sk-minted-key"
        assert reporter.pending_gateway_key_alias == "holdfast-test-device-123-aa11"
        assert token_file.read_text() == "fresh-token"
        assert "sk-minted-key" not in token_file.read_text()

    @patch('requests.post')
    def test_enroll_without_gateway_key_leaves_pending_none(self, mock_post, tmp_path):
        """Enrollment without a minted key leaves pending_gateway_key as None."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"report_token": "fresh-token"}
        token_file = tmp_path / "report.token"
        reporter = StatusReporter("http://cp", "test-device-123", token_path=str(token_file))
        reporter.report_status({"status": "healthy"}, enrollment_code="code-123")
        assert reporter.pending_gateway_key is None


class TestReportingService:
    """Test cases for ReportingService."""

    def test_init(self):
        """Test ReportingService initialization."""
        service = ReportingService("http://test-control-plane", "test-device-123")
        assert service.reporter is not None

    @patch('requests.post')
    def test_report_health_success(self, mock_post):
        """Test successful health reporting."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"report_token": "health-token"}

        service = ReportingService("http://test-control-plane", "test-device-123")
        result = service.report_health(enrollment_code="code-123")
        assert result is True

    @patch('requests.post')
    def test_report_health_failure(self, mock_post):
        """Test health reporting failure."""
        mock_post.return_value.status_code = 500
        mock_post.return_value.json.return_value = {"error": "server error"}
        
        service = ReportingService("http://test-control-plane", "test-device-123")
        result = service.report_health(enrollment_code="code-123")
        assert result is False

    @patch('requests.post')
    def test_report_change_success(self, mock_post):
        """Test successful change reporting."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"status": "ok"}
        
        service = ReportingService("http://test-control-plane", "test-device-123")
        result = service.report_change({"old": "config"}, {"new": "config"})
        assert result is True

    @patch('requests.post')
    def test_report_change_failure(self, mock_post):
        """Test change reporting failure."""
        mock_post.return_value.status_code = 500
        mock_post.return_value.json.return_value = {"error": "server error"}
        
        service = ReportingService("http://test-control-plane", "test-device-123")
        result = service.report_change({"old": "config"}, {"new": "config"})
        assert result is False

    @patch('requests.post')
    def test_report_error_success(self, mock_post):
        """Test successful error reporting."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"status": "ok"}
        
        service = ReportingService("http://test-control-plane", "test-device-123")
        result = service.report_error("Test error message")
        assert result is True

    @patch('requests.post')
    def test_report_error_failure(self, mock_post):
        """Test error reporting failure."""
        mock_post.return_value.status_code = 500
        mock_post.return_value.json.return_value = {"error": "server error"}
        
        service = ReportingService("http://test-control-plane", "test-device-123")
        result = service.report_error("Test error message")
        assert result is False