"""LiteLLM gateway admin client for the Holdfast control plane.

Mints scoped virtual keys via the gateway admin API. The admin token is
narrowly scoped to key management and comes from the holdfast-automation
vault, never holdfast-lan. Error messages never include response bodies,
which could contain key material.
"""

import secrets
from collections.abc import Callable
from typing import Any

HttpPost = Callable[[str, dict[str, str], dict[str, Any]], "tuple[int, Any] | None"]
KeyMinter = Callable[[str, list[str], list[str]], tuple[str, str]]


class GatewayError(Exception):
    """Raised when a LiteLLM admin API call fails."""


def default_http_post(url: str, headers: dict[str, str], json_body: dict[str, Any]) -> tuple[int, Any] | None:
    """POST JSON with a short timeout. Returns (status, parsed body) or None on network failure."""
    import requests

    try:
        response = requests.post(url, headers=headers, json=json_body, timeout=10)
    except requests.RequestException:
        return None
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text
    return response.status_code, body


class LiteLLMClient:
    """Minimal client for the LiteLLM proxy admin API (key management only)."""

    def __init__(self, base_url: str, admin_token: str, http_post: HttpPost = default_http_post):
        self.base_url = base_url.rstrip("/")
        self.admin_token = admin_token
        self.http_post = http_post

    def generate_key(self, device_id: str, models: list[str], mcp_servers: list[str]) -> tuple[str, str]:
        """Mint a virtual key scoped to models; returns (raw_key, key_alias).

        mcp_servers are recorded in key metadata; MCP route enforcement is
        configured gateway-side.
        """
        key_alias = f"holdfast-{device_id}-{secrets.token_hex(4)}"
        response = self.http_post(
            f"{self.base_url}/key/generate",
            {"Authorization": f"Bearer {self.admin_token}"},
            {
                "key_alias": key_alias,
                "models": models,
                "metadata": {
                    "managed_by": "holdfast-control",
                    "device_id": device_id,
                    "mcp_servers": mcp_servers,
                },
            },
        )
        if response is None:
            raise GatewayError("gateway unreachable")
        status_code, body = response
        if status_code != 200 or not isinstance(body, dict) or not isinstance(body.get("key"), str):
            raise GatewayError(f"key generation failed (status {status_code})")
        return body["key"], key_alias
