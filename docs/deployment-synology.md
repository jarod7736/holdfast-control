# Deploying the control plane to Synology

The control plane runs in Container Manager on the DS920+, on the same Docker
network as LiteLLM. Co-location is deliberate: the gateway speaks plain HTTP,
so reaching it by container name keeps the key-minting credential off the LAN.

The control plane is currently live and reachable at
https://holdfast.tail1c66ec.ts.net/ui (tailnet only — no LAN publish).

## Tailnet access (added 2026-08-05)

A Tailscale sidecar container (`holdfast-tailscale`, see
`deploy/synology/docker-compose.yaml`) exposes the control plane as HTTPS on
the tailnet via `tailscale serve`:

- **URL: `https://holdfast.tail1c66ec.ts.net`** (dashboard at `/ui`, API on the
  same port 443). Enrolled devices use this as `control_plane_url` — enrollment
  key material no longer crosses the LAN in cleartext.
- The node authenticates with a reusable auth key (1Password:
  `holdfast-automation/tailscale-authkey`, in the NAS `.env` as `TS_AUTHKEY`),
  so it re-joins the tailnet automatically across container restarts.
- The serve proxy was applied once by hand and persists in the `ts-state`
  volume: `docker exec holdfast-tailscale tailscale serve --bg http://control-plane:8000`
- Gotchas hit during setup: `TS_SERVE_CONFIG` crash-loops before first login
  (`${TS_CERT_DOMAIN}` unresolved), and `serve set --all` does not exist on
  image version 1.98.x — use the `serve --bg` CLI form above.
- The LAN publish on 8200 was removed 2026-08-05: the API and dashboard are
  tailnet-only. New devices must run Tailscale before they can enroll.

## Prerequisites

- DSM 7.2+ with Container Manager installed
- The LiteLLM container running, and the name of the Docker network it is on
- A shared folder for the project, e.g. `/volume1/docker/holdfast-control`

## 1. Snapshot the existing database

Skip this and every already-enrolled device loses its report token and must
re-enroll. On the laptop currently running the control plane:

```sh
./scripts/snapshot-control-plane-db.sh
```

This writes `control-plane.db-snapshot` and prints the row counts it captured.

## 2. Upload the project

Copy the repository to `/volume1/docker/holdfast-control` (File Station, or
`scp -P 2222` if SSH is enabled). Then place the snapshot at
`/volume1/docker/holdfast-control/data/control-plane.db` (using the
`HOLDFAST_DATA_DIR` value you'll set in step 4; the default in `.env.example`
is `/volume1/docker/holdfast-control/data`).

The container runs as UID 10001, so make the data directory writable by it:

```sh
chown -R 10001:10001 /volume1/docker/holdfast-control/data
```

If you cannot chown (no SSH), `chmod 777` the directory from File Station's
permissions dialog as a fallback and tighten it later.

## 3. Find the LiteLLM network name

The single most common deployment failure is guessing this wrong. With SSH:

```sh
sudo docker inspect <litellm-container-name> \
  -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}'
```

Without SSH: Container Manager → **Network**, and look for the network listing
the LiteLLM container as a member.

## 4. Configure `.env`

```sh
cp .env.example .env
chmod 600 .env
```

Fill in every value. `.env.example` documents each one. Two that matter most:

- `HOLDFAST_LITELLM_URL` must be `http://<litellm-container-name>:4000` — **not**
  `http://192.168.1.181:4000`. The LAN address works, but sends the admin
  credential over the wire in cleartext on every enrollment.
- `LITELLM_NETWORK_NAME` is the name from step 3. The compose file declares this
  network `external: true`, so a wrong name fails the deploy loudly rather than
  silently creating an isolated network.

Read the gateway credential out of 1Password on the laptop:

```sh
op read "op://holdfast-automation/litellm-key-admin/credential"
```

Do **not** put the 1Password service-account token on the Synology. It is as
sensitive as the credential it fetches, so it would add exposure, not remove it.

**Note on git:** The `.env.example` file is in git while other `.env*` files are
ignored by `.gitignore` — if you edit or add similar example files, you'll need
`git add -f` to force-stage them since they match the `.env*` glob. This is
intentional to protect production `.env` files from accidental commits.

## 5. Build and start

Container Manager → **Project** → **Create** → point at
`/volume1/docker/holdfast-control` → it detects `docker-compose.yml` → **Build**.

With SSH, equivalently:

```sh
cd /volume1/docker/holdfast-control && sudo docker compose up -d --build
```

## 6. Verify

```sh
# API is up (from any tailnet device)
curl -s -o /dev/null -w '%{http_code}\n' https://holdfast.tail1c66ec.ts.net/openapi.json   # 200

# Migrated state survived — expect the token count from step 1
sudo docker exec holdfast-control python -c \
  "import sqlite3;print(sqlite3.connect('/data/control-plane.db').execute('SELECT COUNT(*) FROM tokens').fetchone())"

# Gateway reachable by container name from inside the container
sudo docker exec holdfast-control python -c \
  "import urllib.request;print(urllib.request.urlopen('http://litellm:4000/health/liveliness',timeout=5).status)"

# End-to-end, no gateway needed: a scope-less code enrolls
HOLDFAST_ADMIN_TOKEN=<token> holdfastctl enroll-code smoke-test \
  --control-plane https://holdfast.tail1c66ec.ts.net
curl -s -X POST https://holdfast.tail1c66ec.ts.net/api/v1/enroll \
  -H 'Content-Type: application/json' \
  -d '{"code":"<printed>","device_id":"smoke-test"}'
# Expect: {"report_token": "..."} and no gateway_key
```

Key minting additionally requires LiteLLM's `/key/generate` to work. Verified
working 2026-08-05: a scoped code (`--models or-cheap --mcp github`) enrolls
with `gateway_key` and `gateway_key_alias` in the response. If minting ever
returns 500 after a LiteLLM image update, apply its Prisma migrations on the
gateway (`prisma db push --schema /app/schema.prisma --skip-generate` inside
the litellm container) before debugging the control plane.

## 7. Point devices at the new host

Prerequisite for new devices: **join the tailnet first** (`tailscale up`) — the
control plane has no LAN listener. On each enrolled device, edit
`~/.config/holdfastctl/config.yaml`:

```yaml
control_plane_url: https://holdfast.tail1c66ec.ts.net
```

Then `systemctl --user restart holdfastctl-agent.timer`. Report tokens carry
over because the database was migrated.

## Rollback

Stop the project in Container Manager and restart the control plane on the
laptop. The Synology database is a copy; the laptop's original is untouched.

## Upgrading

Pull the repo on the Synology, then rebuild the project. Back up
`data/control-plane.db` first — `init_database` migrates in place and does not
roll back.
