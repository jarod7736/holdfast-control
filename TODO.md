# TODO

Future work items for holdfast-control. Not scheduled; pull from here when
planning the next phase.

Grouped by kind, because they are not equivalent: **misleading** items make the
system lie about itself, **undone** items are honest gaps, **noise** items are
stale files that make the repo hard to read. Surveyed 2026-08-07 — at that point
the fleet itself was fully healthy (`scripts/fleet_check.sh`: ALL CHECKS PASSED,
all four devices reporting fresh), so nothing here is an outage.

## Misleading — the code reports something untrue

- **`doctor` says `Overall: HEALTHY` while three of its checks are
  placeholders.** In `src/holdfastctl/cli.py`: `lite_llm` (line 113) and
  `amd_halo` (line 116) are hardcoded `not_configured` and never probed, and
  `desired_state.commit` (line 151) is hardcoded `"unknown"` with the note
  `"Implement desired state tracking"`. The gateway URL is already declared in
  the device manifest (`gateway_url: http://192.168.1.181:4000`) and
  `server/prober.py` already does HTTP probing, so the LiteLLM check is wiring,
  not new machinery. Either implement these or stop printing them — a diagnostic
  that always prints the same three non-answers trains you to skim it.

- **`doctor`'s git check runs in `$HOME`, not in any repo it cares about.**
  `cli.py:121` passes `cwd=Path.home()`. On a host with no `~/.git` (this
  laptop) it always prints `status: not_a_repo_or_error, clean: False` — a
  failure-shaped line that is *not* added to `issues`, so the command still
  reports `HEALTHY`. It dates from the 2026-08-01 bulk commit (dda6e4a), so
  whether it meant to check a home dotfiles repo is unclear. Decide what it is
  for, then make it either check that thing or say nothing.

- **Stale default control plane in `holdfastctl` itself** - the same defect
  fixed in `scripts/install-agent.sh` (2026-08-07) also lives in
  `src/holdfastctl/cli.py`: line 284 falls back to `http://127.0.0.1:8000` when
  a config omits `control_plane_url`, and line 322 defaults the operator-facing
  `enroll-code --control-plane` to it. Both predate the tailnet cutover
  (commit 7674de1). Point them at `https://holdfast.tail1c66ec.ts.net`, which is
  what `scripts/fleet_check.sh` treats as the one canonical URL.

## Undone — honest gaps

- **`--mcp`-only enrollment codes may mint an unrestricted key.** Flagged by the
  key-minting review and still open, and it is the one item here with a security
  edge. A code created with gateway MCP servers but no models mints a LiteLLM
  key with `models=[]`, which LiteLLM may treat as *all models* rather than
  none. Confirm the live semantics on the gateway; if empty means unrestricted,
  reject the combination at code creation (400 when `gateway_mcp_servers` is set
  without `gateway_models`) in the server's enrollment path.

- **Token revocation endpoint** — the `tokens` table has a `revoked` flag
  (`server/storage/__init__.py`) and auth checks honor it
  (`server/auth/__init__.py`), but there is no API endpoint to set it.
  Add an admin-authenticated `POST /api/v1/devices/{device_id}/tokens/revoke`
  (or similar) so a device's report token can be revoked without direct DB
  access. Motivating case: smoke-test enrollment residue on the Synology
  deployment could not be cleaned up remotely (2026-08-05).

- **`JAROD-DESKTOP` has no SSH mapping, so its host state is never checked.**
  `SSH_HOSTS` in `scripts/fleet_check.sh:27` lists only `amd-halo`,
  `jarod7736-laptop`, and `lobsterboy`. `JAROD-DESKTOP` therefore reports
  `SKIP: No SSH mapping for this device` on every run — its timer and config are
  confirmed by nothing but its own API reports. Add the alias, or state in the
  script's output that this device is deliberately API-only.

- **WSL2 interpreter setup on `JAROD-DESKTOP`.** `scripts/install-agent.sh` now
  fails with a clear message instead of an obscure pip error, but the box itself
  still has Python 3.8 global under pyenv (3.12.0 installed) plus a venv, and
  has not been re-run against the fixed script. Deferred deliberately
  (2026-08-07). See `docs/fleet-audit-2026-08-07.md`.

- **`dns:jarod7736-laptop.holdfast.lan` does not resolve.** Long-standing single
  `[repair]` item from `holdfastctl reconcile`; the device is missing from the
  `holdfast.lan` zone. Pre-existing and unrelated to recent work.

## Noise — stale files that make the repo hard to read

- **Three superseded status docs at the repo root**, all last touched
  2026-08-01 and all describing a snapshot that no longer holds:
  `IMPLEMENTATION_STATUS.md` (claims `pytest` 113 passed / mypy 17 files; it is
  now 233 passed / 19 files), `IMPLEMENTATION_SUMMARY.md`, and
  `PHASE2_IMPLEMENTATION_SUMMARY.md`. Worse than merely stale:
  `IMPLEMENTATION_SUMMARY.md` advertises an *"Apply Module — atomic
  configuration application"*, while `docs/operating-guide.md` states plainly
  that there is no `apply` command and that nothing in the codebase writes
  `opencode.json`. A reader cannot tell which is true without reading the source.
  `README.md` + `docs/operating-guide.md` now cover this ground; delete the three.

- **Three one-off scripts at the repo root**, also 2026-08-01 and superseded by
  the pytest suite: `debug_validation.py`, `demo_functionality.sh`,
  `phase1_validation.sh`. `tests/` additionally holds several standalone
  verification scripts (`comprehensive_verification.py`,
  `final_server_verification.py`, `final_verification.py`) that
  `IMPLEMENTATION_STATUS.md` itself records as superseded by the collected suite.
