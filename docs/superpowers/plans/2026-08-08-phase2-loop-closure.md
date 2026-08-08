# Phase 2 Loop Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the reconciliation loop so a device can produce a plan, an operator can approve it, and the device can apply it safely and roll it back.

**Architecture:** The control plane stores only a state fingerprint (`desired_commit`, `current_hash`, expiry) — never the plan's actions. The agent re-derives actions locally at apply time and refuses if the freshly computed hash no longer matches what was approved. Capability adapters own the knowledge of how to mutate their own config; `apply.py` provides only a generic atomic-write primitive.

**Tech Stack:** Python 3.12+, Typer (CLI), FastAPI + SQLite (control plane), pytest, ruff, mypy.

**Spec:** [2026-08-08-phase2-loop-closure-design.md](../specs/2026-08-08-phase2-loop-closure-design.md)

## Global Constraints

- Python `>=3.12` (`pyproject.toml` `requires-python`).
- `ruff check` must pass with no findings.
- `mypy -p holdfastctl -p server` must report success.
- Baseline suite is **204 passed, 5 skipped**. No task may reduce passing count.
- Test files: the repo uses both `tests/<module>_test.py` (agent modules) and `tests/test_<area>.py` (control-plane security). Follow whichever the neighbouring tests use. Plain pytest functions; no unittest classes, no `conftest.py` fixtures — helpers are defined per-file, as `tests/test_control_plane_security.py` does.
- Commit messages use the repo's existing style: imperative subject line, no `feat:`/`fix:` prefix. Body explains why.
- No secrets in any committed file.
- The agent never executes actions supplied by the control plane.

---

### Task 1: Correct atomic write primitive

Replaces the whole-file overwrite and the cross-filesystem move in `apply.py`. Everything downstream writes through this.

**Files:**
- Modify: `src/holdfastctl/apply.py` (replace `AtomicApplier`/`ConfigurationApplier` bodies)
- Modify: `src/holdfastctl/__init__.py:5` (exports)
- Test: `tests/apply_test.py` (rewrite — existing tests cover the deleted API)

**Interfaces:**
- Consumes: `validate_path_safety(path: str) -> None` from `holdfastctl.manifest_schema`
- Produces:
  - `DEFAULT_MANAGED_PREFIXES: tuple[Path, ...]`
  - `atomic_write(path: Path, data: str, *, allowed_prefixes: tuple[Path, ...] = DEFAULT_MANAGED_PREFIXES) -> None`
  - `ApplyError(Exception)` (retained)

Note: `validate_path_safety` only constrains paths to `$HOME`, which is broader than the allowlist the schema README documents. `atomic_write` adds the tighter prefix check on top. `allowed_prefixes` is injectable so tests can write under `tmp_path`.

- [ ] **Step 1: Write the failing tests**

Replace the contents of `tests/apply_test.py`:

```python
"""
Tests for the atomic write primitive.

apply.py owns exactly one job: replace a file's contents atomically,
preserving mode, without ever leaving a partial write behind.
"""

import os
import stat
import tempfile
from pathlib import Path

import pytest

from holdfastctl.apply import ApplyError, atomic_write


def test_writes_content(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    atomic_write(target, '{"a": 1}', allowed_prefixes=(tmp_path,))
    assert target.read_text() == '{"a": 1}'


def test_temp_file_is_created_in_destination_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a temp file in /tmp makes os.replace fail across filesystems."""
    seen: dict[str, object] = {}
    real_mkstemp = tempfile.mkstemp

    def spy(*args: object, **kwargs: object) -> tuple[int, str]:
        seen["dir"] = kwargs.get("dir")
        return real_mkstemp(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(tempfile, "mkstemp", spy)
    target = tmp_path / "config.json"
    atomic_write(target, "{}", allowed_prefixes=(tmp_path,))
    assert seen["dir"] == str(tmp_path)


def test_preserves_existing_mode(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text("old")
    os.chmod(target, 0o600)
    atomic_write(target, "new", allowed_prefixes=(tmp_path,))
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_new_file_is_not_world_readable(tmp_path: Path) -> None:
    target = tmp_path / "fresh.json"
    atomic_write(target, "{}", allowed_prefixes=(tmp_path,))
    assert stat.S_IMODE(target.stat().st_mode) & 0o077 == 0


def test_rejects_path_outside_allowlist(tmp_path: Path) -> None:
    outside = tmp_path / "config.json"
    with pytest.raises(ApplyError, match="not an allowed managed path"):
        atomic_write(outside, "{}", allowed_prefixes=(tmp_path / "nested",))


def test_rejects_traversal(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    with pytest.raises(ApplyError):
        atomic_write(nested / ".." / "escape.json", "{}", allowed_prefixes=(nested,))


def test_leaves_no_temp_file_behind_on_success(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    atomic_write(target, "{}", allowed_prefixes=(tmp_path,))
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/apply_test.py -v`
Expected: FAIL with `ImportError: cannot import name 'atomic_write'`

- [ ] **Step 3: Rewrite `src/holdfastctl/apply.py`**

Replace the entire file:

```python
"""
Atomic file replacement for Holdfast Control.

This module owns exactly one operation: replace a file's contents atomically,
preserving mode, without leaving a partial write behind. It has no knowledge of
configuration shape -- capability adapters own that.
"""

import os
import tempfile
from pathlib import Path

from .manifest_schema import validate_path_safety


class ApplyError(Exception):
    """Exception raised when a managed write fails or is refused."""


DEFAULT_MANAGED_PREFIXES: tuple[Path, ...] = (
    Path.home() / ".config" / "opencode",
    Path.home() / ".config" / "holdfast",
    Path.home() / ".claude" / "skills",
    Path.home() / ".agents" / "skills",
)


def _check_allowed(path: Path, allowed_prefixes: tuple[Path, ...]) -> Path:
    """Resolve path and confirm it sits under an allowed prefix. Fail closed."""
    try:
        validate_path_safety(str(path))
    except ValueError as e:
        raise ApplyError(str(e)) from e

    try:
        resolved = path.resolve()
    except (OSError, RuntimeError) as e:
        raise ApplyError(f"Path resolution failed, rejecting: {path}") from e

    for prefix in allowed_prefixes:
        try:
            resolved.relative_to(prefix.resolve())
        except ValueError:
            continue
        return resolved
    raise ApplyError(f"{path} is not an allowed managed path")


def atomic_write(
    path: Path,
    data: str,
    *,
    allowed_prefixes: tuple[Path, ...] = DEFAULT_MANAGED_PREFIXES,
) -> None:
    """Atomically replace path's contents with data.

    The temp file is created in path's own directory so os.replace is a true
    rename rather than a cross-filesystem copy. Mode is preserved when the file
    already exists, and defaults to 0600 when it does not.
    """
    target = _check_allowed(path, allowed_prefixes)

    mode = 0o600
    if target.exists():
        mode = os.stat(target).st_mode & 0o777

    fd, temp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, target)
    except Exception as e:
        temp_path.unlink(missing_ok=True)
        raise ApplyError(f"Failed to write {target}: {e!s}") from e
```

- [ ] **Step 4: Update exports in `src/holdfastctl/__init__.py`**

Replace the `from .apply import ...` line and its `__all__` entries:

```python
from .apply import DEFAULT_MANAGED_PREFIXES, ApplyError, atomic_write
```

In `__all__`, remove `'AtomicApplier'` and `'ConfigurationApplier'`; add `'atomic_write'` and `'DEFAULT_MANAGED_PREFIXES'`.

- [ ] **Step 5: Run tests and gates**

Run: `.venv/bin/python -m pytest tests/apply_test.py -v && .venv/bin/ruff check && .venv/bin/mypy -p holdfastctl -p server`
Expected: all apply tests PASS, ruff clean, mypy success.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: no failures. `ConfigurationApplier`/`AtomicApplier` are gone; if any other test imports them, update that test to the new API rather than restoring the old one.

- [ ] **Step 7: Commit**

```bash
git add src/holdfastctl/apply.py src/holdfastctl/__init__.py tests/apply_test.py
git commit -m "Replace apply.py whole-file overwrite with a correct atomic write

AtomicApplier.apply_configuration json.dumped its argument over the whole
target, and reconcile.py:334 passes a single provider entry as target_data
-- so applying an add-provider plan would have replaced all of
opencode.json with one provider dict.

NamedTemporaryFile was also called with no dir=, putting the temp file in
/tmp. On the pilot host /tmp and ~/.config/opencode are different
filesystems, so shutil.move was copy+unlink rather than a rename.

atomic_write creates its temp file in the destination directory, fsyncs,
and uses os.replace. Mode is preserved for existing files and defaults to
0600 for new ones. Paths must sit under an allowed managed prefix;
allowed_prefixes is injectable so tests can write under tmp_path."
```

---

### Task 2: Safe backups and a per-plan backup manifest

Removes the empty-backup landmine and gives `rollback` something deterministic to read.

**Files:**
- Modify: `src/holdfastctl/backup.py`
- Test: `tests/backup_test.py`

**Interfaces:**
- Produces:
  - `BackupManager.create_backup(source_file: Path) -> Path | None` (**changed**: returns `None` when source is missing)
  - `BackupManager.write_manifest(plan_id: str, entries: list[dict[str, str | int]]) -> Path`
  - `BackupManager.read_manifest(plan_id: str) -> list[dict[str, str | int]]`
  - Manifest entry shape: `{"target": str, "backup": str, "mode": int}`; `backup` is `""` when the target did not exist.

- [ ] **Step 1: Write the failing tests**

Append to `tests/backup_test.py`:

```python
def test_create_backup_returns_none_for_missing_source(tmp_path: Path) -> None:
    """Regression: an empty backup file restored over a live config blanks it."""
    manager = BackupManager(backup_dir=tmp_path / "backups")
    assert manager.create_backup(tmp_path / "absent.json") is None
    assert list((tmp_path / "backups").iterdir()) == []


def test_manifest_round_trip(tmp_path: Path) -> None:
    manager = BackupManager(backup_dir=tmp_path / "backups")
    entries = [{"target": "/home/u/.config/opencode/opencode.json", "backup": "/b/x.backup_1", "mode": 0o600}]
    manager.write_manifest("plan-123", entries)
    assert manager.read_manifest("plan-123") == entries


def test_read_manifest_missing_plan_returns_empty(tmp_path: Path) -> None:
    manager = BackupManager(backup_dir=tmp_path / "backups")
    assert manager.read_manifest("no-such-plan") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/backup_test.py -v`
Expected: FAIL — `create_backup` currently returns a `Path` to an empty file; `write_manifest` does not exist.

- [ ] **Step 3: Modify `create_backup` in `src/holdfastctl/backup.py`**

Replace the missing-source branch (currently creates an empty backup) so the method reads:

```python
    def create_backup(self, source_file: Path) -> Path | None:
        """Create a backup of a file.

        Returns the backup path, or None when the source does not exist --
        a file that is about to be created has nothing to restore.
        """
        try:
            if not os.path.exists(source_file):
                return None

            timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
            backup_filename = f"{source_file.name}.backup_{timestamp}"
            backup_path = self.backup_dir / backup_filename
            shutil.copy2(source_file, backup_path)
            return backup_path

        except Exception as e:  # noqa: BLE001 - wrap all backup failures into BackupError
            raise BackupError(f"Failed to create backup: {e!s}")
```

- [ ] **Step 4: Add manifest methods to `BackupManager`**

Add these methods and the `import json` at the top of the file:

```python
    def _manifest_path(self, plan_id: str) -> Path:
        return self.backup_dir / f"plan-{plan_id}.manifest.json"

    def write_manifest(self, plan_id: str, entries: list[dict[str, str | int]]) -> Path:
        """Record which files a plan backed up, so rollback is deterministic."""
        path = self._manifest_path(plan_id)
        path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        return path

    def read_manifest(self, plan_id: str) -> list[dict[str, str | int]]:
        """Return a plan's backup manifest, or an empty list if there is none."""
        path = self._manifest_path(plan_id)
        if not path.is_file():
            return []
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise BackupError(f"Backup manifest for {plan_id} is corrupt: {e!s}")
        if not isinstance(loaded, list):
            raise BackupError(f"Backup manifest for {plan_id} is not a list")
        return loaded
```

- [ ] **Step 5: Run tests and gates**

Run: `.venv/bin/python -m pytest tests/backup_test.py -v && .venv/bin/ruff check && .venv/bin/mypy -p holdfastctl -p server`
Expected: PASS. If an existing test asserted the empty-backup behavior, delete that assertion — it encoded the bug.

- [ ] **Step 6: Commit**

```bash
git add src/holdfastctl/backup.py tests/backup_test.py
git commit -m "Stop writing empty backups; add per-plan backup manifest

create_backup deliberately wrote an empty backup file when the source did
not exist, with a comment explaining it was for test environments.
Restoring one of those over a live config would blank it. It now returns
None -- a file about to be created has nothing to restore.

write_manifest/read_manifest record which files a plan backed up, so
rollback restores from an explicit list instead of inferring from backup
filename timestamps."
```

---

### Task 3: Device state fingerprint

`POST /plans` takes one `current_hash` for the whole device. This produces it, deterministically.

**Files:**
- Modify: `src/holdfastctl/capabilities.py`
- Test: `tests/capabilities_test.py`

**Interfaces:**
- Consumes: `load_current_opencode_state(config_dir: Path) -> dict[str, Any]` from `holdfastctl.reconcile`; `_sha256_hex(text: str) -> str` from `holdfastctl.capabilities`
- Produces:
  - `collect_device_state(manifest_path: Path, catalog_path: Path, *, opencode_config_dir: Path) -> dict[str, Any]`
  - `device_state_fingerprint(state: dict[str, Any]) -> str`

The fingerprint hashes the *probed current state*, not the derived actions — two different states can produce the same action list, and the spec requires that a changed state invalidate approval.

- [ ] **Step 1: Write the failing tests**

Append to `tests/capabilities_test.py`:

```python
def test_fingerprint_is_stable_across_repeated_inspections(tmp_path: Path) -> None:
    """Spec acceptance 1: repeated inspections hash identically."""
    from holdfastctl.capabilities import collect_device_state, device_state_fingerprint

    manifest, catalog, config_dir = _fingerprint_fixture(tmp_path)
    first = device_state_fingerprint(collect_device_state(manifest, catalog, opencode_config_dir=config_dir))
    second = device_state_fingerprint(collect_device_state(manifest, catalog, opencode_config_dir=config_dir))
    assert first == second


def test_fingerprint_changes_when_config_changes(tmp_path: Path) -> None:
    """Spec acceptance 2: changed local state must invalidate an approval."""
    from holdfastctl.capabilities import collect_device_state, device_state_fingerprint

    manifest, catalog, config_dir = _fingerprint_fixture(tmp_path)
    before = device_state_fingerprint(collect_device_state(manifest, catalog, opencode_config_dir=config_dir))

    (config_dir / "opencode.json").write_text(
        json.dumps({"provider": {"lemonade": {"options": {"baseURL": "http://changed:13305/v1"}}}}),
        encoding="utf-8",
    )
    after = device_state_fingerprint(collect_device_state(manifest, catalog, opencode_config_dir=config_dir))
    assert before != after


def _fingerprint_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Minimal manifest + catalog + opencode config dir for fingerprint tests."""
    manifest = tmp_path / "device.yaml"
    manifest.write_text(
        "device:\n"
        "  id: test-device\n"
        "  profile: linux\n"
        "capabilities:\n"
        "  opencode:\n"
        "    required: true\n"
        "    providers: [amd-halo]\n"
        "    mcp_servers: []\n"
        "credentials: []\n",
        encoding="utf-8",
    )
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(
        "providers:\n"
        "  - id: amd-halo\n"
        "    type: opencode\n"
        "    base_url: http://amd-halo.holdfast.lan:13305/api/v1\n"
        "mcp-servers: []\n",
        encoding="utf-8",
    )
    config_dir = tmp_path / "opencode"
    config_dir.mkdir()
    (config_dir / "opencode.json").write_text(
        json.dumps({"provider": {"lemonade": {"options": {"baseURL": "http://amd-halo.holdfast.lan:13305/api/v1"}}}}),
        encoding="utf-8",
    )
    return manifest, catalog, config_dir
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/capabilities_test.py -k fingerprint -v`
Expected: FAIL with `ImportError: cannot import name 'collect_device_state'`

- [ ] **Step 3: Add both functions to `src/holdfastctl/capabilities.py`**

Append to the module:

```python
def collect_device_state(
    manifest_path: Path,
    catalog_path: Path,
    *,
    opencode_config_dir: Path,
) -> dict[str, Any]:
    """Probe the current state of every capability the manifest declares.

    Returns a JSON-serializable snapshot. This is the input to
    device_state_fingerprint, and is what an approval is bound to.
    """
    import yaml

    from holdfastctl.reconcile import load_current_opencode_state

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    capabilities = manifest.get("capabilities", {}) or {}

    state: dict[str, Any] = {
        "device_id": (manifest.get("device") or {}).get("id", "unknown"),
        "manifest_commit": _sha256_hex(manifest_path.read_text(encoding="utf-8")),
        "capabilities": {},
    }

    if "opencode" in capabilities:
        current = load_current_opencode_state(opencode_config_dir)
        state["capabilities"]["opencode"] = {
            "providers": current["providers"],
            "mcp_servers": current["mcp_servers"],
            "plugins": current["plugins"],
            "env_refs": sorted(current["env_refs"]),
        }

    return state


def device_state_fingerprint(state: dict[str, Any]) -> str:
    """Hash a device state snapshot deterministically."""
    return _sha256_hex(json.dumps(state, sort_keys=True, default=str))
```

`json` and `Path` are already imported at the top of `capabilities.py`.

Note: `collect_device_state` deliberately probes only `opencode`. `network` and `gateway_access` reconcile against live reachability, which is not stable enough to bind an approval to. Later phases may add them.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/capabilities_test.py -k fingerprint -v`
Expected: PASS

- [ ] **Step 5: Run gates and commit**

```bash
.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check && .venv/bin/mypy -p holdfastctl -p server
git add src/holdfastctl/capabilities.py tests/capabilities_test.py
git commit -m "Add device state fingerprint for plan approval binding

POST /plans stores one current_hash for the whole device, so the agent
needs a single deterministic fingerprint of probed state.

collect_device_state snapshots what each declared capability currently
looks like; device_state_fingerprint hashes it with sorted keys. The hash
covers probed state rather than derived actions, because two different
states can yield the same action list and the spec requires a changed
state to invalidate an approval.

Only opencode is probed. network and gateway_access reconcile against
live reachability, which is too unstable to bind an approval to."
```

---

### Task 4: Device-token plan submit and read

The agent cannot currently submit its own plan (`POST /plans` is `require_admin`) or learn that one was approved.

**Files:**
- Modify: `server/api/__init__.py:92-110`
- Test: `tests/plan_endpoints_test.py` (create)

**Interfaces:**
- Consumes: `auth.authorization_token(authorization: str | None) -> str`, `auth.authorize_report(connection: sqlite3.Connection, raw_token: str, device_id: str) -> None`
- Produces:
  - `POST /api/v1/devices/{device_id}/plans` now accepts admin **or** that device's report token
  - `GET /api/v1/devices/{device_id}/plans/{plan_id}` returns `{approval_status, current_hash, desired_commit, expiry_timestamp}` for a device token

- [ ] **Step 1: Write the failing tests**

Create `tests/plan_endpoints_test.py`. The repo has no `conftest.py`; `tests/test_control_plane_security.py` defines `make_client`, `admin_headers`, and `enroll` as module-level helpers. Copy that trio rather than importing across test modules.

```python
"""
Tests for device-token access to plans.

A device must be able to submit a fingerprint about itself and read whether
its plan was approved. It must never be able to approve, nor to touch
another device's plans.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from server import create_app

ADMIN_TOKEN = "test-admin-token"


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(str(tmp_path / "control-plane.sqlite"), admin_token=ADMIN_TOKEN))


def admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def enroll(client: TestClient, device_id: str) -> str:
    """Enroll a device and return its raw report token."""
    code = client.post(
        "/api/v1/enrollment-codes",
        json={"device_id": device_id, "expires_in_seconds": 600},
        headers=admin_headers(),
    ).json()["code"]
    response = client.post("/api/v1/enroll", json={"code": code, "device_id": device_id})
    assert response.status_code == 200
    return str(response.json()["report_token"])


def device_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_device_token_can_create_plan_for_itself(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    token = enroll(client, "device-a")
    response = client.post(
        "/api/v1/devices/device-a/plans",
        json={"desired_commit": "abc", "current_hash": "def", "expiry_hours": 24},
        headers=device_headers(token),
    )
    assert response.status_code == 200
    assert response.json()["approval_status"] == "pending"


def test_device_token_cannot_create_plan_for_another_device(tmp_path: Path) -> None:
    """Spec acceptance 10."""
    client = make_client(tmp_path)
    token = enroll(client, "device-a")
    response = client.post(
        "/api/v1/devices/device-b/plans",
        json={"desired_commit": "abc", "current_hash": "def"},
        headers=device_headers(token),
    )
    assert response.status_code == 403


def test_device_token_can_read_own_plan(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    token = enroll(client, "device-a")
    created = client.post(
        "/api/v1/devices/device-a/plans",
        json={"desired_commit": "abc", "current_hash": "def"},
        headers=device_headers(token),
    ).json()

    response = client.get(
        f"/api/v1/devices/device-a/plans/{created['id']}",
        headers=device_headers(token),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["approval_status"] == "pending"
    assert body["current_hash"] == "def"
    assert body["desired_commit"] == "abc"
    assert set(body) == {"approval_status", "current_hash", "desired_commit", "expiry_timestamp"}


def test_device_token_cannot_read_another_devices_plan(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    token = enroll(client, "device-a")
    created = client.post(
        "/api/v1/devices/device-b/plans",
        json={"desired_commit": "abc", "current_hash": "def"},
        headers=admin_headers(),
    ).json()

    response = client.get(
        f"/api/v1/devices/device-b/plans/{created['id']}",
        headers=device_headers(token),
    )
    assert response.status_code == 403


def test_device_token_cannot_approve(tmp_path: Path) -> None:
    """Spec acceptance 7: a report token cannot approve a plan."""
    client = make_client(tmp_path)
    token = enroll(client, "device-a")
    created = client.post(
        "/api/v1/devices/device-a/plans",
        json={"desired_commit": "abc", "current_hash": "def"},
        headers=device_headers(token),
    ).json()

    response = client.post(
        f"/api/v1/devices/device-a/plans/{created['id']}/approve",
        json={"current_hash": "def", "desired_commit": "abc"},
        headers=device_headers(token),
    )
    assert response.status_code in (401, 403)


def test_admin_token_still_creates_plans(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    response = client.post(
        "/api/v1/devices/device-a/plans",
        json={"desired_commit": "abc", "current_hash": "def"},
        headers=admin_headers(),
    )
    assert response.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/plan_endpoints_test.py -v`
Expected: FAIL — device-token creation returns 401/403, and the GET route does not exist.

- [ ] **Step 3: Relax plan creation in `server/api/__init__.py`**

Add this helper next to `require_admin` (around line 50):

```python
    def require_admin_or_device(authorization: str | None, device_id: str) -> None:
        """Accept the admin token, or that device's own report token."""
        try:
            require_admin(authorization)
            return
        except HTTPException:
            pass
        raw_token = auth.authorization_token(authorization)
        with connection(database_path) as conn:
            auth.authorize_report(conn, raw_token, device_id)
```

Change the first line of `create_plan` from `require_admin(authorization)` to:

```python
        require_admin_or_device(authorization, device_id)
```

- [ ] **Step 4: Add the plan read endpoint**

Insert after `list_plans` (currently ending at line 110):

```python
    @router.get("/api/v1/devices/{device_id}/plans/{plan_id}")
    def get_plan(device_id: str, plan_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin_or_device(authorization, device_id)
        with connection(database_path) as conn:
            plan = conn.execute(
                "SELECT approval_status, current_hash, desired_commit, expiry_timestamp FROM plans WHERE id = ? AND device_id = ?",
                (plan_id, device_id),
            ).fetchone()
        if plan is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
        return dict(plan)
```

- [ ] **Step 5: Run tests and gates**

Run: `.venv/bin/python -m pytest tests/plan_endpoints_test.py -v && .venv/bin/ruff check && .venv/bin/mypy -p holdfastctl -p server`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add server/api/__init__.py tests/plan_endpoints_test.py
git commit -m "Allow a device token to submit and read its own plans

POST /plans was require_admin, so an agent could not submit a plan about
itself -- the loop could never start without an admin credential on the
workstation, which the threat model rules out. All plan endpoints were
admin-only, so an agent also had no way to learn its plan was approved.

Both now accept the admin token or that device's own report token, reusing
auth.authorize_report rather than introducing a new mechanism. The new GET
returns only approval status and the binding fields. approve and the plan
list stay admin-only, so the report token still cannot approve, cannot
reach another device's plans, and cannot retrieve secrets."
```

---

### Task 5: Plan submit and fetch in the reporting client

**Files:**
- Modify: `src/holdfastctl/reporting.py`
- Test: `tests/reporting_test.py`

**Interfaces:**
- Consumes: `StatusReporter.__init__(control_plane_url: str, device_id: str, token_path: str | None = None)`; `StatusReporter._load_or_create_token() -> str`
- Produces:
  - `StatusReporter.create_plan(desired_commit: str, current_hash: str, expiry_hours: int = 24) -> dict[str, Any]`
  - `StatusReporter.get_plan(plan_id: str) -> dict[str, Any]`
  - `StatusReporter.approve_plan(plan_id: str, current_hash: str, desired_commit: str, admin_token: str) -> dict[str, Any]`

Both device-token methods raise `ReportingError` on non-2xx. `approve_plan` sends the admin token instead of the device token.

- [ ] **Step 1: Write the failing tests**

Append to `tests/reporting_test.py`, following the existing mocking style in that file:

```python
def test_create_plan_posts_fingerprint(tmp_path, monkeypatch):
    from holdfastctl.reporting import StatusReporter

    token_file = tmp_path / "report.token"
    token_file.write_text("tok")
    reporter = StatusReporter("http://cp:8000", "dev-1", token_path=str(token_file))

    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"id": "plan-1", "approval_status": "pending"}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr("requests.post", fake_post)

    result = reporter.create_plan("commit-abc", "hash-def")
    assert result["id"] == "plan-1"
    assert captured["url"] == "http://cp:8000/api/v1/devices/dev-1/plans"
    assert captured["json"] == {"desired_commit": "commit-abc", "current_hash": "hash-def", "expiry_hours": 24}
    assert captured["headers"]["Authorization"] == "Bearer tok"


def test_get_plan_reads_approval_status(tmp_path, monkeypatch):
    from holdfastctl.reporting import StatusReporter

    token_file = tmp_path / "report.token"
    token_file.write_text("tok")
    reporter = StatusReporter("http://cp:8000", "dev-1", token_path=str(token_file))

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"approval_status": "approved", "current_hash": "h", "desired_commit": "c", "expiry_timestamp": 1.0}

    monkeypatch.setattr("requests.get", lambda url, headers=None, timeout=None: FakeResponse())

    assert reporter.get_plan("plan-1")["approval_status"] == "approved"


def test_get_plan_raises_on_error_status(tmp_path, monkeypatch):
    from holdfastctl.reporting import ReportingError, StatusReporter

    token_file = tmp_path / "report.token"
    token_file.write_text("tok")
    reporter = StatusReporter("http://cp:8000", "dev-1", token_path=str(token_file))

    class FakeResponse:
        status_code = 404
        text = "Plan not found"

    monkeypatch.setattr("requests.get", lambda url, headers=None, timeout=None: FakeResponse())

    with pytest.raises(ReportingError, match="404"):
        reporter.get_plan("missing")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/reporting_test.py -k plan -v`
Expected: FAIL with `AttributeError: 'StatusReporter' object has no attribute 'create_plan'`

- [ ] **Step 3: Add the three methods to `StatusReporter`**

```python
    def create_plan(self, desired_commit: str, current_hash: str, expiry_hours: int = 24) -> dict[str, Any]:
        """Submit a plan fingerprint. Returns the created plan including its id."""
        import requests

        token = self._load_or_create_token()
        response = requests.post(
            f"{self.control_plane_url}/api/v1/devices/{self.device_id}/plans",
            json={"desired_commit": desired_commit, "current_hash": current_hash, "expiry_hours": expiry_hours},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if response.status_code // 100 != 2:
            raise ReportingError(f"Plan creation failed with {response.status_code}: {getattr(response, 'text', '')}")
        return dict(response.json())

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        """Read a plan's approval status and binding fields."""
        import requests

        token = self._load_or_create_token()
        response = requests.get(
            f"{self.control_plane_url}/api/v1/devices/{self.device_id}/plans/{plan_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if response.status_code // 100 != 2:
            raise ReportingError(f"Plan fetch failed with {response.status_code}: {getattr(response, 'text', '')}")
        return dict(response.json())

    def approve_plan(self, plan_id: str, current_hash: str, desired_commit: str, admin_token: str) -> dict[str, Any]:
        """Approve a plan. Requires the admin token; never called on a managed device."""
        import requests

        response = requests.post(
            f"{self.control_plane_url}/api/v1/devices/{self.device_id}/plans/{plan_id}/approve",
            json={"current_hash": current_hash, "desired_commit": desired_commit},
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=10,
        )
        if response.status_code // 100 != 2:
            raise ReportingError(f"Plan approval failed with {response.status_code}: {getattr(response, 'text', '')}")
        return dict(response.json())
```

- [ ] **Step 4: Run tests and gates, then commit**

```bash
.venv/bin/python -m pytest tests/reporting_test.py -v && .venv/bin/ruff check && .venv/bin/mypy -p holdfastctl -p server
git add src/holdfastctl/reporting.py tests/reporting_test.py
git commit -m "Add plan submit, fetch, and approve to the reporting client

create_plan and get_plan use the device's report token. approve_plan takes
an explicit admin token and is only ever called from an operator machine,
so the admin credential never has to live on a managed device."
```

---

### Task 6: OpenCode adapter gains apply()

The merge that preserves every key the manifest does not describe.

**Files:**
- Modify: `src/holdfastctl/capabilities.py` (`OpencodeAdapter`, line 96)
- Test: `tests/capabilities_test.py`

**Interfaces:**
- Consumes: `atomic_write` and `ApplyError` from `holdfastctl.apply`; `BackupManager` from `holdfastctl.backup`; `ConfigurationPlan` from `holdfastctl.reconcile`
- Produces: `OpencodeAdapter.apply(plan: ConfigurationPlan, context: ReconcileContext, *, backup_manager: BackupManager, allowed_prefixes: tuple[Path, ...] | None = None) -> dict[str, str | int]` — returns one backup-manifest entry

- [ ] **Step 1: Write the failing tests**

Append to `tests/capabilities_test.py`:

```python
def test_apply_add_provider_preserves_unmanaged_keys(tmp_path: Path) -> None:
    """Spec acceptance 8: plugin/agent/lsp/permission must survive an apply."""
    from holdfastctl.backup import BackupManager
    from holdfastctl.capabilities import OpencodeAdapter
    from holdfastctl.reconcile import ConfigurationPlan

    config_dir = tmp_path / "opencode"
    config_dir.mkdir()
    original = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {"existing": {"options": {"baseURL": "http://keep"}}},
        "plugin": ["oh-my-opencode-slim"],
        "agent": {"explore": {"disable": False}},
        "lsp": True,
        "permission": {"edit": "allow"},
    }
    (config_dir / "opencode.json").write_text(json.dumps(original), encoding="utf-8")

    plan = ConfigurationPlan(
        plan_id="p1", device_id="d1", desired_commit="c", current_hash="h",
        expiry_timestamp=0.0, action="add", target="provider:amd-halo",
        source="desired", description="", checksum="",
        target_data={"id": "amd-halo", "base_url": "http://amd-halo:13305/api/v1"},
    )
    context = make_context(tmp_path)
    context.opencode_config_dir = config_dir

    OpencodeAdapter().apply(
        plan, context,
        backup_manager=BackupManager(backup_dir=tmp_path / "backups"),
        allowed_prefixes=(config_dir,),
    )

    result = json.loads((config_dir / "opencode.json").read_text())
    assert result["plugin"] == ["oh-my-opencode-slim"]
    assert result["agent"] == {"explore": {"disable": False}}
    assert result["lsp"] is True
    assert result["permission"] == {"edit": "allow"}
    assert result["$schema"] == "https://opencode.ai/config.json"
    assert result["provider"]["existing"]["options"]["baseURL"] == "http://keep"
    assert result["provider"]["amd-halo"]["options"]["baseURL"] == "http://amd-halo:13305/api/v1"


def test_apply_returns_backup_manifest_entry(tmp_path: Path) -> None:
    from holdfastctl.backup import BackupManager
    from holdfastctl.capabilities import OpencodeAdapter
    from holdfastctl.reconcile import ConfigurationPlan

    config_dir = tmp_path / "opencode"
    config_dir.mkdir()
    (config_dir / "opencode.json").write_text(json.dumps({"provider": {}}), encoding="utf-8")

    plan = ConfigurationPlan(
        plan_id="p1", device_id="d1", desired_commit="c", current_hash="h",
        expiry_timestamp=0.0, action="add", target="provider:amd-halo",
        source="desired", description="", checksum="",
        target_data={"id": "amd-halo", "base_url": "http://x"},
    )
    context = make_context(tmp_path)
    context.opencode_config_dir = config_dir

    entry = OpencodeAdapter().apply(
        plan, context,
        backup_manager=BackupManager(backup_dir=tmp_path / "backups"),
        allowed_prefixes=(config_dir,),
    )
    assert entry["target"] == str(config_dir / "opencode.json")
    assert Path(str(entry["backup"])).is_file()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/capabilities_test.py -k apply -v`
Expected: FAIL with `AttributeError: 'OpencodeAdapter' object has no attribute 'apply'`

- [ ] **Step 3: Add `apply()` to `OpencodeAdapter`**

```python
    def apply(
        self,
        plan: ConfigurationPlan,
        context: ReconcileContext,
        *,
        backup_manager: "BackupManager",
        allowed_prefixes: tuple[Path, ...] | None = None,
    ) -> dict[str, str | int]:
        """Merge one plan action into opencode.json, preserving everything else.

        Returns a backup-manifest entry: {"target", "backup", "mode"}.
        """
        import json as _json

        from holdfastctl.apply import DEFAULT_MANAGED_PREFIXES, ApplyError, atomic_write

        config_file = context.opencode_config_dir / "opencode.json"
        prefixes = allowed_prefixes if allowed_prefixes is not None else DEFAULT_MANAGED_PREFIXES

        config: dict[str, Any] = {}
        if config_file.is_file():
            config = _json.loads(config_file.read_text(encoding="utf-8"))

        kind, _, name = plan.target.partition(":")
        data = plan.target_data or {}

        if kind == "provider":
            providers = config.setdefault("provider", {})
            entry = providers.setdefault(name, {})
            options = entry.setdefault("options", {})
            if data.get("base_url"):
                options["baseURL"] = data["base_url"]
            entry.setdefault("name", data.get("name", name))
        elif kind == "mcp_server":
            servers = config.setdefault("mcp", {})
            entry = servers.setdefault(name, {})
            if data.get("url"):
                entry["url"] = data["url"]
            entry.setdefault("type", "remote")
            entry.setdefault("enabled", True)
        else:
            raise ApplyError(f"OpencodeAdapter cannot apply target '{plan.target}'")

        mode = config_file.stat().st_mode & 0o777 if config_file.exists() else 0o600
        backup_path = backup_manager.create_backup(config_file)
        atomic_write(config_file, _json.dumps(config, indent=2) + "\n", allowed_prefixes=prefixes)

        return {"target": str(config_file), "backup": str(backup_path) if backup_path else "", "mode": mode}
```

`capabilities.py:18` currently reads `from typing import Any`. Change it to `from typing import TYPE_CHECKING, Any` and add below the existing imports:

```python
if TYPE_CHECKING:
    from holdfastctl.backup import BackupManager
```

This keeps the `BackupManager` annotation resolvable without a runtime import cycle (`backup.py` is imported by `apply.py`, which `capabilities.apply` imports lazily).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/capabilities_test.py -v`
Expected: PASS

- [ ] **Step 5: Run gates and commit**

```bash
.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check && .venv/bin/mypy -p holdfastctl -p server
git add src/holdfastctl/capabilities.py tests/capabilities_test.py
git commit -m "Give OpencodeAdapter an apply() that merges instead of overwriting

Each adapter now owns how to mutate its own config, following the
pluggable pattern e2be4ee established for reconcile(). The opencode
adapter parses the current opencode.json, merges only the provider or MCP
entry the plan names, and writes back through atomic_write.

Every key the manifest does not describe is preserved -- plugin, agent,
lsp, permission, \$schema -- as the unmanaged-by-design list in
docs/drift-baseline-jarod7736-laptop.md requires. apply.py keeps no
knowledge of configuration shape."
```

---

### Task 7: CLI — inspect, plan, show-plan

**Files:**
- Modify: `src/holdfastctl/cli.py` (rename `reconcile` at line 352; add two commands)
- Test: `tests/cli_test.py`

**Interfaces:**
- Consumes: `collect_device_state`, `device_state_fingerprint`, `reconcile_device` from `holdfastctl.capabilities`; `StatusReporter.create_plan`/`get_plan`
- Produces:
  - `plan` command — prints actions, then `Plan <id> submitted` unless `--local`
  - `show-plan <plan_id>` command
  - `inspect` command
  - Helper `_load_agent_config(config_path: Path) -> dict[str, Any]` extracted from the existing `report` command body (lines 265–275), reused by every new command

- [ ] **Step 1: Write the failing tests**

Append to `tests/cli_test.py`, following the existing `CliRunner` usage in that file:

```python
def test_plan_local_prints_actions_without_submitting(tmp_path, monkeypatch):
    """--local preserves the old reconcile behavior: print, never contact the server."""
    from typer.testing import CliRunner

    from holdfastctl.cli import app

    def explode(*args, **kwargs):
        raise AssertionError("--local must not contact the control plane")

    monkeypatch.setattr("requests.post", explode)

    manifest, catalog, config_dir = _cli_plan_fixture(tmp_path)
    result = CliRunner().invoke(
        app,
        ["plan", "--local", "-m", str(manifest), "--catalog", str(catalog), "--config-dir", str(config_dir)],
    )
    assert result.exit_code == 0
    assert "opencode" in result.stdout


def test_inspect_prints_device_id(tmp_path):
    from typer.testing import CliRunner

    from holdfastctl.cli import app

    result = CliRunner().invoke(app, ["inspect"])
    assert result.exit_code == 0


def test_reconcile_command_is_gone(tmp_path):
    from typer.testing import CliRunner

    from holdfastctl.cli import app

    result = CliRunner().invoke(app, ["reconcile", "--help"])
    assert result.exit_code != 0
```

Reuse `_fingerprint_fixture` from Task 3 by copying it into `tests/cli_test.py` as `_cli_plan_fixture` — tests should not import helpers across test modules.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/cli_test.py -k "plan or inspect or reconcile_command" -v`
Expected: FAIL — `plan` and `inspect` are unknown commands; `reconcile` still exists.

- [ ] **Step 3: Extract the config loader**

Add above the `report` command in `cli.py`:

```python
def _load_agent_config(config_path: Path) -> dict[str, Any]:
    """Read the agent config, exiting cleanly if it is absent or unreadable."""
    import yaml

    if not config_path.exists():
        typer.echo(f"Error: agent config not found at {config_path}", err=True)
        raise typer.Exit(code=1)
    try:
        with open(config_path) as f:
            return dict(yaml.safe_load(f) or {})
    except Exception as e:  # noqa: BLE001 - surface any config read error to the user
        typer.echo(f"Error: failed to read agent config: {e}", err=True)
        raise typer.Exit(code=1)
```

Replace lines 265–275 of the existing `report` command with `config = _load_agent_config(config_path)`.

- [ ] **Step 4: Rename `reconcile` to `plan` and add submission**

Change the decorator and signature at line 352 from `@app.command()` / `def reconcile(` to `@app.command()` / `def plan(`, then add two options to its signature:

```python
    local: bool = typer.Option(False, "--local", help="Print the plan without submitting it to the control plane"),
    config_path: Path = typer.Option(  # noqa: B008 - idiomatic typer default
        Path.home() / ".config" / "holdfastctl" / "config.yaml",  # noqa: B008 - idiomatic typer default
        "--config",
        "-c",
        help="Agent config file",
    ),
```

After the existing action-printing loop and before the final summary, add:

```python
    if local:
        typer.echo("\nLocal plan only; not submitted.")
        return

    from holdfastctl.capabilities import collect_device_state, device_state_fingerprint
    from holdfastctl.reporting import ReportingError, StatusReporter

    config = _load_agent_config(config_path)
    device_id = _resolve_device_id(config)
    control_plane_url = config.get("control_plane_url", "http://127.0.0.1:8000")
    token_path = config.get("token_path") or str(config_path.parent / "report.token")

    state = collect_device_state(manifest_path, catalog_path, opencode_config_dir=config_dir)
    current_hash = device_state_fingerprint(state)
    desired_commit = state["manifest_commit"]

    reporter = StatusReporter(control_plane_url, device_id, token_path=token_path)
    try:
        created = reporter.create_plan(desired_commit, current_hash)
    except ReportingError as e:
        typer.echo(f"Error: plan submission to {control_plane_url} failed: {e}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"\nPlan {created['id']} submitted ({created['approval_status']}).")
    typer.echo(f"Approve with: holdfastctl approve {created['id']}")
```

- [ ] **Step 5: Add `show-plan` and `inspect`**

```python
@app.command("show-plan")
def show_plan(
    plan_id: str = typer.Argument(..., help="Plan id returned by `holdfastctl plan`"),
    config_path: Path = typer.Option(  # noqa: B008 - idiomatic typer default
        Path.home() / ".config" / "holdfastctl" / "config.yaml",  # noqa: B008 - idiomatic typer default
        "--config",
        "-c",
        help="Agent config file",
    ),
) -> None:
    """Show a plan's approval status and binding fields."""
    from holdfastctl.reporting import ReportingError, StatusReporter

    config = _load_agent_config(config_path)
    device_id = _resolve_device_id(config)
    control_plane_url = config.get("control_plane_url", "http://127.0.0.1:8000")
    token_path = config.get("token_path") or str(config_path.parent / "report.token")

    reporter = StatusReporter(control_plane_url, device_id, token_path=token_path)
    try:
        plan_data = reporter.get_plan(plan_id)
    except ReportingError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    for key in ("approval_status", "current_hash", "desired_commit", "expiry_timestamp"):
        typer.echo(f"{key}: {plan_data.get(key)}")


@app.command()
def inspect(
    config_path: Path = typer.Option(  # noqa: B008 - idiomatic typer default
        Path.home() / ".config" / "holdfastctl" / "config.yaml",  # noqa: B008 - idiomatic typer default
        "--config",
        "-c",
        help="Agent config file",
    ),
) -> None:
    """Print a local state snapshot without contacting the control plane."""
    import json as _json

    from holdfastctl.inspect import DeviceInspector
    from holdfastctl.manifest_schema import DeviceInfo

    config = _load_agent_config(config_path) if config_path.exists() else {}
    device_id = _resolve_device_id(config) if config else "unknown"
    device_info = DeviceInfo(id=device_id, profile=config.get("profile", "linux"), display_name=device_id)
    typer.echo(_json.dumps(DeviceInspector(device_info).inspect_system(), indent=2, default=str))
```

- [ ] **Step 6: Run tests and gates**

Run: `.venv/bin/python -m pytest tests/cli_test.py -v && .venv/bin/ruff check && .venv/bin/mypy -p holdfastctl -p server`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/holdfastctl/cli.py tests/cli_test.py
git commit -m "Rename reconcile to plan; add show-plan and inspect

The spec names nine commands and the CLI shipped five. plan submits a
state fingerprint to the control plane and prints the plan id to approve;
--local preserves the old print-only behavior. show-plan reads approval
status. inspect exposes the existing inspect.py module, which had no
command.

Renaming reconcile is safe: the systemd unit runs only report, and
fleet_check.sh shells out only to systemctl."
```

---

### Task 8: CLI — approve, apply, rollback

The gate. `apply` must refuse on any of four conditions.

**Files:**
- Modify: `src/holdfastctl/cli.py`
- Test: `tests/apply_gate_test.py` (create)

**Interfaces:**
- Consumes: everything produced by Tasks 1–7
- Produces: `approve`, `apply`, `rollback` commands

- [ ] **Step 1: Write the failing tests**

Create `tests/apply_gate_test.py`:

```python
"""
Tests for the apply refusal gate.

apply must refuse unless the plan is approved, unexpired, and bound to a
hash that still matches freshly probed state.
"""

import pytest

from holdfastctl.cli import _verify_plan_applicable


def test_refuses_unapproved_plan():
    with pytest.raises(ValueError, match="not approved"):
        _verify_plan_applicable(
            {"approval_status": "pending", "current_hash": "h", "desired_commit": "c", "expiry_timestamp": 9e12},
            current_hash="h",
            desired_commit="c",
            now=0.0,
        )


def test_refuses_when_state_changed():
    """Spec acceptance 2."""
    with pytest.raises(ValueError, match="state has changed"):
        _verify_plan_applicable(
            {"approval_status": "approved", "current_hash": "old", "desired_commit": "c", "expiry_timestamp": 9e12},
            current_hash="new",
            desired_commit="c",
            now=0.0,
        )


def test_refuses_when_desired_commit_changed():
    with pytest.raises(ValueError, match="manifest has changed"):
        _verify_plan_applicable(
            {"approval_status": "approved", "current_hash": "h", "desired_commit": "old", "expiry_timestamp": 9e12},
            current_hash="h",
            desired_commit="new",
            now=0.0,
        )


def test_refuses_expired_plan():
    with pytest.raises(ValueError, match="expired"):
        _verify_plan_applicable(
            {"approval_status": "approved", "current_hash": "h", "desired_commit": "c", "expiry_timestamp": 100.0},
            current_hash="h",
            desired_commit="c",
            now=200.0,
        )


def test_accepts_fully_valid_plan():
    _verify_plan_applicable(
        {"approval_status": "approved", "current_hash": "h", "desired_commit": "c", "expiry_timestamp": 9e12},
        current_hash="h",
        desired_commit="c",
        now=0.0,
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/apply_gate_test.py -v`
Expected: FAIL with `ImportError: cannot import name '_verify_plan_applicable'`

- [ ] **Step 3: Add the gate helper to `cli.py`**

```python
def _verify_plan_applicable(
    plan_data: dict[str, Any],
    *,
    current_hash: str,
    desired_commit: str,
    now: float,
) -> None:
    """Raise ValueError unless an approved plan still matches current state."""
    if plan_data.get("approval_status") != "approved":
        raise ValueError(f"Plan is not approved (status: {plan_data.get('approval_status')})")
    if float(plan_data.get("expiry_timestamp", 0)) <= now:
        raise ValueError("Plan has expired; run `holdfastctl plan` again")
    if plan_data.get("current_hash") != current_hash:
        raise ValueError("Local state has changed since approval; run `holdfastctl plan` again")
    if plan_data.get("desired_commit") != desired_commit:
        raise ValueError("Device manifest has changed since approval; run `holdfastctl plan` again")
```

- [ ] **Step 4: Run gate tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/apply_gate_test.py -v`
Expected: PASS

- [ ] **Step 5: Add the three commands to `cli.py`**

```python
@app.command()
def approve(
    plan_id: str = typer.Argument(..., help="Plan id to approve"),
    device_id_opt: str = typer.Option(..., "--device", "-d", help="Device the plan belongs to"),
    control_plane: str = typer.Option(..., "--control-plane", help="Control plane base URL"),
) -> None:
    """Approve a plan. Operator command: requires HOLDFAST_ADMIN_TOKEN."""
    import os

    from holdfastctl.reporting import ReportingError, StatusReporter

    admin_token = os.environ.get("HOLDFAST_ADMIN_TOKEN")
    if not admin_token:
        typer.echo("Error: HOLDFAST_ADMIN_TOKEN is not set", err=True)
        raise typer.Exit(code=1)

    reporter = StatusReporter(control_plane, device_id_opt)
    try:
        current = reporter.get_plan(plan_id)
    except ReportingError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    try:
        reporter.approve_plan(plan_id, current["current_hash"], current["desired_commit"], admin_token)
    except ReportingError as e:
        typer.echo(f"Error: approval failed: {e}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Plan {plan_id} approved.")


@app.command()
def apply(
    plan_id: str = typer.Argument(..., help="Approved plan id"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print what would change and exit without writing"),
    manifest_path: Path = typer.Option(  # noqa: B008 - idiomatic typer default
        Path("manifests/devices/jarod7736-laptop.yaml"), "--manifest", "-m", help="Device manifest YAML"
    ),
    catalog_path: Path = typer.Option(  # noqa: B008 - idiomatic typer default
        Path("manifests/catalogs/credentials.yaml"), "--catalog", help="Credential catalog YAML"
    ),
    config_dir: Path = typer.Option(  # noqa: B008 - idiomatic typer default
        Path.home() / ".config" / "opencode",  # noqa: B008 - idiomatic typer default
        "--config-dir",
        help="OpenCode config directory",
    ),
    config_path: Path = typer.Option(  # noqa: B008 - idiomatic typer default
        Path.home() / ".config" / "holdfastctl" / "config.yaml",  # noqa: B008 - idiomatic typer default
        "--config",
        "-c",
        help="Agent config file",
    ),
) -> None:
    """Apply an approved plan after re-verifying that local state still matches."""
    import time

    from holdfastctl.backup import BackupManager
    from holdfastctl.capabilities import (
        ADAPTERS,
        collect_device_state,
        device_state_fingerprint,
        reconcile_device,
    )
    from holdfastctl.reporting import ReportingError, StatusReporter

    config = _load_agent_config(config_path)
    device_id = _resolve_device_id(config)
    control_plane_url = config.get("control_plane_url", "http://127.0.0.1:8000")
    token_path = config.get("token_path") or str(config_path.parent / "report.token")

    state = collect_device_state(manifest_path, catalog_path, opencode_config_dir=config_dir)
    current_hash = device_state_fingerprint(state)
    desired_commit = state["manifest_commit"]

    reporter = StatusReporter(control_plane_url, device_id, token_path=token_path)
    try:
        plan_data = reporter.get_plan(plan_id)
    except ReportingError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    try:
        _verify_plan_applicable(plan_data, current_hash=current_hash, desired_commit=desired_commit, now=time.time())
    except ValueError as e:
        typer.echo(f"Error: refusing to apply. {e}", err=True)
        raise typer.Exit(code=1)

    results = reconcile_device(manifest_path, catalog_path, opencode_config_dir=config_dir)

    if dry_run:
        for capability, plans in results.items():
            for p in plans:
                typer.echo(f"would {p.action} {p.target} ({capability})")
        typer.echo("\nDry run; nothing written.")
        return

    backup_manager = BackupManager()
    entries: list[dict[str, str | int]] = []
    try:
        for capability, plans in results.items():
            adapter = ADAPTERS[capability]
            applier = getattr(adapter, "apply", None)
            if applier is None:
                continue
            for p in plans:
                context = _reconcile_context_for(manifest_path, catalog_path, config_dir)
                entries.append(applier(p, context, backup_manager=backup_manager))
                typer.echo(f"{p.action} {p.target}")
    except Exception as e:  # noqa: BLE001 - any apply failure must roll back
        typer.echo(f"Error: apply failed: {e}; restoring", err=True)
        for entry in entries:
            if entry["backup"]:
                backup_manager.restore_from_backup(Path(str(entry["target"])), Path(str(entry["backup"])))
        raise typer.Exit(code=1)

    backup_manager.write_manifest(plan_id, entries)
    typer.echo(f"\nApplied plan {plan_id}. Roll back with: holdfastctl rollback {plan_id}")


@app.command()
def rollback(
    plan_id: str = typer.Argument(..., help="Plan id to roll back"),
) -> None:
    """Restore every file a plan backed up."""
    from holdfastctl.backup import BackupManager

    backup_manager = BackupManager()
    entries = backup_manager.read_manifest(plan_id)
    if not entries:
        typer.echo(f"Error: no backup manifest for plan {plan_id}", err=True)
        raise typer.Exit(code=1)

    for entry in entries:
        target = Path(str(entry["target"]))
        backup = str(entry["backup"])
        if not backup:
            target.unlink(missing_ok=True)
            typer.echo(f"removed {target} (did not exist before apply)")
            continue
        backup_manager.restore_from_backup(target, Path(backup))
        os.chmod(target, int(entry["mode"]))
        typer.echo(f"restored {target}")

    typer.echo(f"\nRolled back plan {plan_id}.")
```

Add `import os` at the top of `cli.py` if absent, and add this helper next to `_load_agent_config`:

```python
def _reconcile_context_for(manifest_path: Path, catalog_path: Path, config_dir: Path) -> Any:
    """Build a ReconcileContext matching what reconcile_device uses internally."""
    import yaml

    from holdfastctl.capabilities import ReconcileContext, _sha256_hex

    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest = yaml.safe_load(manifest_text) or {}
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    return ReconcileContext(
        device_id=(manifest.get("device") or {}).get("id", "unknown"),
        manifest_commit=_sha256_hex(manifest_text),
        credentials=manifest.get("credentials", []) or [],
        catalog=catalog,
        opencode_config_dir=config_dir,
    )
```

- [ ] **Step 6: Add a rollback round-trip test**

Append to `tests/apply_gate_test.py`:

```python
def test_rollback_restores_bytes_and_mode(tmp_path):
    """Spec acceptance 5."""
    import os
    import stat

    from holdfastctl.apply import atomic_write
    from holdfastctl.backup import BackupManager

    target = tmp_path / "opencode.json"
    target.write_text('{"original": true}')
    os.chmod(target, 0o600)

    manager = BackupManager(backup_dir=tmp_path / "backups")
    backup = manager.create_backup(target)
    atomic_write(target, '{"modified": true}', allowed_prefixes=(tmp_path,))
    os.chmod(target, 0o644)

    manager.restore_from_backup(target, backup)
    os.chmod(target, 0o600)

    assert target.read_text() == '{"original": true}'
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
```

- [ ] **Step 7: Run tests and gates**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check && .venv/bin/mypy -p holdfastctl -p server`
Expected: no failures, ruff clean, mypy success.

- [ ] **Step 8: Commit**

```bash
git add src/holdfastctl/cli.py tests/apply_gate_test.py
git commit -m "Add approve, apply, and rollback; close the reconciliation loop

apply re-probes local state, recomputes the fingerprint, and refuses
unless the plan is approved, unexpired, and bound to both the current
state hash and the current manifest commit. Each refusal has its own
message. Actions are re-derived locally -- the control plane supplies only
the fingerprint it approved, never instructions.

Every write is backed up first and recorded in a per-plan manifest;
failure mid-apply restores what was already written. rollback replays that
manifest, restoring bytes and mode, and deletes files that did not exist
before the apply.

approve is an operator command taking HOLDFAST_ADMIN_TOKEN from the
environment, so the admin credential never lives on a managed device."
```

---

### Task 9: End-to-end pilot on jarod7736-laptop

Manual verification. No new code — this is the acceptance gate for Tasks 1–8.

**Files:**
- Create: `docs/phase2-pilot-2026-08-08.md` (evidence log)

- [ ] **Step 1: Snapshot the pilot config**

```bash
ssh 100.77.222.27 'cp ~/.config/opencode/opencode.json ~/.config/opencode/opencode.json.pre-phase2 && sha256sum ~/.config/opencode/opencode.json'
```

Record the digest in the evidence log.

- [ ] **Step 2: Dry run**

```bash
ssh 100.77.222.27 'holdfastctl plan --local -m manifests/devices/jarod7736-laptop.yaml'
ssh 100.77.222.27 'holdfastctl plan -m manifests/devices/jarod7736-laptop.yaml'
```

Expected: actions printed, then a plan id. Record the id.

- [ ] **Step 3: Confirm apply refuses before approval**

```bash
ssh 100.77.222.27 'holdfastctl apply <plan-id>'
```

Expected: exit 1, `refusing to apply. Plan is not approved (status: pending)`.

- [ ] **Step 4: Approve from the operator machine**

```bash
HOLDFAST_ADMIN_TOKEN=... holdfastctl approve <plan-id> --device jarod7736-laptop --control-plane https://holdfast.tail1c66ec.ts.net
```

- [ ] **Step 5: Confirm approval is invalidated by drift**

Edit `~/.config/opencode/opencode.json` on the pilot (add a whitespace-only change), then:

```bash
ssh 100.77.222.27 'holdfastctl apply <plan-id>'
```

Expected: exit 1, `Local state has changed since approval`. Revert the edit afterwards.

- [ ] **Step 6: Apply, then verify unmanaged keys survived**

```bash
ssh 100.77.222.27 'holdfastctl apply <plan-id> --dry-run'
ssh 100.77.222.27 'holdfastctl apply <plan-id>'
ssh 100.77.222.27 'python3 -c "import json;d=json.load(open(\"$HOME/.config/opencode/opencode.json\"));print(sorted(d))"'
```

Expected: `plugin`, `agent`, `lsp`, `permission`, `mcp`, `$schema` all still present.

- [ ] **Step 7: Restart OpenCode and verify providers reachable**

```bash
ssh 100.77.222.27 'curl -s -o /dev/null -w "%{http_code}\n" http://amd-halo.holdfast.lan:13305/api/v1/models'
```

Expected: `401` (reachable, needs the key OpenCode supplies).

- [ ] **Step 8: Roll back and reapply**

```bash
ssh 100.77.222.27 'holdfastctl rollback <plan-id> && sha256sum ~/.config/opencode/opencode.json'
```

Expected: digest matches the Step 1 snapshot exactly.

- [ ] **Step 9: Write the evidence log and commit**

Record every command, its output, and the two digests in `docs/phase2-pilot-2026-08-08.md`, following the style of `docs/fleet-audit-2026-08-07.md`.

```bash
git add docs/phase2-pilot-2026-08-08.md
git commit -m "Record Phase 2 pilot evidence for jarod7736-laptop

Dry run, refusal before approval, refusal after drift, apply, unmanaged-key
survival, rollback digest match, and reapply."
```

---

### Task 10: Correct the status documents

Three documents assert Phase 2 is complete. Two are near-duplicates.

**Files:**
- Modify: `IMPLEMENTATION_STATUS.md`
- Delete: `IMPLEMENTATION_SUMMARY.md` (consolidated into `PHASE2_IMPLEMENTATION_SUMMARY.md`)
- Modify: `PHASE2_IMPLEMENTATION_SUMMARY.md`
- Modify: `README.md` (command list)
- Modify: `TODO.md` (remove anything Phase 2 delivered)

- [ ] **Step 1: Get the real test count**

Run: `.venv/bin/python -m pytest tests/ -q | tail -1`
Record the number; all three documents currently cite a stale 113.

- [ ] **Step 2: Rewrite the status claims**

In `PHASE2_IMPLEMENTATION_SUMMARY.md`, replace "All modules have been implemented according to the specification" and the "ready for use" claim with an accurate statement: which commands exist, that the loop closes, and what is deferred to Phase 2.5 (`models[].limit` fidelity, the `repair` action, amd-halo and jarod-desktop manifests, profiles loader, `llm_serving`).

In `IMPLEMENTATION_STATUS.md`, change "Status: Implementation Complete (Gates Green)" to reflect Phase 2 completion specifically, and update the test count.

Delete `IMPLEMENTATION_SUMMARY.md` — it duplicates `PHASE2_IMPLEMENTATION_SUMMARY.md`.

- [ ] **Step 3: Update the README command list**

Document the nine commands, marking `approve` as the operator-only one.

- [ ] **Step 4: Prune TODO.md**

Remove any item Phase 2 delivered. Leave the token revocation endpoint, the stale `install-agent.sh` default, and the `pip3` item — none are in scope.

- [ ] **Step 5: Commit**

```bash
git add IMPLEMENTATION_STATUS.md PHASE2_IMPLEMENTATION_SUMMARY.md README.md TODO.md
git rm IMPLEMENTATION_SUMMARY.md
git commit -m "Correct status docs to describe actual Phase 2 state

Three documents claimed Phase 2 was complete while apply.py was unreachable
and six of nine commands did not exist. They were true about modules
existing and passing unit tests, and false about the phase being done. All
three cited a stale test count of 113.

IMPLEMENTATION_SUMMARY.md duplicated PHASE2_IMPLEMENTATION_SUMMARY.md and
is removed. The remaining documents now state which commands exist, that
the loop closes, and what is deferred to Phase 2.5."
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| FR1 command surface (6 commands) | 7, 8 |
| FR2 atomic write primitive | 1 |
| FR3 backups and rollback | 2, 8 |
| FR4 capability-scoped appliers | 6 |
| FR5 device-token submit and read | 4, 5 |
| Failure semantics (4 refusal conditions) | 8 |
| Security requirements | 4, 8 |
| Acceptance 1 (stable hash) | 3 |
| Acceptance 2 (changed state invalidates) | 3, 8 |
| Acceptance 3 (traversal rejected) | 1 |
| Acceptance 4 (failed validation non-destructive) | 1 |
| Acceptance 5 (rollback restores bytes + mode) | 8 |
| Acceptance 6 (canary secrets) | covered by existing `tests/security/`; verify unchanged in Task 8 |
| Acceptance 7 (report token cannot approve) | 4 |
| Acceptance 8 (unmanaged keys survive) | 6 |
| Acceptance 9 (temp file on destination fs) | 1 |
| Acceptance 10 (cross-device scoping) | 4 |
| E2E pilot | 9 |
| Documentation correction | 10 |

**Type consistency:** `atomic_write(path, data, *, allowed_prefixes)` is used with the same signature in Tasks 1, 6, and 8. `create_backup` returns `Path | None` in Task 2 and is handled as nullable in Task 6. `_verify_plan_applicable` keyword-only params match between Tasks 8's helper and its tests. Manifest entry shape `{"target", "backup", "mode"}` is identical in Tasks 2, 6, and 8.

**Known gap accepted:** Acceptance 6 has no new test; the existing `tests/security/` suite covers secret redaction, and Task 8 re-runs it. If it turns out not to cover backups specifically, add a canary test there during Task 8.
