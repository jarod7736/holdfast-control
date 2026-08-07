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

- **Stale default control plane in `scripts/install-agent.sh`** - line 10 still
  defaults `CONTROL_PLANE_URL` to `http://127.0.0.1:8000`, predating the tailnet
  cutover (commit 7674de1). The agent config is written once and never
  overwritten, so an install that omits `--control-plane` permanently bakes in a
  dead endpoint that `fleet_check.sh` then flags as drift. Change the default to
  `https://holdfast.tail1c66ec.ts.net`, or drop the default and require the flag.
  Motivating case: onboarding `JAROD-DESKTOP` (2026-08-07), see
  `docs/fleet-audit-2026-08-07.md`.

- **`install-agent.sh` calls bare `pip3`** - on a pyenv box the shim can resolve
  to a Python older than the package's `requires-python = ">=3.12"`, and the
  install fails. Workaround is a `PYENV_VERSION=3.12.0` prefix. Consider having
  the script select an interpreter explicitly and fail with a clear message when
  none is new enough (2026-08-07).
