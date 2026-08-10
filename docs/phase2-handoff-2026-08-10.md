# Phase 2 Handoff — 2026-08-10

Status of the "loop closure" work (spec/plan: `docs/superpowers/{specs,plans}/2026-08-08-phase2-loop-closure*`).

**Bottom line:** the reconciliation loop is **closed and deployed**. A device can submit a plan, an operator can approve it, and the device can apply it (gated) and roll it back. Gates green (244 passed, ruff + mypy clean). `master` @ `b6854da`, deployed to the Synology control plane and live-verified.

---

## Done

### Loop closure (commit `b6854da`)
Master had the Task 8 gate + `approve`/`apply`/`rollback` **shells** but not the machinery under them. Ported the correct implementations from branch `phase2-loop-closure` and adapted to master:

- **C — `OpencodeAdapter.apply()`** (`src/holdfastctl/capabilities.py`): merges one plan action into `opencode.json`, preserving every unmanaged key (`plugin`/`agent`/`lsp`/`permission`/`$schema`).
- **D — robust `atomic_write`** (`src/holdfastctl/apply.py`): `validate_path_safety` + `os.replace` + `os.fsync` + prefix allowlist, replacing the cross-filesystem stub. The dead `AtomicApplier`/`ConfigurationApplier` classes were **kept** (behind their existing tests) — removal is deferred (see below).
- **E — `create_backup`→`None`** (`src/holdfastctl/backup.py`): a missing source no longer produces an empty backup, so rolling back a newly-created file deletes it instead of restoring an empty file.
- **A — plan submission**: `reporting.create_plan`, a `plan` CLI command, and `POST /api/v1/devices/{id}/plans` relaxed to admin-**or**-device token.
- **B — plan read**: `GET /api/v1/devices/{id}/plans/{id}` (admin-or-device); `get_plan(admin_token=...)` so `approve` works from an operator machine (which holds no device token).
- `apply` **skips advisory drift** (`repair`/`env` targets) instead of crashing on it.
- Tests added (+10): adapter merge preserves unmanaged keys; `atomic_write` mode/allowlist; `create_backup`→None; 5 plan-endpoint auth tests (`tests/plan_endpoints_test.py`).

### Infra / ops (this session)
- Finished the abandoned Phase 2 rebase onto `origin/master`; removed the stale worktrees; cleared the lint/type debt so master CI is green.
- Redeployed the control plane to the Synology (twice), DB backed up each time; tokens preserved. Reachable at `https://holdfast.tail1c66ec.ts.net`. Live-verified device submit → read → cross-device-403.
- Enrolled `amd-halo` (currently **scope-less** — no gateway key). Added `scripts/enroll-device.sh` (one-shot enroll with stdin prompts; handles the pre-existing-token case).
- Added 10 lemonade text models to the LiteLLM gateway config (`big`, `gpt-oss-120b`, `coder-next`, `coder-qwen`, `coder-qwopus`, `chat-flm`, `chat-magichand`, `qwen3.5-35b-a3b`, `qwen3.6-35b-a3b-mtp`, `vision-27b`). See memory `litellm-gateway`.
- Secret hygiene: removed a plaintext `LEMONADE_API_KEY` from `~/.bashrc`; moved its op-sourcing into the untracked `~/.zshrc.local`; committed dotfiles (`dotfiles_2026` @ `4952c3c`).

---

## Left to do (priority order)

1. **Task 9 — real end-to-end pilot.** apply/rollback are unit-tested but never run on a live device with a real manifest. Natural target is `jarod7736-laptop` (has a manifest; `amd-halo` does not). Verify: refuse-before-approval → approve → refuse-after-drift → apply → unmanaged keys survive → `rollback` digest matches the pre-apply snapshot. Record evidence like `docs/fleet-audit-2026-08-07.md`.
2. **Task 10 — correct status docs.** `IMPLEMENTATION_STATUS.md` still says "Task 8 in-progress" / 113 tests (actual 244); delete duplicate `IMPLEMENTATION_SUMMARY.md`; update `README.md` command list; `docs/operating-guide.md` still claims "there is no apply command."
3. **Standing `TODO.md` items.** Stale `http://127.0.0.1:8000` defaults in `src/holdfastctl/cli.py` (`report`, `status`, `apply`, and the `enroll-code --control-plane` default) — point at the tailnet URL. Add an admin token-revocation endpoint (`tokens.revoked` exists; no API to set it).
4. **Remove dead code + branch.** Delete `AtomicApplier`/`ConfigurationApplier` from `apply.py` and their tests (`tests/apply_test.py` class tests, `tests/phase2_test.py`, `tests/focused_phase2_verification.py`), update `__init__.py` exports; then delete branch `phase2-loop-closure` (its useful commits are ported).
5. **amd-halo gateway key.** Device enrolled scope-less. `gpt-oss-120b`/`coder` are now valid gateway ids; re-mint scoped via `scripts/enroll-device.sh` (answer "force fresh enrollment") and store the one-time key. Full plan/apply flow for amd-halo also needs an amd-halo manifest (Phase 2.5).
6. **Fix git-agent GitHub signing** on amd-halo — normal `git push`/`rsync` currently need `GIT_SSH_COMMAND="ssh -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519"`.

### Deferred (Phase 2.5)
`models[].limit` fidelity, the `repair` action, amd-halo & jarod-desktop device manifests, the profiles loader, `llm_serving`. Optional LiteLLM additions: uncensored/RP aliases, `embed-nomic`, image/audio models (need per-model `mode:` config).

### Loose ends
- One harmless `pending` smoke-test plan left on `amd-halo` (expires ~1h; no delete endpoint — see the revocation TODO).
- Two OpenRouter keys in `~/.zshrc.local` are still plaintext literals (only one OpenRouter item exists in 1Password; ambiguous mapping).

---

## Verification quick-ref
```
.venv/bin/python -m pytest -q          # 244 passed, 5 skipped
.venv/bin/ruff check                   # clean
.venv/bin/mypy -p holdfastctl -p server  # clean
curl -s -o /dev/null -w '%{http_code}\n' https://holdfast.tail1c66ec.ts.net/openapi.json  # 200
```
NAS deploy + LiteLLM specifics: memories `nas-deploy-access`, `litellm-gateway`.
