"""
Tests for the LiteLLM gateway admin client (key minting only).
"""

import pytest

from server.gateway import GatewayError, LiteLLMClient


def test_generate_key_returns_key_and_alias():
    """A 200 response with a key yields (raw_key, alias) and posts the right payload."""
    calls: dict = {}

    def fake_post(url, headers, json_body):
        calls["url"] = url
        calls["headers"] = headers
        calls["body"] = json_body
        return 200, {"key": "sk-minted"}

    client = LiteLLMClient("http://gw:4000/", "admin-token", http_post=fake_post)
    key, alias = client.generate_key("device-a", ["or-cheap"], ["github"])
    assert key == "sk-minted"
    assert alias.startswith("holdfast-device-a-")
    assert calls["url"] == "http://gw:4000/key/generate"
    assert calls["headers"] == {"Authorization": "Bearer admin-token"}
    assert calls["body"]["key_alias"] == alias
    assert calls["body"]["models"] == ["or-cheap"]
    assert calls["body"]["metadata"]["device_id"] == "device-a"
    assert calls["body"]["metadata"]["mcp_servers"] == ["github"]
    assert calls["body"]["metadata"]["managed_by"] == "holdfast-control"


def test_generate_key_unreachable_raises():
    """A network failure (http_post returns None) raises GatewayError."""
    client = LiteLLMClient("http://gw:4000", "t", http_post=lambda url, headers, body: None)
    with pytest.raises(GatewayError, match="unreachable"):
        client.generate_key("device-a", [], [])


def test_generate_key_error_status_raises_without_leaking_body():
    """A non-200 response raises GatewayError whose message never contains the body."""
    client = LiteLLMClient(
        "http://gw:4000", "t", http_post=lambda url, headers, body: (500, {"key": "sk-leak", "error": "boom"})
    )
    with pytest.raises(GatewayError) as excinfo:
        client.generate_key("device-a", [], [])
    assert "sk-leak" not in str(excinfo.value)
    assert "boom" not in str(excinfo.value)


def test_generate_key_missing_key_in_body_raises():
    """A 200 response without a string 'key' field raises GatewayError."""
    client = LiteLLMClient("http://gw:4000", "t", http_post=lambda url, headers, body: (200, {"status": "ok"}))
    with pytest.raises(GatewayError):
        client.generate_key("device-a", [], [])
