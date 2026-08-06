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
