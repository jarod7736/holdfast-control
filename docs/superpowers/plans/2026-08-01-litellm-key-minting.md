# LiteLLM Key Minting at Enrollment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a device enrolls with the control plane, automatically mint a LiteLLM virtual key scoped to the models/MCP servers the operator declared, returning it exactly once — plus the operator CLI and installer flags that make onboarding a new device a three-command flow.

**Architecture:** The gateway scope travels with the enrollment code (the operator declares it when minting the code via the admin-authenticated endpoint), so the server stays manifest-agnostic. During `/api/v1/enroll`, the server calls LiteLLM's admin `/key/generate` API through an injectable `mint_key` callable; the raw key is returned in the enrollment response and never persisted — only metadata (alias, scope, timestamp) is stored. On mint failure the enrollment-code claim rolls back so the code stays usable.

**Tech Stack:** Python 3.12, FastAPI, SQLite, Typer, requests, pytest. No new dependencies.

## Global Constraints

- **Never persist secret material.** Raw keys and tokens must never be written to SQLite, log output, or agent config files. Token hash storage (SHA-256) is the only exception, and applies to report tokens only.
- **Never leak response bodies into error messages** — a LiteLLM error response could contain key material. Error strings carry status codes only.
- The LiteLLM admin credential comes from env vars `HOLDFAST_LITELLM_URL` and `HOLDFAST_LITELLM_ADMIN_TOKEN`; it is narrowly scoped to key management and lives in the `holdfast-automation` 1Password vault, never `holdfast-lan`.
- All SQL is parameterized. Admin-token comparison stays constant-time (existing `server/auth/__init__.py:authorize_admin` — do not touch).
- All HTTP calls have explicit timeouts (10s for admin calls).
- Verification gates after every task: `.venv/bin/python -m pytest tests/ -q`, `.venv/bin/ruff check .`, `.venv/bin/mypy -p holdfastctl -p server`. All three must be green before each commit.
- Scope cap (per ADR 2026-08-01): do NOT extend the apply/rollback engine, do NOT add key-rotation endpoints, do NOT add live MCP-scope verification. Chezmoi is the designated future apply layer.
- Match existing code style: modules are single `__init__.py` files under `server/`, tests are plain pytest functions/classes, docstrings on public functions.
- Run all commands from the repo root `/home/jarod7736/projects/holdfast-control`. Use `.venv/bin/python` — never system python.

---

### Task 1: LiteLLM gateway client (`server/gateway`)

**Files:**
- Create: `server/gateway/__init__.py`
- Test: `tests/gateway_client_test.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `server.gateway.LiteLLMClient(base_url: str, admin_token: str, http_post: HttpPost = default_http_post)` with method `generate_key(device_id: str, models: list[str], mcp_servers: list[str]) -> tuple[str, str]` returning `(raw_key, key_alias)`; exception `server.gateway.GatewayError`; type alias `KeyMinter = Callable[[str, list[str], list[str]], tuple[str, str]]`. Task 4 depends on all three names exactly.

- [ ] **Step 1: Write the failing tests**

```python
# tests/gateway_client_test.py
"""
Tests for the LiteLLM gateway admin client (key minting only).
"""

import pytest

from server.gateway import GatewayError, LiteLLMClient


def test_generate_key_returns_key_and_alias():
    """A 200 response with a key yields (raw_key, alias) and posts the right payload."""
    calls: dict = {}

    def fake_post(url, headers, json_body):
        calls["url"] = url
        calls["headers"] = headers
        calls["body"] = json_body
        return 200, {"key": "sk-minted"}

    client = LiteLLMClient("http://gw:4000/", "admin-token", http_post=fake_post)
    key, alias = client.generate_key("device-a", ["or-cheap"], ["github"])
    assert key == "sk-minted"
    assert alias.startswith("holdfast-device-a-")
    assert calls["url"] == "http://gw:4000/key/generate"
    assert calls["headers"] == {"Authorization": "Bearer admin-token"}
    assert calls["body"]["key_alias"] == alias
    assert calls["body"]["models"] == ["or-cheap"]
    assert calls["body"]["metadata"]["device_id"] == "device-a"
    assert calls["body"]["metadata"]["mcp_servers"] == ["github"]
    assert calls["body"]["metadata"]["managed_by"] == "holdfast-control"


def test_generate_key_unreachable_raises():
    """A network failure (http_post returns None) raises GatewayError."""
    client = LiteLLMClient("http://gw:4000", "t", http_post=lambda url, headers, body: None)
    with pytest.raises(GatewayError, match="unreachable"):
        client.generate_key("device-a", [], [])


def test_generate_key_error_status_raises_without_leaking_body():
    """A non-200 response raises GatewayError whose message never contains the body."""
    client = LiteLLMClient(
        "http://gw:4000", "t", http_post=lambda url, headers, body: (500, {"key": "sk-leak", "error": "boom"})
    )
    with pytest.raises(GatewayError) as excinfo:
        client.generate_key("device-a", [], [])
    assert "sk-leak" not in str(excinfo.value)
    assert "boom" not in str(excinfo.value)


def test_generate_key_missing_key_in_body_raises():
    """A 200 response without a string 'key' field raises GatewayError."""
    client = LiteLLMClient("http://gw:4000", "t", http_post=lambda url, headers, body: (200, {"status": "ok"}))
    with pytest.raises(GatewayError):
        client.generate_key("device-a", [], [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/gateway_client_test.py -v`
Expected: FAIL at import with `ModuleNotFoundError: No module named 'server.gateway'`

- [ ] **Step 3: Write the implementation**

```python
# server/gateway/__init__.py
"""LiteLLM gateway admin client for the Holdfast control plane.

Mints scoped virtual keys via the gateway admin API. The admin token is
narrowly scoped to key management and comes from the holdfast-automation
vault, never holdfast-lan. Error messages never include response bodies,
which could contain key material.
"""

import secrets
from collections.abc import Callable
from typing import Any

HttpPost = Callable[[str, dict[str, str], dict[str, Any]], "tuple[int, Any] | None"]
KeyMinter = Callable[[str, list[str], list[str]], tuple[str, str]]


class GatewayError(Exception):
    """Raised when a LiteLLM admin API call fails."""


def default_http_post(url: str, headers: dict[str, str], json_body: dict[str, Any]) -> tuple[int, Any] | None:
    """POST JSON with a short timeout. Returns (status, parsed body) or None on network failure."""
    import requests

    try:
        response = requests.post(url, headers=headers, json=json_body, timeout=10)
    except requests.RequestException:
        return None
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text
    return response.status_code, body


class LiteLLMClient:
    """Minimal client for the LiteLLM proxy admin API (key management only)."""

    def __init__(self, base_url: str, admin_token: str, http_post: HttpPost = default_http_post):
        self.base_url = base_url.rstrip("/")
        self.admin_token = admin_token
        self.http_post = http_post

    def generate_key(self, device_id: str, models: list[str], mcp_servers: list[str]) -> tuple[str, str]:
        """Mint a virtual key scoped to models; returns (raw_key, key_alias).

        mcp_servers are recorded in key metadata; MCP route enforcement is
        configured gateway-side.
        """
        key_alias = f"holdfast-{device_id}-{secrets.token_hex(4)}"
        response = self.http_post(
            f"{self.base_url}/key/generate",
            {"Authorization": f"Bearer {self.admin_token}"},
            {
                "key_alias": key_alias,
                "models": models,
                "metadata": {
                    "managed_by": "holdfast-control",
                    "device_id": device_id,
                    "mcp_servers": mcp_servers,
                },
            },
        )
        if response is None:
            raise GatewayError("gateway unreachable")
        status_code, body = response
        if status_code != 200 or not isinstance(body, dict) or not isinstance(body.get("key"), str):
            raise GatewayError(f"key generation failed (status {status_code})")
        return body["key"], key_alias
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/gateway_client_test.py -v`
Expected: 4 PASS

- [ ] **Step 5: Run full gates**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check . && .venv/bin/mypy -p holdfastctl -p server`
Expected: all green (mypy file count grows by 1)

- [ ] **Step 6: Commit**

```bash
git add server/gateway/__init__.py tests/gateway_client_test.py
git commit -m "Add LiteLLM gateway admin client for virtual-key minting"
```

---

### Task 2: Storage — gateway scope columns and key-metadata table

**Files:**
- Modify: `server/storage/__init__.py` (the `_SCHEMA` string and `init_database`)
- Test: `tests/storage_migration_test.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `enrollment_codes` gains nullable TEXT columns `gateway_models` and `gateway_mcp_servers` (JSON-encoded lists or NULL); new table `gateway_keys(id TEXT PK, device_id TEXT, key_alias TEXT, models TEXT, mcp_servers TEXT, minted_at REAL)`. `init_database` must upgrade a pre-existing database that lacks the new columns. Tasks 3 and 4 depend on these exact column/table names.

- [ ] **Step 1: Write the failing tests**

```python
# tests/storage_migration_test.py
"""
Tests that init_database creates the gateway-key schema and upgrades old databases.
"""

import sqlite3

from server.storage import init_database


def columns(db_path: str, table: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_fresh_database_has_gateway_scope_columns(tmp_path):
    db = str(tmp_path / "fresh.sqlite")
    init_database(db)
    assert {"gateway_models", "gateway_mcp_servers"} <= columns(db, "enrollment_codes")


def test_fresh_database_has_gateway_keys_table(tmp_path):
    db = str(tmp_path / "fresh.sqlite")
    init_database(db)
    assert {"id", "device_id", "key_alias", "models", "mcp_servers", "minted_at"} <= columns(db, "gateway_keys")


def test_existing_database_is_upgraded(tmp_path):
    """A database created with the old enrollment_codes shape gains the new columns."""
    db = str(tmp_path / "old.sqlite")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE enrollment_codes (code TEXT PRIMARY KEY, device_id TEXT NOT NULL, "
            "expires_at REAL NOT NULL, used_at REAL)"
        )
    init_database(db)
    assert {"gateway_models", "gateway_mcp_servers"} <= columns(db, "enrollment_codes")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/storage_migration_test.py -v`
Expected: 3 FAIL (missing columns / missing table)

- [ ] **Step 3: Implement**

In `server/storage/__init__.py`, change the `enrollment_codes` CREATE statement inside `_SCHEMA` to:

```sql
CREATE TABLE IF NOT EXISTS enrollment_codes (
    code TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    expires_at REAL NOT NULL,
    used_at REAL,
    gateway_models TEXT,
    gateway_mcp_servers TEXT
);
```

Append to `_SCHEMA` (before the closing `"""`):

```sql
CREATE TABLE IF NOT EXISTS gateway_keys (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    key_alias TEXT NOT NULL,
    models TEXT,
    mcp_servers TEXT,
    minted_at REAL NOT NULL
);
```

Add a module-level migrations list after `_SCHEMA` and run it in `init_database` (ALTER fails benignly with "duplicate column" on already-upgraded databases):

```python
_MIGRATIONS: list[str] = [
    "ALTER TABLE enrollment_codes ADD COLUMN gateway_models TEXT",
    "ALTER TABLE enrollment_codes ADD COLUMN gateway_mcp_servers TEXT",
]


def init_database(database_path: str) -> None:
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as connection:
        connection.executescript(_SCHEMA)
        for statement in _MIGRATIONS:
            try:
                connection.execute(statement)
            except sqlite3.OperationalError:
                pass  # column already exists (fresh schema or already migrated)
        connection.executemany(
            "INSERT OR IGNORE INTO adapter_health(adapter, kind) VALUES (?, ?)",
            _ADAPTER_SEEDS,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/storage_migration_test.py -v`
Expected: 3 PASS

- [ ] **Step 5: Run full gates**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check . && .venv/bin/mypy -p holdfastctl -p server`
Expected: all green

- [ ] **Step 6: Commit**

```bash
git add server/storage/__init__.py tests/storage_migration_test.py
git commit -m "Add gateway scope columns and gateway_keys metadata table"
```

---

### Task 3: Enrollment codes carry gateway scope

**Files:**
- Modify: `server/enrollment/__init__.py` (`create_enrollment_code`)
- Modify: `server/api/__init__.py` (`EnrollmentCodeRequest` model and the `/api/v1/enrollment-codes` route)
- Test: `tests/gateway_minting_test.py` (created here, extended in Task 4)

**Interfaces:**
- Consumes: `gateway_models`/`gateway_mcp_servers` columns from Task 2.
- Produces: `create_enrollment_code(database_path: str, device_id: str, expires_in_seconds: int, gateway_models: list[str] | None = None, gateway_mcp_servers: list[str] | None = None) -> dict[str, Any]`. API request model `EnrollmentCodeRequest` gains `gateway_models: list[str]` and `gateway_mcp_servers: list[str]` (both default empty). Task 4 and Task 6 depend on these exact field names.

- [ ] **Step 1: Write the failing test**

```python
# tests/gateway_minting_test.py
"""
Tests for gateway virtual-key scope on enrollment codes and key minting at enroll.
"""

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from server import create_app

ADMIN = "admin-token"


def make_client(tmp_path: Path, mint_key=None) -> tuple[TestClient, Path]:
    db = tmp_path / "control-plane.sqlite"
    return TestClient(create_app(str(db), admin_token=ADMIN, mint_key=mint_key)), db


def mint_code(client: TestClient, device_id: str = "device-a", **extra) -> str:
    response = client.post(
        "/api/v1/enrollment-codes",
        json={"device_id": device_id, **extra},
        headers={"Authorization": f"Bearer {ADMIN}"},
    )
    assert response.status_code == 200, response.text
    return response.json()["code"]


def test_enrollment_code_stores_gateway_scope(tmp_path):
    client, db = make_client(tmp_path)
    code = mint_code(client, gateway_models=["or-cheap", "or-opus"], gateway_mcp_servers=["github"])
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT gateway_models, gateway_mcp_servers FROM enrollment_codes WHERE code = ?", (code,)
        ).fetchone()
    assert json.loads(row[0]) == ["or-cheap", "or-opus"]
    assert json.loads(row[1]) == ["github"]


def test_enrollment_code_without_scope_stores_null(tmp_path):
    client, db = make_client(tmp_path)
    code = mint_code(client)
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT gateway_models, gateway_mcp_servers FROM enrollment_codes WHERE code = ?", (code,)
        ).fetchone()
    assert row == (None, None)
```

Note: `create_app` does not accept `mint_key` yet — Task 4 adds it. For this task, add the parameter as a pass-through stub so the test file imports cleanly (see Step 3).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/gateway_minting_test.py -v`
Expected: FAIL — `create_app() got an unexpected keyword argument 'mint_key'` (or missing-column/validation errors once the signature is stubbed)

- [ ] **Step 3: Implement**

In `server/__init__.py`, extend `create_app` (full replacement of the function signature and router line; keep the rest):

```python
def create_app(
    database_path: str | None = None,
    admin_token: str | None = None,
    mint_key: "KeyMinter | None" = None,
) -> FastAPI:
```

with `from server.gateway import KeyMinter` added to the imports, and pass `mint_key` through:

```python
    app.router.routes.extend(create_router(database_path, admin_token, mint_key).routes)
```

In `server/api/__init__.py`, extend the request model and router signature (Task 4 wires `mint_key` into `/enroll`; here it is accepted and ignored):

```python
class EnrollmentCodeRequest(BaseModel):
    device_id: str = Field(min_length=1)
    expires_in_seconds: int = Field(default=600, ge=1, le=3600)
    gateway_models: list[str] = Field(default_factory=list)
    gateway_mcp_servers: list[str] = Field(default_factory=list)
```

```python
def create_router(database_path: str, admin_token: str | None = None, mint_key: "KeyMinter | None" = None) -> APIRouter:
```

(add `from server.gateway import KeyMinter` to the imports) and update the enrollment-codes route body:

```python
    @router.post("/api/v1/enrollment-codes")
    def create_enrollment_code(request: EnrollmentCodeRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(authorization)
        return enrollment.create_enrollment_code(
            database_path,
            request.device_id,
            request.expires_in_seconds,
            gateway_models=request.gateway_models,
            gateway_mcp_servers=request.gateway_mcp_servers,
        )
```

In `server/enrollment/__init__.py`, replace `create_enrollment_code` (add `import json` to the module imports):

```python
def create_enrollment_code(
    database_path: str,
    device_id: str,
    expires_in_seconds: int,
    gateway_models: list[str] | None = None,
    gateway_mcp_servers: list[str] | None = None,
) -> dict[str, Any]:
    code = secrets.token_urlsafe(32)
    expires_at = time.time() + expires_in_seconds
    with connection(database_path) as conn:
        conn.execute(
            "INSERT INTO enrollment_codes(code, device_id, expires_at, gateway_models, gateway_mcp_servers) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                code,
                device_id,
                expires_at,
                json.dumps(gateway_models) if gateway_models else None,
                json.dumps(gateway_mcp_servers) if gateway_mcp_servers else None,
            ),
        )
    return {
        "code": code,
        "device_id": device_id,
        "expires_at": expires_at,
        "gateway_models": gateway_models or [],
        "gateway_mcp_servers": gateway_mcp_servers or [],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/gateway_minting_test.py -v`
Expected: 2 PASS

- [ ] **Step 5: Run full gates**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check . && .venv/bin/mypy -p holdfastctl -p server`
Expected: all green (existing enrollment tests unaffected — new fields default to empty)

- [ ] **Step 6: Commit**

```bash
git add server/__init__.py server/api/__init__.py server/enrollment/__init__.py tests/gateway_minting_test.py
git commit -m "Enrollment codes carry operator-declared gateway key scope"
```

---

### Task 4: Enroll mints the gateway key

**Files:**
- Modify: `server/enrollment/__init__.py` (`exchange_enrollment_code`)
- Modify: `server/api/__init__.py` (the `/api/v1/enroll` route passes `mint_key`; enroll response type widens to `dict[str, Any]`)
- Modify: `server/__init__.py` (env-derived default minter)
- Test: `tests/gateway_minting_test.py` (extend)

**Interfaces:**
- Consumes: `LiteLLMClient`, `GatewayError`, `KeyMinter` from Task 1; scope columns and `gateway_keys` table from Task 2; `mint_key` pass-through from Task 3.
- Produces: `exchange_enrollment_code(database_path: str, code: str, device_id: str, mint_key: KeyMinter | None = None) -> dict[str, Any]`. Enroll response contains `report_token` always, plus `gateway_key` and `gateway_key_alias` when scope was declared. Task 5 depends on those exact response keys. Behavior contract: scope declared + no minter configured → 503; mint failure → 502 with the code claim rolled back (code stays usable).

- [ ] **Step 1: Write the failing tests** (append to `tests/gateway_minting_test.py`)

```python
def enroll(client: TestClient, code: str, device_id: str = "device-a"):
    return client.post("/api/v1/enroll", json={"code": code, "device_id": device_id})


def test_enroll_without_scope_returns_only_report_token(tmp_path):
    client, _ = make_client(tmp_path)
    code = mint_code(client)
    response = enroll(client, code)
    assert response.status_code == 200
    body = response.json()
    assert "report_token" in body
    assert "gateway_key" not in body


def test_enroll_with_scope_mints_scoped_key(tmp_path):
    minted: dict = {}

    def fake_mint(device_id, models, mcp_servers):
        minted.update(device_id=device_id, models=models, mcp_servers=mcp_servers)
        return "sk-minted-key", f"holdfast-{device_id}-abcd1234"

    client, _ = make_client(tmp_path, mint_key=fake_mint)
    code = mint_code(client, gateway_models=["or-cheap"], gateway_mcp_servers=["github"])
    response = enroll(client, code)
    assert response.status_code == 200
    body = response.json()
    assert body["gateway_key"] == "sk-minted-key"
    assert body["gateway_key_alias"] == "holdfast-device-a-abcd1234"
    assert minted == {"device_id": "device-a", "models": ["or-cheap"], "mcp_servers": ["github"]}


def test_gateway_key_material_never_persisted(tmp_path):
    client, db = make_client(tmp_path, mint_key=lambda d, m, s: ("sk-canary-key-material", "alias-1"))
    code = mint_code(client, gateway_models=["or-cheap"])
    assert enroll(client, code).status_code == 200
    assert b"sk-canary-key-material" not in db.read_bytes()


def test_gateway_key_metadata_recorded(tmp_path):
    client, db = make_client(tmp_path, mint_key=lambda d, m, s: ("sk-x", "holdfast-device-a-ff00ff00"))
    code = mint_code(client, gateway_models=["or-cheap"], gateway_mcp_servers=["github"])
    assert enroll(client, code).status_code == 200
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT device_id, key_alias, models, mcp_servers FROM gateway_keys"
        ).fetchone()
    assert row[0] == "device-a"
    assert row[1] == "holdfast-device-a-ff00ff00"
    assert json.loads(row[2]) == ["or-cheap"]
    assert json.loads(row[3]) == ["github"]


def test_mint_failure_returns_502_and_code_stays_usable(tmp_path):
    from server.gateway import GatewayError

    attempts: list[int] = []

    def flaky_mint(device_id, models, mcp_servers):
        attempts.append(1)
        if len(attempts) == 1:
            raise GatewayError("gateway unreachable")
        return "sk-second-try", "alias-2"

    client, _ = make_client(tmp_path, mint_key=flaky_mint)
    code = mint_code(client, gateway_models=["or-cheap"])
    assert enroll(client, code).status_code == 502
    retry = enroll(client, code)
    assert retry.status_code == 200
    assert retry.json()["gateway_key"] == "sk-second-try"


def test_scope_without_configured_minter_returns_503(tmp_path):
    client, _ = make_client(tmp_path, mint_key=None)
    code = mint_code(client, gateway_models=["or-cheap"])
    assert enroll(client, code).status_code == 503
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/gateway_minting_test.py -v`
Expected: `test_enroll_without_scope_returns_only_report_token` PASSES (existing behavior); the other 5 new tests FAIL (no `gateway_key` in response, no 502/503 handling)

- [ ] **Step 3: Implement**

In `server/enrollment/__init__.py`, replace `exchange_enrollment_code` (add imports: `import json`, `from server.gateway import GatewayError, KeyMinter`):

```python
def exchange_enrollment_code(
    database_path: str, code: str, device_id: str, mint_key: KeyMinter | None = None
) -> dict[str, Any]:
    with connection(database_path) as conn:
        row = conn.execute(
            "SELECT device_id, expires_at, used_at, gateway_models, gateway_mcp_servers "
            "FROM enrollment_codes WHERE code = ?",
            (code,),
        ).fetchone()
        if row is None or row["used_at"] is not None or row["expires_at"] <= time.time():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or expired enrollment code")
        if row["device_id"] != device_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Enrollment code is bound to another device")
        # Atomically claim the code so concurrent exchanges cannot both succeed.
        # Raising below this point rolls the claim back (connection context
        # manager), so a failed mint leaves the code usable for a retry.
        claimed = conn.execute(
            "UPDATE enrollment_codes SET used_at = ? WHERE code = ? AND used_at IS NULL", (time.time(), code)
        )
        if claimed.rowcount != 1:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or expired enrollment code")

        gateway_key: str | None = None
        gateway_key_alias: str | None = None
        models = json.loads(row["gateway_models"]) if row["gateway_models"] else []
        mcp_servers = json.loads(row["gateway_mcp_servers"]) if row["gateway_mcp_servers"] else []
        if models or mcp_servers:
            if mint_key is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Gateway key requested but the control plane has no LiteLLM admin credential "
                    "(set HOLDFAST_LITELLM_URL and HOLDFAST_LITELLM_ADMIN_TOKEN)",
                )
            try:
                gateway_key, gateway_key_alias = mint_key(device_id, models, mcp_servers)
            except GatewayError as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Gateway key minting failed") from exc
            conn.execute(
                "INSERT INTO gateway_keys(id, device_id, key_alias, models, mcp_servers, minted_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    device_id,
                    gateway_key_alias,
                    json.dumps(models),
                    json.dumps(mcp_servers),
                    time.time(),
                ),
            )

        raw_token = secrets.token_urlsafe(48)
        conn.execute(
            "INSERT INTO tokens(id, device_id, token_hash, created_at) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), device_id, hash_token(raw_token), time.time()),
        )
    result: dict[str, Any] = {"report_token": raw_token}
    if gateway_key is not None:
        result["gateway_key"] = gateway_key
        result["gateway_key_alias"] = gateway_key_alias
    return result
```

In `server/api/__init__.py`, update the enroll route (return type widens):

```python
    @router.post("/api/v1/enroll")
    def enroll(request: EnrollmentRequest) -> dict[str, Any]:
        return enrollment.exchange_enrollment_code(database_path, request.code, request.device_id, mint_key=mint_key)
```

In `server/__init__.py`, build the env-derived default minter inside `create_app` (before the router line):

```python
    if mint_key is None:
        litellm_url = os.environ.get("HOLDFAST_LITELLM_URL")
        litellm_admin_token = os.environ.get("HOLDFAST_LITELLM_ADMIN_TOKEN")
        if litellm_url and litellm_admin_token:
            from server.gateway import LiteLLMClient

            mint_key = LiteLLMClient(litellm_url, litellm_admin_token).generate_key
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/gateway_minting_test.py -v`
Expected: 8 PASS

- [ ] **Step 5: Run full gates**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check . && .venv/bin/mypy -p holdfastctl -p server`
Expected: all green (existing security tests still pass — enroll without scope is unchanged)

- [ ] **Step 6: Commit**

```bash
git add server/enrollment/__init__.py server/api/__init__.py server/__init__.py tests/gateway_minting_test.py
git commit -m "Mint scoped LiteLLM virtual key during enrollment; metadata only in SQLite"
```

---

### Task 5: Agent receives the key and shows it exactly once

**Files:**
- Modify: `src/holdfastctl/reporting.py` (`enroll`, `_load_or_create_token`, `StatusReporter.__init__`)
- Modify: `src/holdfastctl/cli.py` (`report` command output)
- Test: `tests/reporting_test.py` (append to `TestStatusReporter`), `tests/install_test.py` untouched

**Interfaces:**
- Consumes: enroll response keys `report_token`, `gateway_key`, `gateway_key_alias` from Task 4.
- Produces: `EnrollmentResult` dataclass in `holdfastctl.reporting` with fields `report_token: str`, `gateway_key: str | None`, `gateway_key_alias: str | None`. `StatusReporter.enroll(enrollment_code: str) -> EnrollmentResult` (was `-> str`). New instance attributes `StatusReporter.pending_gateway_key: str | None` and `pending_gateway_key_alias: str | None`, set after a fresh enrollment that minted a key. The CLI prints the key once; it is never written to disk.

- [ ] **Step 1: Write the failing tests** (append inside `TestStatusReporter` in `tests/reporting_test.py`)

```python
    @patch('requests.post')
    def test_enroll_returns_gateway_key_and_never_stores_it(self, mock_post, tmp_path):
        """A minted gateway key is surfaced on the reporter but never written to disk."""
        def responses(url, **kwargs):
            reply = type('R', (), {})()
            reply.status_code = 200
            if url.endswith('/api/v1/enroll'):
                reply.json = lambda: {
                    "report_token": "fresh-token",
                    "gateway_key": "sk-minted-key",
                    "gateway_key_alias": "holdfast-test-device-123-aa11",
                }
            else:
                reply.json = lambda: {"status": "accepted"}
            return reply

        mock_post.side_effect = responses
        token_file = tmp_path / "report.token"
        reporter = StatusReporter("http://cp", "test-device-123", token_path=str(token_file))
        assert reporter.report_status({"status": "healthy"}, enrollment_code="code-123") is True
        assert reporter.pending_gateway_key == "sk-minted-key"
        assert reporter.pending_gateway_key_alias == "holdfast-test-device-123-aa11"
        assert token_file.read_text() == "fresh-token"
        assert "sk-minted-key" not in token_file.read_text()

    @patch('requests.post')
    def test_enroll_without_gateway_key_leaves_pending_none(self, mock_post, tmp_path):
        """Enrollment without a minted key leaves pending_gateway_key as None."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"report_token": "fresh-token"}
        token_file = tmp_path / "report.token"
        reporter = StatusReporter("http://cp", "test-device-123", token_path=str(token_file))
        reporter.report_status({"status": "healthy"}, enrollment_code="code-123")
        assert reporter.pending_gateway_key is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/reporting_test.py -v -k gateway`
Expected: 2 FAIL with `AttributeError: 'StatusReporter' object has no attribute 'pending_gateway_key'`

- [ ] **Step 3: Implement**

In `src/holdfastctl/reporting.py`, add after the imports:

```python
from dataclasses import dataclass


@dataclass
class EnrollmentResult:
    """Outcome of exchanging an enrollment code: report token plus optional one-time gateway key."""

    report_token: str
    gateway_key: str | None = None
    gateway_key_alias: str | None = None
```

In `StatusReporter.__init__`, add:

```python
        self.pending_gateway_key: str | None = None
        self.pending_gateway_key_alias: str | None = None
```

Replace `enroll` (same docstring style):

```python
    def enroll(self, enrollment_code: str) -> EnrollmentResult:
        """Exchange an operator-provisioned one-time enrollment code for a report token.

        The response may include a one-time gateway virtual key; it is returned
        to the caller and never persisted.

        Raises:
            ReportingError: If enrollment fails
        """
        enroll_response = requests.post(
            f"{self.control_plane_url}/api/v1/enroll",
            json={"code": enrollment_code, "device_id": self.device_id},
        )
        if enroll_response.status_code != 200:
            raise ReportingError(f"Enrollment failed: {enroll_response.text}")
        body = enroll_response.json()
        token: Any = body.get("report_token")
        if not isinstance(token, str):
            raise ReportingError("Enrollment response missing report_token")
        gateway_key = body.get("gateway_key")
        gateway_key_alias = body.get("gateway_key_alias")
        return EnrollmentResult(
            report_token=token,
            gateway_key=gateway_key if isinstance(gateway_key, str) else None,
            gateway_key_alias=gateway_key_alias if isinstance(gateway_key_alias, str) else None,
        )
```

In `_load_or_create_token`, replace the enrollment tail (from `raw_token = self.enroll(enrollment_code)` to the end) with:

```python
        result = self.enroll(enrollment_code)
        self.pending_gateway_key = result.gateway_key
        self.pending_gateway_key_alias = result.gateway_key_alias
        if self.token_path is not None:
            token_path = Path(self.token_path)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(result.report_token)
            os.chmod(token_path, 0o600)
        return result.report_token
```

In `src/holdfastctl/cli.py`, in the `report` command, replace the final `typer.echo(f"Reported {device_id} to {control_plane_url}")` with:

```python
    typer.echo(f"Reported {device_id} to {control_plane_url}")
    if reporter.pending_gateway_key:
        typer.echo("")
        typer.echo("Gateway virtual key minted for this device (shown ONCE, never stored):")
        typer.echo(f"  alias: {reporter.pending_gateway_key_alias}")
        typer.echo(f"  key:   {reporter.pending_gateway_key}")
        typer.echo("Store it in 1Password now, then export it in your shell profile, e.g.:")
        typer.echo(
            f"  op item create --vault holdfast-lan --category 'API Credential' "
            f"--title 'litellm-{device_id}' credential='<paste key>'"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/reporting_test.py tests/install_test.py -v`
Expected: all PASS (existing enroll-flow tests unaffected: mocks without `gateway_key` yield `pending_gateway_key is None`)

- [ ] **Step 5: Run full gates**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check . && .venv/bin/mypy -p holdfastctl -p server`
Expected: all green

- [ ] **Step 6: Commit**

```bash
git add src/holdfastctl/reporting.py src/holdfastctl/cli.py tests/reporting_test.py
git commit -m "Agent surfaces one-time gateway key at enrollment; never persisted"
```

---

### Task 6: Operator `enroll-code` CLI command

**Files:**
- Modify: `src/holdfastctl/cli.py` (new command)
- Test: `tests/cli_test.py` (append a test class)

**Interfaces:**
- Consumes: `/api/v1/enrollment-codes` request fields from Task 3 (`gateway_models`, `gateway_mcp_servers`).
- Produces: `holdfastctl enroll-code DEVICE_ID [--models a,b] [--mcp x,y] [--control-plane URL] [--expires N]`, reading the admin token from `HOLDFAST_ADMIN_TOKEN`. This closes the dangling reference in `src/holdfastctl/reporting.py:66` ("Ask the operator to mint a code (`holdfastctl enroll-code DEVICE_ID`)").

- [ ] **Step 1: Write the failing tests** (append to `tests/cli_test.py`)

```python
class TestEnrollCodeCommand:
    """The operator enroll-code command mints a device-bound code with gateway scope."""

    def test_requires_admin_token_env(self, monkeypatch):
        from typer.testing import CliRunner

        from holdfastctl.cli import app

        monkeypatch.delenv("HOLDFAST_ADMIN_TOKEN", raising=False)
        result = CliRunner().invoke(app, ["enroll-code", "device-a"])
        assert result.exit_code == 1
        assert "HOLDFAST_ADMIN_TOKEN" in result.output

    def test_mints_code_with_scope(self, monkeypatch):
        from unittest.mock import patch

        from typer.testing import CliRunner

        from holdfastctl.cli import app

        monkeypatch.setenv("HOLDFAST_ADMIN_TOKEN", "admin-token")
        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"code": "one-time-code", "device_id": "device-a"}
            result = CliRunner().invoke(
                app,
                [
                    "enroll-code", "device-a",
                    "--models", "or-cheap,or-opus",
                    "--mcp", "github",
                    "--control-plane", "http://cp:8000",
                ],
            )
        assert result.exit_code == 0
        assert "one-time-code" in result.output
        request = mock_post.call_args
        assert request.args[0] == "http://cp:8000/api/v1/enrollment-codes"
        assert request.kwargs["headers"] == {"Authorization": "Bearer admin-token"}
        assert request.kwargs["json"]["gateway_models"] == ["or-cheap", "or-opus"]
        assert request.kwargs["json"]["gateway_mcp_servers"] == ["github"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/cli_test.py -v -k EnrollCode`
Expected: 2 FAIL — `No such command 'enroll-code'` (exit code 2 / usage error in output)

- [ ] **Step 3: Implement** — add to `src/holdfastctl/cli.py` (after the `report` command; `import os` and `import requests` go inside the function, matching the file's lazy-import style):

```python
@app.command("enroll-code")
def enroll_code(
    device_id: str = typer.Argument(..., help="Device id the one-time code is bound to"),
    models: str = typer.Option("", "--models", help="Comma-separated gateway model ids for the device's virtual key"),
    mcp_servers: str = typer.Option("", "--mcp", help="Comma-separated MCP server ids recorded in key metadata"),
    control_plane_url: str = typer.Option("http://127.0.0.1:8000", "--control-plane", help="Control plane URL"),
    expires_in_seconds: int = typer.Option(600, "--expires", help="Code lifetime in seconds"),
) -> None:
    """Operator: mint a one-time enrollment code (requires HOLDFAST_ADMIN_TOKEN)."""
    import os

    import requests

    admin_token = os.environ.get("HOLDFAST_ADMIN_TOKEN")
    if not admin_token:
        typer.echo("Error: HOLDFAST_ADMIN_TOKEN is not set", err=True)
        raise typer.Exit(code=1)
    payload = {
        "device_id": device_id,
        "expires_in_seconds": expires_in_seconds,
        "gateway_models": [m.strip() for m in models.split(",") if m.strip()],
        "gateway_mcp_servers": [m.strip() for m in mcp_servers.split(",") if m.strip()],
    }
    try:
        response = requests.post(
            f"{control_plane_url}/api/v1/enrollment-codes",
            json=payload,
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=10,
        )
    except requests.RequestException as e:
        typer.echo(f"Error: control plane unreachable: {e}", err=True)
        raise typer.Exit(code=1)
    if response.status_code != 200:
        typer.echo(f"Error: code creation failed (status {response.status_code})", err=True)
        raise typer.Exit(code=1)
    code = response.json()["code"]
    typer.echo(f"Enrollment code for {device_id} (valid {expires_in_seconds}s):")
    typer.echo(f"  {code}")
    typer.echo(f"On the device: holdfastctl report --enrollment-code {code}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/cli_test.py -v -k EnrollCode`
Expected: 2 PASS

- [ ] **Step 5: Run full gates**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check . && .venv/bin/mypy -p holdfastctl -p server`
Expected: all green

- [ ] **Step 6: Commit**

```bash
git add src/holdfastctl/cli.py tests/cli_test.py
git commit -m "Add operator enroll-code command with gateway key scope"
```

---

### Task 7: Installer onboarding flags and docs

**Files:**
- Modify: `scripts/install-agent.sh` (flag parsing, config template, first report)
- Modify: `README.md` (API list, onboarding section)
- Test: existing `tests/install_test.py` must stay green (it runs shellcheck and asserts the script's shape)

**Interfaces:**
- Consumes: `holdfastctl report --enrollment-code` (existing) and `enroll-code` flow from Task 6.
- Produces: `scripts/install-agent.sh [REPO_ROOT] [--control-plane URL] [--enrollment-code CODE]`. Onboarding a new device becomes: (1) operator runs `holdfastctl enroll-code <device> --models ... --mcp ...`, (2) device runs `scripts/install-agent.sh --control-plane http://<server>:8000 --enrollment-code <code>`, (3) operator stores the printed key in 1Password.

- [ ] **Step 1: Modify `scripts/install-agent.sh`**

Replace the header comment usage line and the `REPO_ROOT=` line with flag parsing:

```bash
# Usage: scripts/install-agent.sh [REPO_ROOT] [--control-plane URL] [--enrollment-code CODE]
#   REPO_ROOT defaults to the repository root (parent of this script).
set -euo pipefail

CONTROL_PLANE_URL="http://127.0.0.1:8000"
ENROLLMENT_CODE=""
POSITIONAL_REPO=""
while [ $# -gt 0 ]; do
    case "$1" in
        --control-plane) CONTROL_PLANE_URL="$2"; shift 2 ;;
        --enrollment-code) ENROLLMENT_CODE="$2"; shift 2 ;;
        *) POSITIONAL_REPO="$1"; shift ;;
    esac
done
REPO_ROOT="${POSITIONAL_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
```

In the config heredoc, replace the hardcoded URL line with:

```bash
control_plane_url: ${CONTROL_PLANE_URL}
```

After the timer is enabled (after the `systemctl --user enable --now` line), add:

```bash
# 4. First report: enroll immediately when a code was provided.
if [ -n "${ENROLLMENT_CODE}" ]; then
    echo "[enroll] Running first report with enrollment code"
    "${AGENT_BIN}" report --enrollment-code "${ENROLLMENT_CODE}" || \
        echo "[enroll] WARNING: first report failed — run manually: ${AGENT_BIN} report --enrollment-code <code>" >&2
fi
```

- [ ] **Step 2: Verify the script**

Run: `bash -n scripts/install-agent.sh && shellcheck scripts/install-agent.sh && .venv/bin/python -m pytest tests/install_test.py -v`
Expected: no syntax errors, shellcheck clean, install tests PASS

- [ ] **Step 3: Update `README.md`**

In the API list, replace the enrollment-codes and enroll lines with:

```markdown
- `POST /api/v1/enrollment-codes` — generate an enrollment code for a device, optionally declaring `gateway_models`/`gateway_mcp_servers` for its LiteLLM virtual key (admin token required)
- `POST /api/v1/enroll` — exchange an operator-provisioned code for a raw report token; when the code declares gateway scope, the response also carries a one-time scoped LiteLLM virtual key (public)
```

After the "Install the agent" section's existing content, add:

```markdown
## Onboard a new device

1. Operator (any machine with the admin token):
   `HOLDFAST_ADMIN_TOKEN=... holdfastctl enroll-code <device-id> --models or-cheap,or-coder --mcp github --control-plane http://<server>:8000`
2. On the device:
   `scripts/install-agent.sh --control-plane http://<server>:8000 --enrollment-code <code>`
3. The first report prints the device's LiteLLM virtual key exactly once — store it in 1Password (`holdfast-lan`) and export it as `LITELLM_API_KEY` in the device's shell profile.

Key minting requires the control plane to run with `HOLDFAST_LITELLM_URL` and `HOLDFAST_LITELLM_ADMIN_TOKEN`
(a key-management-scoped credential from the `holdfast-automation` vault). Without them, codes minted with
gateway scope fail enrollment with 503; codes without scope enroll normally.
```

- [ ] **Step 4: Run full gates**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check . && .venv/bin/mypy -p holdfastctl -p server`
Expected: all green

- [ ] **Step 5: Commit**

```bash
git add scripts/install-agent.sh README.md
git commit -m "Installer onboarding flags and documented three-step device onboarding"
```

---

## Validation Criteria (definition of done)

Every criterion maps to an automated test except the final manual E2E.

**Gates (run after the last task):**
1. `.venv/bin/python -m pytest tests/ -q` — all pass (expect 19 new tests over the 132-passed/1-skipped baseline).
2. `.venv/bin/ruff check .` — clean.
3. `.venv/bin/mypy -p holdfastctl -p server` — clean (19 files, up from 18).

**Security invariants (each proven by a named test):**
4. Raw gateway-key material never lands in SQLite — `test_gateway_key_material_never_persisted` (byte-level scan of the DB file).
5. Gateway errors never leak response bodies — `test_generate_key_error_status_raises_without_leaking_body`.
6. A failed mint leaves the enrollment code usable (no burned codes) — `test_mint_failure_returns_502_and_code_stays_usable`.
7. Scope declared but minting unconfigured fails closed with 503 — `test_scope_without_configured_minter_returns_503`.
8. The agent never writes the gateway key to disk; only the report token is stored (0600) — `test_enroll_returns_gateway_key_and_never_stores_it`.
9. Enrollment without scope is byte-for-byte the old behavior — `test_enroll_without_scope_returns_only_report_token` plus the pre-existing `tests/test_control_plane_security.py` suite passing unchanged.

**Behavioral criteria:**
10. Key scope derives from operator-declared code scope; alias format `holdfast-<device_id>-<8 hex>`; metadata row recorded — `test_enroll_with_scope_mints_scoped_key`, `test_gateway_key_metadata_recorded`.
11. Old control-plane databases upgrade in place — `test_existing_database_is_upgraded`.
12. `holdfastctl enroll-code` works and requires `HOLDFAST_ADMIN_TOKEN` — `TestEnrollCodeCommand` (2 tests).
13. `shellcheck scripts/install-agent.sh` clean; `tests/install_test.py` green.

**Manual E2E (operator runs once, after implementation):**
```sh
# Terminal 1 — control plane with a fake-real config against the live gateway:
HOLDFAST_ADMIN_TOKEN=test-admin \
HOLDFAST_LITELLM_URL=http://192.168.1.181:4000 \
HOLDFAST_LITELLM_ADMIN_TOKEN=$(op read "op://holdfast-automation/litellm-key-admin/credential") \
.venv/bin/python -m server

# Terminal 2 — mint a code and enroll a scratch device:
HOLDFAST_ADMIN_TOKEN=test-admin .venv/bin/holdfastctl enroll-code e2e-test-device --models or-cheap
curl -s -X POST http://127.0.0.1:8000/api/v1/enroll -H 'Content-Type: application/json' \
  -d '{"code": "<printed code>", "device_id": "e2e-test-device"}'
# Expect: JSON with report_token + gateway_key (sk-...) + gateway_key_alias.
# Verify the minted key works and is scoped:
curl -s http://192.168.1.181:4000/v1/models -H "Authorization: Bearer <gateway_key>"
# Expect: only or-cheap listed. Then delete the test key in the LiteLLM UI.
```
Note: the `op://holdfast-automation/litellm-key-admin` item must be created by the operator first (key-management-scoped LiteLLM credential). If the live LiteLLM version rejects any `/key/generate` field, adjust `LiteLLMClient.generate_key`'s payload — the test suite pins the contract, so update tests and client together.
