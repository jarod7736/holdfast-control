# Operating Guide

Day-to-day use of Holdfast Control: running it, enabling features, and keeping declared state and real state in agreement.

For architecture, the HTTP API, and control-plane deployment, see [the README](../README.md) and [docs/architecture.md](architecture.md). This guide is about operating the system, not building it.

---

## The mental model

Three things, and it helps to keep them distinct:

| | What it is | Where it lives |
|---|---|---|
| **Declared state** | What a device *should* have | `manifests/` (git) |
| **Actual state** | What the device really has | `~/.config/opencode/opencode.json`, DNS, the gateway |
| **Observed state** | What the control plane has been told | SQLite on the Synology, fed by `holdfastctl report` |

## The one rule that explains everything

**`reconcile` never changes anything. It prints a plan and exits.**

Nothing in this codebase writes `opencode.json`. There is no `apply` command. When reconcile says `[add] provider:amd-halo`, that is a to-do item for you, not something it just did.

This is deliberate — the blast radius of a config manager that edits your tooling unattended is large, and the value here is in *detecting* drift. But it means the loop only closes when you apply the change and re-run. Get that habit and the system works well; skip it and the manifests quietly become fiction.

---

## Everyday commands

Run as `holdfastctl <cmd>` (installed at `~/.local/bin`), or from the repo as `.venv/bin/python -m holdfastctl.cli <cmd>`.

| Command | What it does |
|---|---|
| `reconcile` | Compare declared vs actual, print a plan. **Read-only.** |
| `validate` | Check manifests for schema errors and literal secrets |
| `doctor` | Local diagnostics — opencode, 1Password, git, ssh-agent |
| `report` | Inspect local state and post it to the control plane |
| `enroll-code` | Operator: mint a one-time enrollment code (needs `HOLDFAST_ADMIN_TOKEN`) |
| `run opencode` | Launch opencode with secrets injected via `op run` |

`reconcile` takes three paths, all defaulted:

```sh
holdfastctl reconcile \
  --manifest   manifests/devices/jarod7736-laptop.yaml \
  --catalog    manifests/catalogs/credentials.yaml \
  --config-dir ~/.config/opencode
```

`--config-dir` is useful for dry-running a change against a throwaway config before touching your real one.

---

## How the manifests fit together

Two files matter.

**The device manifest** (`manifests/devices/<device>.yaml`) declares which capabilities the device has and which catalog entries it uses — by id:

```yaml
device:
  id: jarod7736-laptop
  profile: linux-wsl          # a label only — see Gotchas
capabilities:
  opencode:
    required: true
    providers: [opencode-cloud, amd-halo, openrouter]
    mcp_servers: [github, halo-commander, playwright]
  network:
    hostname: jarod7736-laptop
    dns_zone: holdfast.lan
    gateway_url: http://192.168.1.181:4000
  gateway_access:
    gateway_url: http://192.168.1.181:4000
    models: [or-opus, or-gpt, or-coder, or-cheap]
    mcp_servers: [github, halo-commander, playwright]
credentials:
  - id: litellm-jarod-laptop
    reference: op://holdfast-lan/opencode-litellm-jarod-laptop-api-key/credential
    inject_as: LITELLM_API_KEY
```

**The catalog** (`manifests/catalogs/credentials.yaml`) defines what those ids *mean* — the shared library every device draws from. It holds `credentials`, `providers`, and `mcp-servers` blocks.

Only three capabilities exist. Declaring anything else is rejected by `validate` with `Unknown capability type: <name>`:

- **`opencode`** — reconciles `opencode.json` against declared providers and MCP servers
- **`network`** — DNS presence in the zone, gateway reachability
- **`gateway_access`** — the LiteLLM virtual key's model/MCP scope

---

## Enabling a feature

### Adding an MCP server

Worked example — adding `playwright`:

**1. Define it in the catalog** (`manifests/catalogs/credentials.yaml`):

```yaml
mcp-servers:
  - id: playwright
    url: http://192.168.1.181:4000/mcp/playwright
```

> **The URL must be `/mcp/<exact-alias>`** as the gateway registers it — underscores and all, with no trailing `/mcp`. A wrong path does not fail loudly: an unrecognized alias returns HTTP 200 from the aggregate gateway and serves **zero tools**. Check the live list with:
> ```sh
> curl -s http://192.168.1.181:4000/v1/mcp/server \
>   -H "Authorization: Bearer $LITELLM_API_KEY" | python3 -m json.tool | grep '"alias"'
> ```

A `credentials:` block on an MCP entry is optional and usually wrong — every MCP server authenticates with the single gateway key (`LITELLM_API_KEY`). There are no per-server keys.

**2. Declare it on the device** — add the id to `capabilities.opencode.mcp_servers`, and to `capabilities.gateway_access.mcp_servers` if the device's virtual key should be scoped to it.

**3. Apply it for real** — add the entry to `~/.config/opencode/opencode.json`:

```json
"playwright": {
  "type": "remote",
  "url": "http://192.168.1.181:4000/mcp/playwright",
  "headers": { "Authorization": "Bearer {env:LITELLM_API_KEY}" }
}
```

**4. Close the loop** — `holdfastctl validate && holdfastctl reconcile`, and expect `no drift` under `[opencode]`.

### Adding a provider

Same shape: add to the catalog's `providers:` block (`id`, `type`, `base_url`, optional `credentials`), reference the id from `capabilities.opencode.providers`, add it to `opencode.json` under `provider`, then reconcile.

Set `builtin: true` for providers that connect through `opencode auth login` rather than `opencode.json` — reconcile skips those instead of reporting them as permanently missing.

### Adding a credential

Credentials are **always** `op://` references — a literal secret is rejected by `validate`. Each needs all three fields:

```yaml
- id: amd-halo-api-key
  reference: op://holdfast-lan/lemonade api key/credential
  inject_as: LEMONADE_API_KEY
```

`inject_as` is the env var the config references as `{env:VAR}`. Reconcile checks that the var appears in `opencode.json`, not that it resolves — a reference to a 1Password item that doesn't exist looks healthy. Confirm with `op item get "<name>" --vault holdfast-lan`.

---

## Keeping it in sync

### The loop

```
edit manifest  →  validate  →  reconcile  →  APPLY BY HAND  →  reconcile  →  "no drift"
```

That final re-run is the step that matters. Without it you have a plan, not a synced device.

```sh
holdfastctl validate    # schema + secret-literal check
holdfastctl reconcile   # print the plan
# ... apply the printed changes ...
holdfastctl reconcile   # expect: no drift
```

### Reading the plan

| Plan line | Meaning | Fix |
|---|---|---|
| `[add] provider:X` | Declared in the manifest, absent from `opencode.json` | Add it to `opencode.json`, or drop it from the manifest |
| `[add] mcp_server:X` | Same, for an MCP server | As above |
| `[add] env:VAR` | A declared credential's `inject_as` var is never referenced in the config | Add `{env:VAR}` where it belongs |
| `[repair] dns:<fqdn>` | The device doesn't resolve in `holdfast.lan` | Add the DNS record, or correct `hostname`/`dns_zone` |
| `[repair] gateway:<url>` | Gateway unreachable from this device | Network problem, not a config problem |
| `[repair] mcp_server_url:X` | **A configured MCP URL is live-probed and wrong** | See below |

The last one is the only check that talks to the network on the opencode capability, and it has two forms:

- `returned HTTP N - URL matches no gateway MCP route`
  The path doesn't exist. Usually a trailing `/mcp` — `/mcp/github/mcp` should be `/mcp/github`.

- `reports 'litellm-mcp-server' - URL does not select this server`
  The path is reachable but the alias didn't match, so you hit the aggregate gateway. Usually hyphens where the alias uses underscores (`/mcp/halo-commander` → `/mcp/halo_commander`). **This fails silently in normal use** — the server appears connected and provides no tools.

A probe that can't reach the host at all produces *no* plan. Unknown state is never reported as drift, so a clean reconcile while the gateway is down means "nothing checked", not "all good".

### Automatic reporting

`scripts/install-agent.sh` installs a systemd **user** timer that runs `holdfastctl report` every 15 minutes (`*-*-* *:00/15:00`, `Persistent=true`, 60s jitter). It posts local state to the control plane — it does **not** reconcile or fix anything.

```sh
systemctl --user list-timers holdfastctl-agent.timer
systemctl --user start holdfastctl-agent.service   # run one now
journalctl --user -u holdfastctl-agent.service -n 50
```

### Fleet-wide check

`scripts/fleet_check.sh` queries the control plane for every enrolled device and reports report freshness, drift, timer health, and config sanity. Needs the 1Password service account and the admin token.

### Adding a device

See [Onboard a new device](../README.md#onboard-a-new-device) — mint a code with `enroll-code`, run `install-agent.sh` on the device, then store the one-time LiteLLM key it prints.

---

## Before you commit

```sh
.venv/bin/python -m pytest -q                        # 228 passed, 1 skipped
.venv/bin/python -m mypy -p holdfastctl -p server    # must be clean
.venv/bin/python -m ruff check                       # must be clean
.venv/bin/python -m holdfastctl.cli validate         # all manifests valid
```

CI runs `ruff check` bare across the whole repo, so a lint error anywhere fails the build even when tests pass. The one skipped test is a shellcheck gate that skips when shellcheck isn't installed.

---

## Gotchas

Things that look like they do something and don't. Each was verified, not assumed.

**`manifests/profiles/` is not loaded.** `device.profile` is read as a string label and nothing else. `linux-wsl.yaml` isn't even a profile — it contains a `device:` block naming `jarod7736-laptop`, making it a stale copy of the device manifest (it still lists the removed `nas-files`). Editing it has no effect. Cleanup candidate.

**Only `credentials.yaml` is loaded.** It is the `--catalog` default and holds `credentials`, `providers`, and `mcp-servers` despite the name. `skills.yaml` sits beside it and nothing reads it.

**Skills aren't implemented.** There is no skills adapter. The catalog's `skills:` block is never read on the reconcile path, and declaring `skills:` as a *capability* is rejected outright — `Unknown capability type: skills`.

**Reconcile only notices things that are missing.** It compares declared → actual. An entry in `opencode.json` that no manifest declares is invisible to it. Extra config is never flagged.

**Credential references aren't resolved.** Reconcile checks that `{env:VAR}` appears in the config, not that the `op://` item exists. Verify with `op item get` when adding one.

**`holdfastctl validate` only enforces the strict schema on files under `catalogs/` and on manifests with a `device:` or `profile:` key.** A YAML file elsewhere in `manifests/` gets the lighter treatment.
