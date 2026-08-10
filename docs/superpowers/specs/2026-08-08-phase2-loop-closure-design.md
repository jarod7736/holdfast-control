# Spec: Phase 2 Loop Closure — Approval-Gated Apply and Rollback

**Date:** 2026-08-08
**Status:** Approved
**Implements:** Phase 2 of [holdfast-control-implementation-plan.md](../../../../2ndBrain/wiki/analyses/holdfast-control-implementation-plan.md), sections "Phase 2: Controller And WSL Pilot" and "Agent And API Behavior"

## Problem

`docs/architecture.md` describes a system that "inspects local state, generates a deterministic plan, applies only an exact approved plan, backs up managed files, rolls back safely, and reports status." The second half of that sentence does not exist.

**The reconciliation loop is open at both ends.** `holdfastctl reconcile` (`cli.py:352`) builds plans and prints them to stdout — it never submits them to the control plane, never persists them, never surfaces a plan id. There is no `apply` command. The server's `POST /plans` and `POST /plans/{id}/approve` endpoints are live and tested, and nothing on the agent side ever calls them.

**Six of the nine specified commands are missing.** The spec names `doctor | inspect | plan | show-plan | approve | apply | rollback | report | run opencode`. The CLI ships `doctor | run | report | enroll-code | reconcile`.

**`apply.py` is not merely unwired — it is incorrect, and wiring it as-is would destroy configuration.** Three defects, all verified:

1. **Whole-file overwrite.** `reconcile.py:334` sets `target_data=provider` — a single catalog entry. `AtomicApplier.apply_plan` passes it to `apply_configuration`, which does `json.dump(new_config, ...)` over the target. Applying an add-provider plan replaces the entirety of `opencode.json` with one provider dict.
2. **The atomic write is not atomic.** `tempfile.NamedTemporaryFile` is called with no `dir=`, so the temp file lands in `/tmp`. On the pilot host `/tmp` is `st_dev 37` and `~/.config/opencode` is `st_dev 28` — different filesystems, so `shutil.move` is copy+unlink, not a rename. An interruption mid-write leaves a truncated config, and the restore path only fires on a caught exception.
3. **Empty backups.** `BackupManager.create_backup` deliberately writes an empty backup file when the source does not exist ("For test environments where files may not exist yet"). Restoring one over a live config blanks it.

Three repository documents — `IMPLEMENTATION_STATUS.md`, `IMPLEMENTATION_SUMMARY.md`, `PHASE2_IMPLEMENTATION_SUMMARY.md` — assert Phase 2 is complete. They are true about modules existing and passing unit tests, and false about the phase being done.

Evidence that the current fidelity is insufficient, independent of the above: on 2026-08-08 the agent reported clean on three machines while OpenCode was failing on all three, because `load_current_opencode_state` (`reconcile.py:259`) reads `options.baseURL` and stops.

## Goal

A device compares itself to versioned desired state, produces a deterministic reviewable plan, applies only an operator-approved plan, and can roll back — with the report token never able to approve, and the control plane never able to make the agent execute arbitrary instructions.

```
holdfastctl inspect              local state snapshot, no server contact
holdfastctl plan                 re-derive actions, compute current_hash,
                                 POST /plans  ->  plan_id
holdfastctl show-plan <id>       actions + approval status
holdfastctl approve <id>         OPERATOR machine, admin token
holdfastctl apply <id>           re-inspect -> verify -> backup -> merge
holdfastctl rollback <id>        restore from backup manifest
```

## Core Invariant

**The control plane stores the fingerprint, never the actions.** `POST /plans` persists only `desired_commit`, `current_hash`, and `expiry_timestamp` — it has no column for plan contents, and none will be added.

The agent re-derives actions locally at apply time and refuses if the freshly computed hash no longer matches the approved one. The agent never executes instructions supplied by the server. This is what prevents a compromised control plane from becoming remote code execution on every workstation, and it is already the shape the server was built in.

## Functional Requirements

### FR1 — Command surface

Six commands, matching the spec exactly so the CLI and the written spec stop diverging:

| Command | Auth | Behavior |
|---|---|---|
| `inspect` | none | CLI wrapper over the existing `inspect.py` module; prints the state snapshot |
| `plan` | device token | Renames `reconcile`. Re-derives actions, computes `current_hash`, POSTs to `/plans` (see FR5), prints the returned `plan_id`. `--local` skips submission and only prints, preserving today's offline behavior |
| `show-plan <id>` | device token | Prints locally re-derived actions plus the plan's approval status |
| `approve <id>` | admin token | Operator machine only. Calls `POST /plans/{id}/approve` |
| `apply <id>` | device token | Verifies, backs up, applies. `--dry-run` prints what would change and exits 0 without writing |
| `rollback <id>` | none | Restores from the plan's backup manifest |

Renaming `reconcile` to `plan` is safe: nothing external invokes it. The systemd unit (`templates/systemd/holdfastctl-agent.service:11`) runs only `report`, and `scripts/fleet_check.sh` shells out only to `systemctl`.

### FR2 — Atomic write primitive

`apply.py` is reduced to one correct primitive. Delete `apply_configuration` and `apply_plan`.

`atomic_write(path, data)` must:
- create its temp file in `path.parent`, never `/tmp`
- `fsync` before replacing
- use `os.replace` for a genuine atomic rename
- capture mode via `os.stat` before and restore via `os.chmod` after
- drop the `os.chown` call — same-user in practice, and it fails unprivileged
- reject any path failing the allowlist, reusing `validate_path_safety` from `manifest_schema.py`

### FR3 — Backups and rollback

- `create_backup` returns `None` for a missing source rather than writing an empty file.
- A per-plan backup manifest records `plan_id -> [(target, backup_path, mode)]`, so `rollback` restores deterministically instead of inferring from timestamps.
- `rollback` restores both file bytes and permissions.

### FR4 — Capability-scoped appliers

Each capability adapter in `capabilities.py` gains `apply()` alongside its existing `reconcile()`, following the pluggable-adapter pattern established in `e2be4ee`.

`OpencodeCapabilityAdapter.apply` parses the current `opencode.json`, merges only the provider or MCP entry the plan names, and writes back via `atomic_write`. **Every key the manifest does not describe is preserved byte-for-byte** — `plugin`, `agent`, `lsp`, `permission`, `$schema`, `model`. This is required by the unmanaged-by-design list in `docs/drift-baseline-jarod7736-laptop.md`.

`apply.py` retains no knowledge of configuration shape.

### FR5 — Device-token plan submit and read

Two server changes in `server/api/__init__.py`. Both reuse the existing device-scoped check `auth.authorize_report(conn, raw_token, device_id)`, which `POST /reports` already uses — no new auth mechanism is introduced.

**Relax plan creation.** `POST /api/v1/devices/{device_id}/plans` is currently `require_admin`, which makes it impossible for an agent to submit its own plan. It must accept **either** an admin token **or** that device's report token, scoped to its own `device_id`. A device submitting a fingerprint about itself is the intended flow; approval remains admin-only, so this grants no new authority.

**Add plan read.** New endpoint:

```
GET /api/v1/devices/{device_id}/plans/{plan_id}
```

Authenticated by the device's report token, scoped to that device, returning only `approval_status`, `current_hash`, `desired_commit`, `expiry_timestamp`.

`POST /plans/{id}/approve` and `GET /devices/{id}/plans` (list) keep `require_admin` unchanged. The report token remains unable to approve, to read another device's plans, or to retrieve secrets.

## Failure Semantics

`apply` refuses unless **all** of the following hold, and exits non-zero with a distinct message for each:

1. plan `approval_status` is `approved`
2. freshly recomputed `current_hash` equals the approved hash
3. plan has not expired
4. every target path passes the allowlist

A backup precedes every write. Any failure during application restores from backup and exits non-zero. Failed validation is non-destructive: the target is never opened for writing until validation passes.

## Security Requirements

- The agent never executes actions supplied by the control plane; it re-derives them locally.
- The report token cannot approve a plan, read another device's plans, or retrieve secrets.
- `approve` requires the admin token and is never run on a managed device.
- Secret-shaped literals reach neither logs, database, reports, nor backups.
- Path traversal and non-allowlisted targets are rejected before any write.

## Out of Scope

Deferred to Phase 2.5, in this order:

- `models[].limit` fidelity in `load_current_opencode_state`
- the `repair` action for present-but-misconfigured providers
- device manifests for `amd-halo` and `jarod-desktop`
- the profiles loader and merge, plus the unreachable `ProfileManifest` branch at `validate.py:37`
- the `llm_serving` capability covering `-np`, `ctx_size`, `max_loaded_models`

Also out of scope: Phase 3 dashboard adapters, Authentik/SSO, and the open items already tracked in `TODO.md`.

## Acceptance Criteria

Drawn from the spec's own Phase 2 verification list rather than invented:

1. Repeated inspections hash identically.
2. Changed local state invalidates an existing approval.
3. Path traversal is rejected.
4. Failed validation is non-destructive.
5. Rollback restores bytes and permissions.
6. Canary secrets appear in no log, database row, report, or backup.
7. A report token cannot approve a plan.

Two regression tests specific to the defects above:

8. Applying an add-provider plan leaves `plugin`, `agent`, `lsp`, and `permission` intact.
9. `atomic_write` places its temp file on the destination filesystem (assert matching `st_dev`).
10. A device token cannot create or read a plan belonging to another `device_id`.

End-to-end pilot on `jarod7736-laptop`, per original Phase 2 items 5–7: dry-run, apply a real OpenCode change, restart OpenCode, verify providers and MCP reachability, then roll back and reapply.

## Documentation Correction

`IMPLEMENTATION_STATUS.md`, `IMPLEMENTATION_SUMMARY.md`, and `PHASE2_IMPLEMENTATION_SUMMARY.md` must be corrected to describe actual state. `IMPLEMENTATION_SUMMARY.md` and `PHASE2_IMPLEMENTATION_SUMMARY.md` are near-duplicates and should be consolidated into one. All three cite a stale test count of 113; the suite currently runs 204.
