# Holdfast Control

Configuration management for home lab devices: an agent-driven system that inventories devices, reconciles them against declared manifests, and reports status/drift to a small control plane.

## Architecture

- **`holdfastctl`** — the agent CLI (Python, `src/holdfastctl/`):
  - `doctor` — diagnostic checks for the local system (opencode, 1Password, git, ssh-agent, …)
  - `inspect` — collects a structured device state snapshot
  - `reconcile` — compares current state to the desired manifest and produces a change plan
  - `report` — enrolls with the control plane, stores a report-only token (mode `0600`), and posts device status
  - Device identity is resolved from the config, then the hostname (hosts/DNS), then an interactive prompt.
- **Control plane** — FastAPI server (`server/`), run with `python -m server`:
  - One-time, expiring, device-bound enrollment codes; raw report tokens returned only at enrollment and stored as SHA-256 hashes
  - Constant-time token verification (`secrets.compare_digest`), per-device token isolation, revocation support
  - Authenticated report ingestion, plan creation/approval, and drift endpoints
  - Health endpoints at `/healthz` and `/readyz`
- **Manifests** (`manifests/`) — device and profile YAML describing desired capabilities and `op://` 1Password credential references. Secret literals are rejected; only `op://` references are accepted.

## API

- `POST /api/v1/enrollment-codes` — generate an enrollment code for a device
- `POST /api/v1/enroll` — exchange code for a raw report token
- `POST /api/v1/devices/{device_id}/reports` — authenticated report ingestion
- `POST` / `GET /api/v1/devices/{device_id}/plans` — plan creation and listing
- `POST /api/v1/devices/{device_id}/plans/{plan_id}/approve` — plan approval bound to exact `current_hash`/`desired_commit`
- `GET /api/v1/devices/{device_id}/drift` — drift reporting

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

## Development

```sh
.venv/bin/pip install -e '.[dev]'
ruff check .
mypy -p holdfastctl -p server
pytest tests/
```

## Documentation

- [Architecture](docs/architecture.md)
- [Threat Model](docs/threat-model.md)
- [Credential Policy](docs/credential-policy.md)
- [Drift Baseline — jarod7736-laptop](docs/drift-baseline-jarod7736-laptop.md)

## Reference

- [Implementation Plan](../2ndBrain/wiki/analyses/holdfast-control-implementation-plan.md)
