# TODO

Future work items for holdfast-control. Not scheduled; pull from here when
planning the next phase.

## API

- **Token revocation endpoint** — the `tokens` table has a `revoked` flag
  (`server/storage/__init__.py`) and auth checks honor it
  (`server/auth/__init__.py`), but there is no API endpoint to set it.
  Add an admin-authenticated `POST /api/v1/devices/{device_id}/tokens/revoke`
  (or similar) so a device's report token can be revoked without direct DB
  access. Motivating case: smoke-test enrollment residue on the Synology
  deployment could not be cleaned up remotely (2026-08-05).

## Agent install

- **Stale default control plane in `holdfastctl` itself** - the same defect just
  fixed in `scripts/install-agent.sh` also lives in `src/holdfastctl/cli.py`:
  line 284 falls back to `http://127.0.0.1:8000` when a config omits
  `control_plane_url`, and line 322 defaults the operator-facing
  `enroll-code --control-plane` to it. Both predate the tailnet cutover
  (commit 7674de1). Point them at `https://holdfast.tail1c66ec.ts.net`, which is
  what `scripts/fleet_check.sh` treats as the one canonical URL (2026-08-07).
