# Holdfast Control

Configuration management for home lab devices: an agent-driven system that inventories devices, reconciles them against declared manifests, and reports status/drift to a small control plane.

## Architecture

- **`holdfastctl`** — the agent CLI (Python, `src/holdfastctl/`):
  - `doctor` — diagnostic checks for the local system (opencode, 1Password, git, ssh-agent, …)
  - `inspect` — collects a structured device state snapshot
  - `reconcile` — compares current state to the desired manifest and produces a change plan
  - `report` — enrolls with the control plane, stores a report-only token (mode `0600`), and posts device status
  - Device identity is resolved from the config, then the hostname (hosts/DNS), then an interactive prompt.
- **Control plane** — FastAPI server (`server/`), run with `python -m server` (listens on `0.0.0.0` so every device on the LAN can reach it; override with `HOLDFAST_HOST`/`HOLDFAST_PORT`):
  - Operator-gated enrollment: `POST /api/v1/enrollment-codes` requires an admin token (`HOLDFAST_ADMIN_TOKEN`, compared in constant time); devices exchange an operator-provisioned one-time code via `/enroll` and receive a report-only token stored as a SHA-256 hash
  - Token verification by hashed lookup (SHA-256 `token_hash`, parameterized SQL, no plaintext comparison), per-device token isolation, revocation support
  - Admin-token auth on plan creation/approval, device listing, and capability/credential/integration status endpoints; device bearer-token auth on report ingestion
  - Health endpoints at `/healthz` and `/readyz` (public)
- **Manifests** (`manifests/`) — device and profile YAML describing desired capabilities and `op://` 1Password credential references. Secret literals are rejected; only `op://` references are accepted.

## API

- `POST /api/v1/enrollment-codes` — generate an enrollment code for a device, optionally declaring `gateway_models`/`gateway_mcp_servers` for its LiteLLM virtual key (admin token required)
- `POST /api/v1/enroll` — exchange an operator-provisioned code for a raw report token; when the code declares gateway scope, the response also carries a one-time scoped LiteLLM virtual key (public)
- `POST /api/v1/devices/{device_id}/reports` — device bearer-token authenticated report ingestion
- `POST` / `GET /api/v1/devices/{device_id}/plans` — plan creation and listing (admin token required)
- `POST /api/v1/devices/{device_id}/plans/{plan_id}/approve` — plan approval bound to exact `current_hash`/`desired_commit` (admin token required)
- `GET /api/v1/devices/{device_id}/drift` — drift reporting (admin token required)

Report payloads containing secret-shaped literals (e.g. AWS `AKIA…` keys) are rejected and never persisted.

## Install the agent

```sh
scripts/install-agent.sh
```

Installs `holdfastctl`, writes `~/.config/holdfastctl/config.yaml` (device id from the real hostname), and enables a user systemd timer that runs `holdfastctl report` every 15 minutes.

Start the control plane locally:

```sh
.venv/bin/python -m server   # serves on http://127.0.0.1:8000
```

## Onboard a new device

1. Operator (any machine with the admin token):
   `HOLDFAST_ADMIN_TOKEN=... holdfastctl enroll-code <device-id> --models or-cheap,or-coder --mcp github --control-plane http://<server>:8000`
2. On the device:
   `scripts/install-agent.sh --control-plane http://<server>:8000 --enrollment-code <code>`
3. The first report prints the device's LiteLLM virtual key exactly once — store it in 1Password (`holdfast-lan`) and export it as `LITELLM_API_KEY` in the device's shell profile.

Key minting requires the control plane to run with `HOLDFAST_LITELLM_URL` and `HOLDFAST_LITELLM_ADMIN_TOKEN`
(a key-management-scoped credential from the `holdfast-automation` vault). Without them, codes minted with
gateway scope fail enrollment with 503; codes without scope enroll normally.

## Deploy the control plane

The control plane is packaged as a container and runs on the Synology in
Container Manager, on the same Docker network as LiteLLM so the key-minting
credential never crosses the LAN. See
[docs/deployment-synology.md](docs/deployment-synology.md) for the full
procedure.

    ./scripts/snapshot-control-plane-db.sh   # preserve enrolled devices' tokens
    cp .env.example .env                     # fill in, chmod 600
    docker compose up -d --build             # on the Synology

Configuration is entirely environment-driven via `.env`: `HOLDFAST_ADMIN_TOKEN`,
`HOLDFAST_LITELLM_URL`, `HOLDFAST_LITELLM_ADMIN_TOKEN`, `HOLDFAST_DATA_DIR`,
`LITELLM_NETWORK_NAME`, `HOLDFAST_PORT_PUBLISHED`. (The image fixes
`HOLDFAST_DB_PATH`, `HOLDFAST_HOST`, and `HOLDFAST_PORT` to match the container
and compose setup.)

## Development

```sh
.venv/bin/pip install -e '.[dev]'
ruff check .
mypy -p holdfastctl -p server
pytest tests/
```

## Documentation

- [Operating Guide](docs/operating-guide.md) — day-to-day use, enabling features, keeping devices in sync
- [Architecture](docs/architecture.md)
- [Threat Model](docs/threat-model.md)
- [Credential Policy](docs/credential-policy.md)
- [Drift Baseline — jarod7736-laptop](docs/drift-baseline-jarod7736-laptop.md)

## Reference

- [Implementation Plan](../2ndBrain/wiki/analyses/holdfast-control-implementation-plan.md)
