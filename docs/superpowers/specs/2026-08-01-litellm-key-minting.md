# Spec: LiteLLM Virtual-Key Minting at Enrollment

**Date:** 2026-08-01
**Status:** Approved (implements ADR "Control plane mints scoped LiteLLM virtual keys at device enrollment" and respects ADR "Cap holdfast-control scope")
**Plan:** [2026-08-01-litellm-key-minting.md](../plans/2026-08-01-litellm-key-minting.md)

## Problem

Every device on holdfast.lan needs a LiteLLM virtual key scoped to exactly the models and MCP servers it is entitled to on the gateway (`192.168.1.181:4000`). Today keys are minted by hand in the LiteLLM UI, stored in 1Password manually, and nothing verifies that a key's live scope matches what the device manifest declares. Onboarding a new device is a multi-step manual process; the `holdfastctl enroll-code` command referenced in agent error messages does not exist.

## Goal

Onboarding a new device becomes a three-command flow that produces a correctly-scoped gateway key automatically:

1. Operator: `holdfastctl enroll-code <device-id> --models ... --mcp ... --control-plane <url>` (requires `HOLDFAST_ADMIN_TOKEN`)
2. Device: `scripts/install-agent.sh --control-plane <url> --enrollment-code <code>`
3. Operator stores the once-printed key in 1Password; device exports it as `LITELLM_API_KEY`.

## Functional Requirements

**FR1 — Scope travels with the enrollment code.** `POST /api/v1/enrollment-codes` (admin-authenticated) accepts optional `gateway_models: list[str]` and `gateway_mcp_servers: list[str]`. The server stays manifest-agnostic; the operator declares scope when minting the code.

**FR2 — Key minted during enrollment.** When a device exchanges a code that declares gateway scope, `POST /api/v1/enroll` calls the LiteLLM admin API `POST /key/generate` with `key_alias` (`holdfast-<device_id>-<8 hex>`), `models`, and `metadata` (`managed_by: holdfast-control`, `device_id`, `mcp_servers`). The response gains `gateway_key` and `gateway_key_alias` alongside the existing `report_token`.

**FR3 — Metadata tracking.** A `gateway_keys` table records (id, device_id, key_alias, models, mcp_servers, minted_at) for every minted key. Never the key itself.

**FR4 — Configuration.** The control plane reads `HOLDFAST_LITELLM_URL` and `HOLDFAST_LITELLM_ADMIN_TOKEN` at app creation. The admin credential is key-management-scoped and lives in the `holdfast-automation` vault, never `holdfast-lan`.

**FR5 — Agent one-time delivery.** `holdfastctl report --enrollment-code <code>` prints the minted key exactly once, with a ready-to-paste `op item create` command. The key is never written to disk; only the report token is stored (mode 0600, unchanged).

**FR6 — Operator CLI.** New `holdfastctl enroll-code DEVICE_ID [--models a,b] [--mcp x,y] [--control-plane URL] [--expires N]` command, admin token from `HOLDFAST_ADMIN_TOKEN` env.

**FR7 — Installer onboarding.** `scripts/install-agent.sh` accepts `--control-plane URL` (written into the agent config) and `--enrollment-code CODE` (triggers an immediate first report/enrollment).

## Failure Semantics

- Scope declared but no LiteLLM admin credential configured → enrollment fails **503**; the code claim rolls back (code stays usable).
- LiteLLM unreachable or `/key/generate` fails → enrollment fails **502**; the code claim rolls back.
- Codes without gateway scope enroll exactly as before this change (backward compatible).

## Security Requirements

- **SR1:** Raw key material never persisted — not in SQLite, not in logs, not in agent files. Proven by a byte-level DB scan test.
- **SR2:** Gateway error messages never include response bodies (which may contain key material) — status codes only.
- **SR3:** All existing invariants hold unchanged: constant-time admin comparison, hash-only report tokens, parameterized SQL, one-time device-bound codes, secret-shaped report rejection.
- **SR4:** All admin HTTP calls carry explicit timeouts.

## Out of Scope (per scope-cap ADR)

- Key rotation/revocation endpoints (metadata supports them later).
- Live MCP-scope verification in the `gateway_access` adapter.
- Any apply/rollback engine work (chezmoi is the designated future apply layer).
- Dashboard surfacing of key metadata (Phase 3).

## Acceptance Criteria

All 13 validation criteria in the plan's "Validation Criteria" section: three green gates (pytest with 19 new tests, ruff, mypy at 19 files), six named security-invariant tests, behavioral tests for scope derivation/alias format/DB migration/CLI, shellcheck-clean installer — plus the manual E2E against the live gateway (mint a scoped key for a scratch device, verify `/v1/models` shows only the declared models, delete the test key).
