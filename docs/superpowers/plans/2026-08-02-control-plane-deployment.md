# Control Plane Deployment to Synology Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package the holdfast-control plane as a container and deploy it to Synology Container Manager on the same Docker network as LiteLLM, so it runs 24/7 and mints gateway keys without the admin credential ever crossing the LAN.

**Architecture:** A single-stage `python:3.12-slim` image installs `holdfastctl` from `src/` via pip and copies the root-level `server/` package alongside it, running `python -m server` from `/app`. A root-level `docker-compose.yml` joins LiteLLM's **existing** user-defined bridge as an `external` network, so the gateway is reachable as `http://<litellm-container>:4000` rather than `http://192.168.1.181:4000`. SQLite lives on a bind-mounted volume; all environment-specific values (container names, network name, host paths, secrets) come from a gitignored `.env` file because they cannot be discovered from this machine.

**Tech Stack:** Docker / Synology Container Manager (DSM 7.3.2), Python 3.12, FastAPI, uvicorn, SQLite.

## Context

Decided in the ADR of 2026-08-02: the control plane moves off this laptop (which sleeps) onto the Synology DS920+ at `192.168.1.181`, co-located with LiteLLM. The decisive reason is that the LiteLLM gateway speaks plain HTTP — running the control plane anywhere else puts `HOLDFAST_LITELLM_ADMIN_TOKEN` on the wire on every enrollment.

Two facts discovered during orientation that shape this plan:

1. **`server/` is not an installed package.** `pyproject.toml` has `packages.find = {where = ["src"]}`, which packages only `holdfastctl`. `python -m server` works solely because the repo root is the working directory (pytest papers over this with `pythonpath = ["."]`). The image therefore sets `WORKDIR /app` and copies `server/` in. We deliberately do **not** restructure packaging: the feature branch just merged green, and moving `server/` under `src/` would churn a reviewed codebase for no functional gain.
2. **The live database has real state.** `~/.holdfast/control-plane.db` holds **2 device tokens** and 2 enrollment codes, and has already been migrated to the gateway-scope schema. Deploying a fresh database would invalidate both enrolled devices' report tokens, forcing re-enrollment. Task 4 copies it.

## Global Constraints

- **No secret material in git.** `.env` is gitignored; only `.env.example` with placeholder values is committed. The LiteLLM admin credential is injected as a container environment variable — the 1Password `ai-holdfast` service-account token is **never** placed on the Synology (it is equally sensitive and would only add a dependency).
- **Do not modify** `server/enrollment/`, `server/gateway/`, `server/api/`, or `server/auth/` — the key-minting feature is merged and reviewed. Task 1 is the only production-code change in this plan.
- All shell scripts start with `set -euo pipefail` and pass `bash -n`. (`shellcheck` is not installed on this machine; `tests/install_test.py` skips it — that is the suite's pre-existing "1 skipped".)
- Verification gates after every task: `.venv/bin/python -m pytest tests/ -q`, `.venv/bin/ruff check .`, `.venv/bin/mypy -p holdfastctl -p server`. All three green before each commit. Baseline is **154 passed, 1 skipped**.
- Run all commands from repo root `/home/jarod7736/projects/holdfast-control`. Use `.venv/bin/python` — never system python.
- **Docker is not installed on this laptop.** No task may require building or running a container locally. Image verification happens on the Synology (Task 6) and is explicitly out of the automated gates.
- **Out of scope:** TLS or Tailscale exposure of the control plane API. The enroll response carries raw key material over plain HTTP, which is a real gap on a WPA2-PSK network shared with IoT devices — but it is a separate decision the user has not made. Do not add it here. Do not change the LiteLLM container. Do not touch the `holdfast_iot.lan` / VLAN configuration.

---

## File Structure

| File | Responsibility |
|---|---|
| `server/__init__.py` (modify) | Add `HOLDFAST_DB_PATH` env override to `create_app` |
| `tests/server_config_test.py` (create) | Cover the database-path resolution order |
| `Dockerfile` (create) | Build the control-plane image |
| `.dockerignore` (create) | Keep venv, git, tests, and stray SQLite files out of the build context |
| `docker-compose.yml` (create) | Container Manager project: volume, external network, healthcheck, restart policy |
| `.env.example` (create) | Documented placeholders for every environment-specific value |
| `.gitignore` (modify) | Ignore `.env` and `*.db-snapshot` |
| `scripts/snapshot-control-plane-db.sh` (create) | Consistent SQLite snapshot for migrating to the Synology |
| `docs/deployment-synology.md` (create) | Operator runbook: upload, configure, build, verify, roll back |
| `README.md` (modify) | Link the runbook from a "Deploy the control plane" section |

---

### Task 1: `HOLDFAST_DB_PATH` environment override

The container must place SQLite on the mounted volume. `create_app()` currently hard-codes `~/.holdfast/control-plane.db` with no override, and `server/__init__.py:41` calls it with no arguments at import time. An env var matches the file's existing convention (`HOLDFAST_ADMIN_TOKEN`, `HOLDFAST_LITELLM_URL`) and `server/__main__.py`'s (`HOLDFAST_HOST`, `HOLDFAST_PORT`).

**Files:**
- Modify: `server/__init__.py` (the `database_path is None` branch of `create_app`)
- Test: `tests/server_config_test.py` (create)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `create_app()` resolves its database path as **explicit argument → `HOLDFAST_DB_PATH` → `~/.holdfast/control-plane.db`**. Tasks 2 and 3 depend on the env var name spelled exactly `HOLDFAST_DB_PATH`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/server_config_test.py
"""
Tests for control-plane database-path resolution in create_app.
"""

from pathlib import Path

from server import create_app


def test_env_var_sets_database_path(tmp_path, monkeypatch):
    """HOLDFAST_DB_PATH is used when no explicit path is passed, and parents are created."""
    db = tmp_path / "data" / "control-plane.db"
    monkeypatch.setenv("HOLDFAST_DB_PATH", str(db))
    app = create_app()
    assert app.state.database_path == str(db)
    assert db.exists()


def test_explicit_argument_wins_over_env_var(tmp_path, monkeypatch):
    """An explicit database_path argument takes precedence over the environment."""
    monkeypatch.setenv("HOLDFAST_DB_PATH", str(tmp_path / "from-env.db"))
    explicit = tmp_path / "explicit.db"
    app = create_app(str(explicit))
    assert app.state.database_path == str(explicit)


def test_default_path_when_env_var_unset(tmp_path, monkeypatch):
    """Without the env var, the path falls back to ~/.holdfast/control-plane.db."""
    monkeypatch.delenv("HOLDFAST_DB_PATH", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    app = create_app()
    assert app.state.database_path == str(Path(tmp_path) / ".holdfast" / "control-plane.db")


def test_empty_env_var_falls_back_to_default(tmp_path, monkeypatch):
    """An empty HOLDFAST_DB_PATH is treated as unset, not as a path of ''."""
    monkeypatch.setenv("HOLDFAST_DB_PATH", "")
    monkeypatch.setenv("HOME", str(tmp_path))
    app = create_app()
    assert app.state.database_path == str(Path(tmp_path) / ".holdfast" / "control-plane.db")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/server_config_test.py -v`
Expected: exactly **1 FAIL, 3 pass**. Only `test_env_var_sets_database_path` fails — `create_app` ignores the environment today, so `app.state.database_path` is `~/.holdfast/control-plane.db` rather than the tmp path. The other three describe behavior that already holds (an explicit argument already wins; an unset *or* empty env var already yields the home-directory default), and they are regression guards for Step 3, not new behavior. If any of those three fail here, stop — the environment is not what this plan assumes.

- [ ] **Step 3: Implement**

In `server/__init__.py`, replace the two-line default inside `create_app`:

```python
    if database_path is None:
        database_path = os.environ.get("HOLDFAST_DB_PATH") or str(
            Path.home() / ".holdfast" / "control-plane.db"
        )
```

`os` and `Path` are already imported at the top of the file. `init_database` already does `Path(database_path).parent.mkdir(parents=True, exist_ok=True)`, so nested volume paths work without further change.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/server_config_test.py -v`
Expected: 4 PASS

- [ ] **Step 5: Run full gates**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check . && .venv/bin/mypy -p holdfastctl -p server`
Expected: 158 passed, 1 skipped; ruff clean; mypy clean (19 files)

- [ ] **Step 6: Commit**

```bash
git add server/__init__.py tests/server_config_test.py
git commit -m "Allow HOLDFAST_DB_PATH to override the control-plane database location"
```

---

### Task 2: Container image

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

**Interfaces:**
- Consumes: `HOLDFAST_DB_PATH` from Task 1.
- Produces: an image whose default command is `python -m server`, serving on `0.0.0.0:8000`, storing SQLite at `/data/control-plane.db`, running as UID 10001. Task 3's compose file depends on the `/data` mount point and port 8000.

- [ ] **Step 1: Write the `.dockerignore`**

```
.venv/
.git/
.github/
.claude/
.superpowers/
__pycache__/
*.pyc
*.sqlite
*.db
*.db-snapshot
tests/
docs/
htmlcov/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.env
```

- [ ] **Step 2: Write the `Dockerfile`**

```dockerfile
# Holdfast control plane.
#
# `server/` is not part of the installed distribution (pyproject packages only
# src/), so it is copied next to the install and run with WORKDIR=/app, which
# puts it on sys.path for `python -m server`.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependency layer: changes only when packaging metadata changes.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# Application layer.
COPY server/ ./server/

ENV HOLDFAST_DB_PATH=/data/control-plane.db \
    HOLDFAST_HOST=0.0.0.0 \
    HOLDFAST_PORT=8000

RUN useradd --system --uid 10001 --create-home holdfast \
    && mkdir -p /data \
    && chown -R holdfast:holdfast /data /app
USER holdfast

EXPOSE 8000

CMD ["python", "-m", "server"]
```

- [ ] **Step 3: Verify what can be verified without Docker**

Docker is not installed on this machine, so the build cannot run here. Verify the two things that are checkable statically:

Run: `.venv/bin/python -c "
import pathlib, re
df = pathlib.Path('Dockerfile').read_text()
assert 'COPY server/ ./server/' in df, 'server package must be copied into the image'
assert 'HOLDFAST_DB_PATH=/data/control-plane.db' in df, 'DB must live on the volume'
assert re.search(r'^USER holdfast', df, re.M), 'must not run as root'
print('Dockerfile static checks OK')
"`
Expected: `Dockerfile static checks OK`

Run: `.venv/bin/python -c "
import pathlib
ignored = pathlib.Path('.dockerignore').read_text().split()
for required in ('.venv/', '.git/', '.env', '*.db'):
    assert required in ignored, f'{required} missing from .dockerignore'
print('.dockerignore OK')
"`
Expected: `.dockerignore OK`

- [ ] **Step 4: Run full gates**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check . && .venv/bin/mypy -p holdfastctl -p server`
Expected: unchanged from Task 1 (158 passed, 1 skipped; ruff and mypy clean)

- [ ] **Step 5: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "Add container image for the control plane"
```

---

### Task 3: Compose project and environment template

The compose file lives at the repo root so the Synology project folder *is* the repo folder and the build context is `.` — Container Manager projects do not reliably escape their own directory with `../..` contexts.

**Files:**
- Create: `docker-compose.yml`
- Create: `.env.example`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: the image from Task 2 (`/data` mount, port 8000).
- Produces: a Container Manager project requiring these `.env` keys: `HOLDFAST_ADMIN_TOKEN`, `HOLDFAST_LITELLM_URL`, `HOLDFAST_LITELLM_ADMIN_TOKEN`, `HOLDFAST_DATA_DIR`, `LITELLM_NETWORK_NAME`, `HOLDFAST_PORT_PUBLISHED`. Task 5's runbook documents each one.

- [ ] **Step 1: Write `docker-compose.yml`**

```yaml
services:
  holdfast-control:
    build:
      context: .
      dockerfile: Dockerfile
    image: holdfast-control:local
    container_name: holdfast-control
    restart: unless-stopped
    environment:
      HOLDFAST_DB_PATH: /data/control-plane.db
      HOLDFAST_HOST: 0.0.0.0
      HOLDFAST_PORT: 8000
      HOLDFAST_ADMIN_TOKEN: ${HOLDFAST_ADMIN_TOKEN:?set HOLDFAST_ADMIN_TOKEN in .env}
      HOLDFAST_LITELLM_URL: ${HOLDFAST_LITELLM_URL:?set HOLDFAST_LITELLM_URL in .env}
      HOLDFAST_LITELLM_ADMIN_TOKEN: ${HOLDFAST_LITELLM_ADMIN_TOKEN:?set HOLDFAST_LITELLM_ADMIN_TOKEN in .env}
    volumes:
      - ${HOLDFAST_DATA_DIR:?set HOLDFAST_DATA_DIR in .env}:/data
    ports:
      - "${HOLDFAST_PORT_PUBLISHED:-8000}:8000"
    networks:
      - litellm
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - |
          import sys, urllib.request
          sys.exit(0 if urllib.request.urlopen("http://127.0.0.1:8000/openapi.json", timeout=5).status == 200 else 1)
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s

networks:
  litellm:
    external: true
    name: ${LITELLM_NETWORK_NAME:?set LITELLM_NETWORK_NAME in .env}
```

Two deliberate choices: the healthcheck uses `python` because `python:3.12-slim` ships neither `curl` nor `wget`; and the network is `external: true` so compose **fails loudly** if the name is wrong instead of silently creating a fresh isolated network — which would leave the gateway unreachable by container name and push traffic back onto the LAN.

- [ ] **Step 2: Write `.env.example`**

```bash
# Copy to .env on the Synology and fill in. Never commit .env.
# chmod 600 .env — it holds the LiteLLM key-minting credential.

# Admin token for POST /api/v1/enrollment-codes. Generate with:
#   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
HOLDFAST_ADMIN_TOKEN=

# LiteLLM gateway URL. MUST be the container name on the shared bridge, not
# 192.168.1.181 — using the LAN address sends the admin credential over the
# wire in cleartext, which is the whole reason this runs on the Synology.
# Find the name: Container Manager > Container > (LiteLLM container) > name.
HOLDFAST_LITELLM_URL=http://litellm:4000

# LiteLLM credential authorised for /key/generate. Read it out of 1Password:
#   op read "op://holdfast-automation/litellm-key-admin/credential"
HOLDFAST_LITELLM_ADMIN_TOKEN=

# Host directory bind-mounted at /data (holds control-plane.db).
# Must already exist and be writable by UID 10001.
HOLDFAST_DATA_DIR=/volume1/docker/holdfast-control/data

# Name of the EXISTING Docker network the LiteLLM container is attached to.
# Find it with:  docker inspect <litellm-container> -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}'
# or in Container Manager > Network.
LITELLM_NETWORK_NAME=

# Host port to publish the control plane on. DSM reserves 5000/5001.
HOLDFAST_PORT_PUBLISHED=8000
```

- [ ] **Step 3: Update `.gitignore`**

Append:

```
.env
*.db-snapshot
```

- [ ] **Step 4: Verify the compose file parses and declares the external network**

Run: `.venv/bin/python -c "
import yaml
c = yaml.safe_load(open('docker-compose.yml'))
svc = c['services']['holdfast-control']
assert svc['restart'] == 'unless-stopped'
assert c['networks']['litellm']['external'] is True, 'network must be external'
assert '/data' in svc['volumes'][0], 'volume must mount at /data'
assert 'healthcheck' in svc
print('compose OK:', sorted(svc['environment']))
"`
Expected: `compose OK:` followed by the six environment keys.

Run: `git check-ignore -q .env && echo ".env is ignored"`
Expected: `.env is ignored`

- [ ] **Step 5: Run full gates**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check . && .venv/bin/mypy -p holdfastctl -p server`
Expected: unchanged (158 passed, 1 skipped)

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml .env.example .gitignore
git commit -m "Add Container Manager compose project for the control plane"
```

---

### Task 4: Database snapshot script

The live database holds 2 device tokens. Copying the file while uvicorn is running risks a torn read, so use SQLite's online backup API, which produces a consistent snapshot without stopping the service.

**Files:**
- Create: `scripts/snapshot-control-plane-db.sh`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `scripts/snapshot-control-plane-db.sh [SOURCE_DB] [DEST_FILE]`, defaulting to `~/.holdfast/control-plane.db` → `./control-plane.db-snapshot`. Task 5's runbook calls it by that path.

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
#
# Take a consistent snapshot of the control-plane SQLite database so it can be
# copied to the Synology. Uses SQLite's online backup API, so the running
# control plane does not need to be stopped.
#
# Usage: scripts/snapshot-control-plane-db.sh [SOURCE_DB] [DEST_FILE]
set -euo pipefail

SOURCE_DB="${1:-${HOME}/.holdfast/control-plane.db}"
DEST_FILE="${2:-control-plane.db-snapshot}"

if [ ! -f "${SOURCE_DB}" ]; then
    echo "Error: source database not found: ${SOURCE_DB}" >&2
    exit 1
fi

PYTHON_BIN="$(command -v python3)"
"${PYTHON_BIN}" - "${SOURCE_DB}" "${DEST_FILE}" <<'PYTHON'
import sqlite3
import sys

source_path, dest_path = sys.argv[1], sys.argv[2]
source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
dest = sqlite3.connect(dest_path)
with dest:
    source.backup(dest)
counts = {
    table: dest.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    for table in ("tokens", "enrollment_codes", "gateway_keys")
}
source.close()
dest.close()
print(f"snapshot written to {dest_path}")
print("  " + ", ".join(f"{table}={count}" for table, count in counts.items()))
PYTHON

chmod 600 "${DEST_FILE}"
echo "Copy ${DEST_FILE} to the Synology as <HOLDFAST_DATA_DIR>/control-plane.db"
echo "Then: chown it to UID 10001 (or chmod 666) so the container can write it."
```

- [ ] **Step 2: Verify the script**

Run: `bash -n scripts/snapshot-control-plane-db.sh && chmod +x scripts/snapshot-control-plane-db.sh && ./scripts/snapshot-control-plane-db.sh "${HOME}/.holdfast/control-plane.db" /tmp/claude-1000/-home-jarod7736-projects-holdfast-control/33ee401d-47ed-4955-a183-4cc0cdf9fffd/scratchpad/verify.db-snapshot`
Expected: no syntax errors; output reports `tokens=2, enrollment_codes=2, gateway_keys=0`

Run: `.venv/bin/python -c "
import sqlite3
c = sqlite3.connect('/tmp/claude-1000/-home-jarod7736-projects-holdfast-control/33ee401d-47ed-4955-a183-4cc0cdf9fffd/scratchpad/verify.db-snapshot')
assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
cols = {r[1] for r in c.execute('PRAGMA table_info(enrollment_codes)')}
assert {'gateway_models', 'gateway_mcp_servers'} <= cols, 'snapshot predates the gateway schema'
print('snapshot integrity OK, gateway columns present')
"`
Expected: `snapshot integrity OK, gateway columns present`

- [ ] **Step 3: Run full gates**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check . && .venv/bin/mypy -p holdfastctl -p server`
Expected: unchanged (158 passed, 1 skipped) — `tests/install_test.py` shellcheck test still skips

- [ ] **Step 4: Commit**

```bash
git add scripts/snapshot-control-plane-db.sh
git commit -m "Add consistent SQLite snapshot script for control-plane migration"
```

---

### Task 5: Operator runbook and README section

**Files:**
- Create: `docs/deployment-synology.md`
- Modify: `README.md` (add a "Deploy the control plane" section after "Onboard a new device")

**Interfaces:**
- Consumes: every artifact from Tasks 1–4 by exact filename and env-var name.
- Produces: the operator-facing procedure. No code depends on it.

- [ ] **Step 1: Write `docs/deployment-synology.md`**

````markdown
# Deploying the control plane to Synology

The control plane runs in Container Manager on the DS920+, on the same Docker
network as LiteLLM. Co-location is deliberate: the gateway speaks plain HTTP,
so reaching it by container name keeps the key-minting credential off the LAN.

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
`/volume1/docker/holdfast-control/data/control-plane.db`.

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

## 5. Build and start

Container Manager → **Project** → **Create** → point at
`/volume1/docker/holdfast-control` → it detects `docker-compose.yml` → **Build**.

With SSH, equivalently:

```sh
cd /volume1/docker/holdfast-control && sudo docker compose up -d --build
```

## 6. Verify

```sh
# API is up
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.1.181:8000/openapi.json   # 200

# Migrated state survived — expect the token count from step 1
sudo docker exec holdfast-control python -c \
  "import sqlite3;print(sqlite3.connect('/data/control-plane.db').execute('SELECT COUNT(*) FROM tokens').fetchone())"

# Gateway reachable by container name from inside the container
sudo docker exec holdfast-control python -c \
  "import urllib.request;print(urllib.request.urlopen('http://litellm:4000/health/liveliness',timeout=5).status)"

# End-to-end, no gateway needed: a scope-less code enrolls
HOLDFAST_ADMIN_TOKEN=<token> holdfastctl enroll-code smoke-test \
  --control-plane http://192.168.1.181:8000
curl -s -X POST http://192.168.1.181:8000/api/v1/enroll \
  -H 'Content-Type: application/json' \
  -d '{"code":"<printed>","device_id":"smoke-test"}'
# Expect: {"report_token": "..."} and no gateway_key
```

Key minting additionally requires LiteLLM's `/key/generate` to work. As of
2026-08-02 that endpoint returns 500 (`LiteLLM_VerificationToken.key_type does
not exist`) because the container image was updated without applying its Prisma
migrations. Fix that on the gateway before expecting scoped codes to enrol.

## 7. Point devices at the new host

On each enrolled device, edit `~/.config/holdfastctl/config.yaml`:

```yaml
control_plane_url: http://192.168.1.181:8000
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
````

- [ ] **Step 2: Add the README section**

Insert after the "Onboard a new device" section and before "Development":

```markdown
## Deploy the control plane

The control plane is packaged as a container and runs on the Synology in
Container Manager, on the same Docker network as LiteLLM so the key-minting
credential never crosses the LAN. See
[docs/deployment-synology.md](docs/deployment-synology.md) for the full
procedure.

    ./scripts/snapshot-control-plane-db.sh   # preserve enrolled devices' tokens
    cp .env.example .env                     # fill in, chmod 600
    docker compose up -d --build             # on the Synology

Configuration is entirely environment-driven: `HOLDFAST_DB_PATH`,
`HOLDFAST_HOST`, `HOLDFAST_PORT`, `HOLDFAST_ADMIN_TOKEN`,
`HOLDFAST_LITELLM_URL`, `HOLDFAST_LITELLM_ADMIN_TOKEN`.
```

- [ ] **Step 3: Verify the docs are internally consistent**

Run: `.venv/bin/python -c "
import pathlib, re
doc = pathlib.Path('docs/deployment-synology.md').read_text()
env = pathlib.Path('.env.example').read_text()
keys = set(re.findall(r'^([A-Z][A-Z0-9_]+)=', env, re.M))
compose = pathlib.Path('docker-compose.yml').read_text()
missing = [k for k in keys if k not in compose]
assert not missing, f'env keys unused by compose: {missing}'
assert 'snapshot-control-plane-db.sh' in doc
assert 'external' in compose
print('docs consistent; env keys:', sorted(keys))
"`
Expected: `docs consistent; env keys:` listing all six keys

Run: `grep -n 'Deploy the control plane' README.md`
Expected: one match

- [ ] **Step 4: Run full gates**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check . && .venv/bin/mypy -p holdfastctl -p server`
Expected: unchanged (158 passed, 1 skipped)

- [ ] **Step 5: Commit**

```bash
git add docs/deployment-synology.md README.md
git commit -m "Document Synology deployment procedure for the control plane"
```

---

### Task 6: Operator deployment (manual, on the Synology)

Not automatable from this machine: Docker is not installed here, and this host's
SSH key is rejected by the Synology on port 2222. These steps are the user's to
run; the agent's job ends at Task 5.

- [ ] Run `./scripts/snapshot-control-plane-db.sh` on the laptop
- [ ] Upload the repo and the snapshot per runbook steps 2–3
- [ ] Fill in `.env` per runbook step 4
- [ ] Build and start the project per runbook step 5
- [ ] Run every verification in runbook step 6 **except** scoped key minting
- [ ] Repoint devices per runbook step 7

---

## Validation Criteria (definition of done)

**Automated gates (run after the last agent task):**
1. `.venv/bin/python -m pytest tests/ -q` — 158 passed, 1 skipped (4 new tests over the 154 baseline).
2. `.venv/bin/ruff check .` — clean.
3. `.venv/bin/mypy -p holdfastctl -p server` — clean, 19 files.

**Proven by named tests:**
4. `HOLDFAST_DB_PATH` relocates the database — `test_env_var_sets_database_path`.
5. An explicit argument still wins, so existing callers and the whole test suite are unaffected — `test_explicit_argument_wins_over_env_var`.
6. Behavior without the env var is byte-for-byte unchanged — `test_default_path_when_env_var_unset`, plus the pre-existing suite passing unchanged.
7. An empty env var does not create a database at `''` — `test_empty_env_var_falls_back_to_default`.

**Proven by static checks in the task steps:**
8. The image copies `server/`, runs non-root, and puts SQLite on the volume — Task 2 Step 3.
9. The compose network is `external: true`, so a wrong network name fails loudly instead of silently isolating the container — Task 3 Step 4.
10. `.env` is gitignored — Task 3 Step 4.
11. The snapshot passes `PRAGMA integrity_check` and carries the gateway-scope columns and all 2 device tokens — Task 4 Step 2.
12. Every `.env.example` key is consumed by the compose file — Task 5 Step 3.

**Operator-verified on the Synology (Task 6):**
13. `/openapi.json` returns 200 on the LAN address; the container reaches `http://litellm:4000` by name; the migrated token count matches the snapshot; a scope-less enrollment succeeds.

**Blocked on the gateway (not part of this plan's done):**
14. A scoped enrollment mints a real key. Requires LiteLLM's `/key/generate` to be repaired — its image was upgraded on 2026-08-02 without applying Prisma migrations, so the endpoint currently 500s. This also settles the open question of whether an `--mcp`-only code (which mints with `models=[]`) yields a genuinely restricted key.
