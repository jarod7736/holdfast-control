"""
Tests for the capability adapter layer.

Capabilities are adapter-keyed entries in the device manifest: `opencode`,
`network`, and `gateway_access`. Each adapter reconciles one aspect of a
device against its declared desired state.
"""

import json
from pathlib import Path

import pytest

from holdfastctl.capabilities import (
    ADAPTERS,
    ReconcileContext,
    reconcile_device,
)
from holdfastctl.manifest_schema import validate_capabilities


def make_context(
    tmp_path: Path,
    env: dict[str, str] | None = None,
    hosts: dict[str, str] | None = None,
    responses: dict[str, tuple[int, object]] | None = None,
) -> ReconcileContext:
    """Build a context with fake resolver/prober so tests never touch the network."""

    def resolve_host(fqdn: str) -> str | None:
        return (hosts or {}).get(fqdn)

    def http_get(url: str, headers: dict[str, str] | None = None) -> tuple[int, object] | None:
        return (responses or {}).get(url)

    return ReconcileContext(
        device_id="test-laptop",
        manifest_commit="abc",
        credentials=[{"id": "litellm-test", "inject_as": "LITELLM_API_KEY"}],
        catalog={},
        opencode_config_dir=tmp_path,
        env=env or {},
        resolve_host=resolve_host,
        http_get=http_get,
    )


class TestCapabilitySchema:
    """Capability entries validate against per-type schemas; unknown types are rejected."""

    def test_accepts_known_capability_types(self):
        validate_capabilities(
            {
                "opencode": {
                    "required": True,
                    "config_profile": "personal-development",
                    "providers": ["amd-halo"],
                    "mcp_servers": ["github"],
                },
                "network": {
                    "hostname": "test-laptop",
                    "dns_zone": "holdfast.lan",
                    "gateway_url": "http://192.168.1.181:4000",
                },
                "gateway_access": {
                    "gateway_url": "http://192.168.1.181:4000",
                    "models": ["or-cheap"],
                    "mcp_servers": ["github"],
                },
            }
        )

    def test_rejects_unknown_capability_type(self):
        with pytest.raises(ValueError, match="Unknown capability"):
            validate_capabilities({"ansible": {"playbook": "site.yml"}})

    def test_rejects_unknown_field_in_capability(self):
        with pytest.raises(ValueError):
            validate_capabilities({"network": {"hostname": "x", "bogus_field": 1}})


class TestNetworkAdapter:
    """The network adapter checks DNS resolution and gateway reachability."""

    def test_unresolvable_hostname_plans_repair(self, tmp_path):
        context = make_context(tmp_path, hosts={})
        plans = ADAPTERS["network"].reconcile({"hostname": "ghost", "dns_zone": "holdfast.lan"}, context)
        assert len(plans) == 1
        assert plans[0].action == "repair"
        assert plans[0].target == "dns:ghost.holdfast.lan"

    def test_resolved_and_gateway_reachable_no_drift(self, tmp_path):
        context = make_context(
            tmp_path,
            hosts={"test-laptop.holdfast.lan": "192.168.1.50"},
            responses={"http://gw:4000/health/liveliness": (200, "alive")},
        )
        plans = ADAPTERS["network"].reconcile(
            {"hostname": "test-laptop", "gateway_url": "http://gw:4000"}, context
        )
        assert plans == []

    def test_gateway_unreachable_plans_repair(self, tmp_path):
        context = make_context(
            tmp_path,
            hosts={"test-laptop.holdfast.lan": "192.168.1.50"},
            responses={},
        )
        plans = ADAPTERS["network"].reconcile(
            {"hostname": "test-laptop", "gateway_url": "http://gw:4000"}, context
        )
        assert len(plans) == 1
        assert plans[0].target == "gateway:http://gw:4000"

    def test_hostname_defaults_to_device_id(self, tmp_path):
        context = make_context(tmp_path, hosts={"test-laptop.holdfast.lan": "192.168.1.50"})
        plans = ADAPTERS["network"].reconcile({}, context)
        assert plans == []


class TestGatewayAccessAdapter:
    """The gateway_access adapter checks the device's LiteLLM virtual-key scope."""

    def test_missing_key_plans_enrollment(self, tmp_path):
        context = make_context(tmp_path, env={})
        plans = ADAPTERS["gateway_access"].reconcile(
            {"gateway_url": "http://gw:4000", "models": ["or-cheap"]}, context
        )
        assert len(plans) == 1
        assert plans[0].action == "add"
        assert plans[0].target == "gateway_key:LITELLM_API_KEY"

    def test_missing_model_plans_grant(self, tmp_path):
        context = make_context(
            tmp_path,
            env={"LITELLM_API_KEY": "test"},
            responses={"http://gw:4000/v1/models": (200, {"data": [{"id": "or-cheap"}]})},
        )
        plans = ADAPTERS["gateway_access"].reconcile(
            {"gateway_url": "http://gw:4000", "models": ["or-cheap", "or-opus"]}, context
        )
        assert len(plans) == 1
        assert plans[0].action == "grant"
        assert plans[0].target == "model:or-opus"

    def test_scope_in_sync_no_drift(self, tmp_path):
        context = make_context(
            tmp_path,
            env={"LITELLM_API_KEY": "test"},
            responses={"http://gw:4000/v1/models": (200, {"data": [{"id": "or-cheap"}, {"id": "or-opus"}]})},
        )
        plans = ADAPTERS["gateway_access"].reconcile(
            {"gateway_url": "http://gw:4000", "models": ["or-cheap", "or-opus"]}, context
        )
        assert plans == []

    def test_gateway_unreachable_reports_unknown(self, tmp_path):
        context = make_context(tmp_path, env={"LITELLM_API_KEY": "test"}, responses={})
        plans = ADAPTERS["gateway_access"].reconcile(
            {"gateway_url": "http://gw:4000", "models": ["or-cheap"]}, context
        )
        assert len(plans) == 1
        assert plans[0].action == "unknown"
        assert plans[0].target == "gateway:scope"


class TestOpencodeAdapter:
    """The opencode adapter reconciles opencode.json via the capability's nested declarations."""

    def test_missing_provider_plans_add(self, tmp_path):
        context = make_context(tmp_path)
        context.catalog = {
            "providers": [{"id": "amd-halo", "type": "opencode", "base_url": "http://halo:13305/api/v1"}],
            "mcp-servers": [],
        }
        plans = ADAPTERS["opencode"].reconcile({"providers": ["amd-halo"], "mcp_servers": []}, context)
        targets = [p.target for p in plans]
        assert "provider:amd-halo" in targets

    def test_configured_provider_no_drift(self, tmp_path):
        (tmp_path / "opencode.json").write_text(
            json.dumps(
                {
                    "provider": {"amd_halo": {"options": {"baseURL": "http://halo:13305/api/v1"}}},
                    "mcp": {},
                }
            )
        )
        context = make_context(tmp_path)
        context.credentials = []
        context.catalog = {
            "providers": [{"id": "amd-halo", "type": "opencode", "base_url": "http://halo:13305/api/v1"}],
            "mcp-servers": [],
        }
        plans = ADAPTERS["opencode"].reconcile({"providers": ["amd-halo"], "mcp_servers": []}, context)
        assert plans == []


class TestReconcileDevice:
    """reconcile_device loads a manifest and dispatches every declared capability."""

    def write_manifest(self, tmp_path: Path) -> tuple[Path, Path]:
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            "device:\n"
            "  id: test-laptop\n"
            "  profile: linux-wsl\n"
            "capabilities:\n"
            "  opencode:\n"
            "    providers: [amd-halo]\n"
            "    mcp_servers: [github]\n"
            "  network:\n"
            "    hostname: test-laptop\n"
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
            "    base_url: http://halo:13305/api/v1\n"
            "mcp-servers:\n"
            "  - id: github\n"
            "    url: http://cp/mcp/github\n"
        )
        return manifest, catalog

    def test_dispatches_declared_capabilities(self, tmp_path):
        manifest, catalog = self.write_manifest(tmp_path)
        results = reconcile_device(
            manifest,
            catalog,
            opencode_config_dir=tmp_path,
            env={},
            resolve_host=lambda fqdn: "192.168.1.50",
            http_get=lambda url, headers=None: None,
        )
        assert set(results) == {"opencode", "network"}
        assert [p.target for p in results["network"]] == []
        assert "provider:amd-halo" in [p.target for p in results["opencode"]]

    def test_rejects_unknown_capability(self, tmp_path):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            "device:\n"
            "  id: test-laptop\n"
            "  profile: linux-wsl\n"
            "capabilities:\n"
            "  ansible: {}\n"
            "credentials: []\n"
        )
        catalog = tmp_path / "catalog.yaml"
        catalog.write_text("providers: []\n")
        with pytest.raises(ValueError, match="Unknown capability"):
            reconcile_device(manifest, catalog, opencode_config_dir=tmp_path, env={})
