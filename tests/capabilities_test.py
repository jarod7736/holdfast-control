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


class TestDefaultMcpProbe:
    """The real probe parses an MCP initialize response off the wire.

    Every other probe test injects a fake, so this is the only coverage of the
    parsing itself.
    """

    def fake_post(self, monkeypatch, status: int, text: str):
        import requests

        class Response:
            status_code = status

        Response.text = text
        monkeypatch.setattr(requests, "post", lambda *a, **k: Response())

    def sse(self, server_info: str) -> str:
        return (
            'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":'
            '{"capabilities":{"tools":{"listChanged":false}},'
            f'"serverInfo":{server_info}}}}}\n\n'
        )

    def test_extracts_server_name(self, monkeypatch):
        from holdfastctl.capabilities import default_mcp_probe

        self.fake_post(monkeypatch, 200, self.sse('{"name":"github","version":"1.0.0"}'))
        assert default_mcp_probe("http://gw/mcp/github", "sk-x") == (200, "github")

    def test_extracts_server_name_when_nested_object_precedes_it(self, monkeypatch):
        """serverInfo may carry a nested object before `name`."""
        from holdfastctl.capabilities import default_mcp_probe

        self.fake_post(monkeypatch, 200, self.sse('{"meta":{"a":1},"name":"github"}'))
        assert default_mcp_probe("http://gw/mcp/github", "sk-x") == (200, "github")

    def test_non_200_reports_status_without_name(self, monkeypatch):
        from holdfastctl.capabilities import default_mcp_probe

        self.fake_post(monkeypatch, 401, '{"detail":"Authentication Error"}')
        assert default_mcp_probe("http://gw/mcp/github/mcp", "sk-x") == (401, None)

    def test_transport_failure_returns_none(self, monkeypatch):
        import requests

        from holdfastctl.capabilities import default_mcp_probe

        def boom(*a, **k):
            raise requests.RequestException("no route to host")

        monkeypatch.setattr(requests, "post", boom)
        assert default_mcp_probe("http://gw/mcp/github", "sk-x") is None


class TestOpencodeMcpProbe:
    """Configured MCP URLs are probed live so a URL that does not select its server is caught.

    Covers the two ways a LiteLLM gateway URL fails: a path that no MCP route
    matches (rejected outright), and a path whose alias does not match, which
    returns 200 from the aggregate gateway under a different server name.
    """

    def write_config(self, tmp_path: Path, url: str) -> None:
        (tmp_path / "opencode.json").write_text(
            json.dumps({"provider": {}, "mcp": {"github": {"url": url}}})
        )

    def context_with_probe(self, tmp_path: Path, result):
        context = make_context(tmp_path, env={"LITELLM_API_KEY": "sk-test"})
        context.credentials = []
        context.catalog = {"providers": [], "mcp-servers": []}
        context.mcp_probe = lambda url, key: result
        return context

    def reconcile(self, context):
        return ADAPTERS["opencode"].reconcile({"providers": [], "mcp_servers": []}, context)

    def test_matching_server_name_no_drift(self, tmp_path):
        self.write_config(tmp_path, "http://gw:4000/mcp/github")
        plans = self.reconcile(self.context_with_probe(tmp_path, (200, "github")))
        assert plans == []

    def test_mismatched_server_name_plans_repair(self, tmp_path):
        """A hyphenated or unknown alias returns 200 as the aggregate gateway."""
        self.write_config(tmp_path, "http://gw:4000/mcp/git-hub")
        plans = self.reconcile(self.context_with_probe(tmp_path, (200, "litellm-mcp-server")))
        assert [p.target for p in plans] == ["mcp_server_url:github"]
        assert "litellm-mcp-server" in plans[0].description

    def test_non_200_plans_repair(self, tmp_path):
        """A trailing /mcp segment matches no route and is rejected."""
        self.write_config(tmp_path, "http://gw:4000/mcp/github/mcp")
        plans = self.reconcile(self.context_with_probe(tmp_path, (401, None)))
        assert [p.target for p in plans] == ["mcp_server_url:github"]
        assert "401" in plans[0].description

    def test_probe_failure_does_not_fabricate_drift(self, tmp_path):
        """A transport failure is unknown state, not drift."""
        self.write_config(tmp_path, "http://gw:4000/mcp/github")
        plans = self.reconcile(self.context_with_probe(tmp_path, None))
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


def test_opencode_apply_preserves_unmanaged_keys(tmp_path):
    """Applying an add-provider plan must not drop plugin/agent/lsp/permission."""
    from holdfastctl.backup import BackupManager
    from holdfastctl.capabilities import OpencodeAdapter
    from holdfastctl.reconcile import ConfigurationPlan

    config_dir = tmp_path / "opencode"
    config_dir.mkdir()
    original = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {"existing": {"options": {"baseURL": "http://keep"}}},
        "plugin": ["oh-my-opencode-slim"],
        "agent": {"explore": {"disable": False}},
        "lsp": True,
        "permission": {"edit": "allow"},
    }
    (config_dir / "opencode.json").write_text(json.dumps(original), encoding="utf-8")

    plan = ConfigurationPlan(
        plan_id="p1", device_id="d1", desired_commit="c", current_hash="h",
        expiry_timestamp=0.0, action="add", target="provider:amd-halo",
        source="desired", description="", checksum="",
        target_data={"id": "amd-halo", "base_url": "http://amd-halo:13305/api/v1"},
    )
    context = make_context(tmp_path)
    context.opencode_config_dir = config_dir

    entry = OpencodeAdapter().apply(
        plan, context,
        backup_manager=BackupManager(backup_dir=tmp_path / "backups"),
        allowed_prefixes=(config_dir,),
    )

    result = json.loads((config_dir / "opencode.json").read_text())
    assert result["plugin"] == ["oh-my-opencode-slim"]
    assert result["agent"] == {"explore": {"disable": False}}
    assert result["lsp"] is True
    assert result["permission"] == {"edit": "allow"}
    assert result["$schema"] == "https://opencode.ai/config.json"
    assert result["provider"]["existing"]["options"]["baseURL"] == "http://keep"
    assert result["provider"]["amd-halo"]["options"]["baseURL"] == "http://amd-halo:13305/api/v1"
    assert entry["target"] == str(config_dir / "opencode.json")
    assert Path(str(entry["backup"])).is_file()


def test_collect_device_state_fingerprint_reflects_opencode_config(tmp_path):
    """Regression: the fingerprint MUST change when the device's opencode config
    changes, else a stale approval could be applied after drift."""
    from holdfastctl.capabilities import collect_device_state, device_state_fingerprint

    manifest = tmp_path / "device.yaml"
    manifest.write_text(
        "device:\n  id: test-device\n  profile: linux\n"
        "capabilities:\n  opencode:\n    required: true\n    providers: [amd-halo]\n    mcp_servers: []\n"
        "credentials: []\n",
        encoding="utf-8",
    )
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text("providers: []\nmcp-servers: []\n", encoding="utf-8")
    cd = tmp_path / "opencode"
    cd.mkdir()
    (cd / "opencode.json").write_text(
        json.dumps({"provider": {"lemonade": {"options": {"baseURL": "http://a"}}}}), encoding="utf-8"
    )

    def fp() -> str:
        return device_state_fingerprint(collect_device_state(manifest, catalog, opencode_config_dir=cd))

    f0 = fp()
    assert f0 == fp()  # stable across repeated inspections

    (cd / "opencode.json").write_text(
        json.dumps({"provider": {"lemonade": {"options": {"baseURL": "http://CHANGED"}}}}), encoding="utf-8"
    )
    assert f0 != fp()  # changed local state must change the fingerprint
